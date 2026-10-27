"""Validate the shipped blueprints, packages and HomeKit config against HA's schemas."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA, async_validate_config_item
from homeassistant.components.blueprint import models
from homeassistant.components.homekit import CONFIG_SCHEMA as HOMEKIT_SCHEMA
from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml
import pytest

ROOT = Path(__file__).parent.parent / "homeassistant"
BLUEPRINTS = sorted((ROOT / "blueprints/automation/frostbox").glob("*.yaml"))


def _swap_device_actions(node):
    """Phone notifications need a real mobile_app device; validate them as notify calls instead."""
    if isinstance(node, dict):
        if node.get("domain") == "mobile_app" and node.get("type") == "notify":
            return {"action": "notify.notify", "data": {k: node[k] for k in ("title", "message", "data") if k in node}}
        return {k: _swap_device_actions(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_swap_device_actions(v) for v in node]
    return node


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.name)
def test_blueprint_schema(path: Path) -> None:
    bp = models.Blueprint(load_yaml(path), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)
    assert bp.name.startswith("Frostbox")


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.name)
async def test_blueprint_substitutes(hass: HomeAssistant, path: Path) -> None:
    """Fill required inputs with dummy values and validate the resulting automation."""
    bp = models.Blueprint(load_yaml(path), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)
    inputs = {}
    for key, spec in bp.inputs.items():
        if spec and "default" in spec:
            continue
        sel = next(iter((spec or {}).get("selector", {"text": None})))
        inputs[key] = {
            "device": "abc123",
            "entity": "calendar.family"
            if "calendar" in key
            else ("zone.store" if "zone" in key else "person.me" if key == "people" else "input_boolean.away"),
            "text": "x",
        }.get(sel, "x")
        if key == "people":
            inputs[key] = ["person.me"]
    inst = models.BlueprintInputs(bp, {"use_blueprint": {"path": path.name, "input": inputs}})
    inst.validate()
    config = _swap_device_actions(inst.async_substitute())
    await async_setup_component(hass, "conversation", {})
    result = await async_validate_config_item(
        hass, path.stem, {"id": path.stem, **{k: v for k, v in config.items() if k != "blueprint"}}
    )
    assert result is not None and result.validation_error is None, result and result.validation_error


async def test_assist_package(hass: HomeAssistant) -> None:
    await async_setup_component(hass, "conversation", {})
    pkg = load_yaml(ROOT / "packages/frostbox_assist.yaml")
    for item in pkg["automation"]:
        result = await async_validate_config_item(hass, item["id"], item)
        assert result is not None and result.validation_error is None, item["id"]


def test_homekit_package() -> None:
    pkg = load_yaml(ROOT / "packages/frostbox_homekit.yaml")
    HOMEKIT_SCHEMA(pkg)


def test_dashboard_parses() -> None:
    dash = load_yaml(ROOT / "dashboards/frostbox_fridge.yaml")
    assert dash["views"][0]["type"] == "sections"


async def test_assist_end_to_end(hass: HomeAssistant, setup_fridge) -> None:
    """Spoken questions reach Frostbox and come back as answers."""
    from homeassistant.components import conversation

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    pkg = load_yaml(ROOT / "packages/frostbox_assist.yaml")
    assert await async_setup_component(hass, "automation", {"automation": pkg["automation"]})
    await hass.async_block_till_done()

    async def ask(text: str) -> str:
        result = await conversation.async_converse(hass, text, None, Context())
        return result.response.speech["plain"]["speech"]

    assert "Added milk to the fridge, good for 7 more days" in await ask("add milk to the fridge")
    assert (await ask("do we have any milk")).startswith("Yes, you have 1 milk")
    assert "No, I don't see any pickles" in await ask("is there any pickles in the fridge")
    assert "Marked milk as finished" in await ask("we're out of milk")
    assert "Saved Leftover chili" in await ask("save leftover chili")
    assert "Nothing is about to expire" in await ask("what's expiring")  # leftovers last 4 days
    await hass.services.async_call("frostbox", "add_leftover", {"name": "rice", "days": 1}, blocking=True)
    assert "Use these first: Leftover rice" in await ask("what should I eat first")
    assert "fridge is 37 degrees" in await ask("is the fridge ok")
    assert "Party mode is on" in await ask("start party mode")
