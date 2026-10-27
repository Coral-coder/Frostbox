"""Shared fixtures: a fake SmartThings API backed by JSON fixtures."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.frostbox.const import (
    AUTH_PAT,
    CONF_AUTH_TYPE,
    CONF_DEVICE_ID,
    CONF_LOCATION_ID,
    CONF_TOKEN,
    DOMAIN,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeSmartThings:
    """Stands in for SmartThingsApi; records commands and mutates status."""

    def __init__(self) -> None:
        self.device = json.loads((FIXTURES / "device.json").read_text())
        self.status = json.loads((FIXTURES / "status.json").read_text())["components"]
        self.health = "ONLINE"
        self.commands: list[tuple[str, str, str, str, list[Any] | None]] = []
        self.virtual: dict[str, dict[str, Any]] = {}

    def set(self, component: str, capability: str, attribute: str, value: Any) -> None:
        self.status[component][capability][attribute]["value"] = value

    async def list_devices(self):
        return [self.device] + [{"deviceId": k, "label": v["label"], "components": []} for k, v in self.virtual.items()]

    async def get_device(self, device_id):
        return self.device

    async def get_status(self, device_id):
        if device_id in self.virtual:
            return {"main": {"switch": {"switch": {"value": self.virtual[device_id]["switch"]}}}}
        return copy.deepcopy(self.status)

    async def get_health(self, device_id):
        return self.health

    async def send_command(self, device_id, component, capability, command, arguments=None):
        self.commands.append((device_id, component, capability, command, arguments))
        if device_id in self.virtual:
            self.virtual[device_id]["switch"] = command
            return
        if capability.startswith("samsungce.power"):
            self.set(component, capability, "activated", command == "activate")
        elif capability == "custom.fridgeMode":
            self.set(component, capability, "fridgeMode", arguments[0])
        elif capability == "thermostatCoolingSetpoint":
            self.set(component, capability, "coolingSetpoint", arguments[0])
        elif capability == "switch":
            self.set(component, "switch", "switch", command)

    async def create_virtual_switch(self, name, location_id):
        dev_id = f"vs-{len(self.virtual)}"
        self.virtual[dev_id] = {"label": name, "switch": "off"}
        return {"deviceId": dev_id}

    async def fetch_binary(self, url, token=None):
        return b"\xff\xd8fake-jpeg\xff\xd9"


@pytest.fixture
def fake_api() -> FakeSmartThings:
    return FakeSmartThings()


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Family Hub",
        unique_id="fridge-1234",
        data={CONF_AUTH_TYPE: AUTH_PAT, CONF_TOKEN: "pat", CONF_DEVICE_ID: "fridge-1234", CONF_LOCATION_ID: "loc-1"},
        options={},
    )


@pytest.fixture
async def setup_fridge(hass, fake_api, mock_entry):
    """Set up Frostbox against the fake API."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    with patch("custom_components.frostbox.build_api", return_value=fake_api):
        mock_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        yield mock_entry
