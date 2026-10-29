"""Frostbox actions (services).

Every action that answers a question returns a response with a ready-made
``speech`` string, so Assist, Siri Shortcuts and automations can just read
it out.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import SmartThingsError
from .const import (
    COMPONENT_FREEZER,
    COMPONENT_FRIDGE,
    COMPONENT_MAIN,
    CONF_EXPIRY_WARNING_DAYS,
    CONF_RECIPE_AGENT,
    CONF_SHOPPING_LIST,
    DEFAULT_EXPIRY_WARNING_DAYS,
    DEFAULT_SHOPPING_LIST,
    DOMAIN,
    EVENT_INVENTORY,
    LOCATIONS,
    MODES,
)
from .lookups import lookup_barcode, recipes_for
from .shelf_life import BY_KEY
from .status import FridgeStatus

_LOGGER = logging.getLogger(__name__)

ATTR_ENTRY = "config_entry_id"
ENTRY_FIELD = {vol.Optional(ATTR_ENTRY): cv.string}

ADD_SCHEMA = vol.Schema(
    {
        **ENTRY_FIELD,
        vol.Optional("name"): cv.string,
        vol.Optional("barcode"): cv.string,
        vol.Optional("quantity", default=1): vol.Coerce(float),
        vol.Optional("unit"): cv.string,
        vol.Optional("location", default="fridge"): vol.In(LOCATIONS),
        vol.Optional("category"): vol.In(list(BY_KEY)),
        vol.Optional("expires"): cv.date,
        vol.Optional("days"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3650)),
        vol.Optional("staple", default=False): cv.boolean,
        vol.Optional("min_quantity", default=0): vol.Coerce(float),
        vol.Optional("notes"): cv.string,
    }
)
LEFTOVER_SCHEMA = vol.Schema(
    {
        **ENTRY_FIELD,
        vol.Required("name"): cv.string,
        vol.Optional("days", default=4): vol.All(vol.Coerce(int), vol.Range(min=0, max=30)),
        vol.Optional("location", default="fridge"): vol.In(LOCATIONS),
    }
)
CONSUME_SCHEMA = vol.Schema(
    {
        **ENTRY_FIELD,
        vol.Required("name"): cv.string,
        vol.Optional("quantity", default=1): vol.Coerce(float),
        vol.Optional("all", default=False): cv.boolean,
    }
)
REMOVE_SCHEMA = vol.Schema({**ENTRY_FIELD, vol.Required("name"): cv.string})
FIND_SCHEMA = vol.Schema({**ENTRY_FIELD, vol.Required("query"): cv.string})
STATUS_SCHEMA = vol.Schema(ENTRY_FIELD)
RECIPE_SCHEMA = vol.Schema({**ENTRY_FIELD, vol.Optional("limit", default=5): vol.All(int, vol.Range(min=1, max=20))})
MODE_SCHEMA = vol.Schema(
    {
        **ENTRY_FIELD,
        vol.Required("mode"): vol.In(MODES),
        vol.Optional("on", default=True): cv.boolean,
        vol.Optional("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=24 * 30)),
    }
)
COMMAND_SCHEMA = vol.Schema(
    {
        **ENTRY_FIELD,
        vol.Optional("component", default=COMPONENT_MAIN): cv.string,
        vol.Required("capability"): cv.string,
        vol.Required("command"): cv.string,
        vol.Optional("arguments", default=[]): list,
    }
)


def _entry(hass: HomeAssistant, call: ServiceCall):
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    wanted = call.data.get(ATTR_ENTRY)
    if wanted:
        entries = [e for e in entries if e.entry_id == wanted]
    if not entries:
        raise ServiceValidationError("No loaded Frostbox fridge found")
    return entries[0]


def _join(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _days_phrase(days: int | None) -> str:
    if days is None:
        return ""
    if days < 0:
        return "expired"
    if days == 0:
        return "expires today"
    if days == 1:
        return "expires tomorrow"
    return f"good for {days} more days"


async def async_after_consume(hass: HomeAssistant, coordinator, result) -> str | None:
    """Put low staples on the shopping list. Returns a note for speech."""
    if not result.now_low:
        return None
    target = coordinator.config_entry.options.get(CONF_SHOPPING_LIST, DEFAULT_SHOPPING_LIST)
    if target and hass.states.get(target):
        await hass.services.async_call(
            "todo", "add_item", {"entity_id": target, "item": result.item.name}, blocking=True
        )
        note = f"I added {result.item.name} to your shopping list."
    else:
        note = f"You're running low on {result.item.name}."
    hass.bus.async_fire(EVENT_INVENTORY, {"action": "low", "name": result.item.name})
    return note


async def async_setup_services(hass: HomeAssistant) -> None:
    async def add_item(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        inv = entry.runtime_data.inventory
        data = dict(call.data)
        name = data.get("name")
        brand = None
        tags: list[str] = []
        known = True
        if data.get("barcode"):
            product = await lookup_barcode(hass, data["barcode"])
            if product:
                name = name or product["name"]
                brand = product["brand"]
                tags = product["category_tags"]
            elif not name:
                known = False
                name = f"Item {data['barcode']}"
        if not name:
            raise ServiceValidationError("Give a name or a barcode")
        item = inv.add(
            name,
            quantity=data["quantity"],
            unit=data.get("unit"),
            location=data["location"],
            category=data.get("category"),
            category_tags=tags,
            expires=data.get("expires"),
            days=data.get("days"),
            barcode=data.get("barcode"),
            brand=brand,
            staple=data["staple"],
            min_quantity=data["min_quantity"],
            notes=data.get("notes"),
        )
        hass.bus.async_fire(EVENT_INVENTORY, {"action": "add", "name": item.name})
        days = item.days_left(inv.today)
        speech = f"Added {item.name} to the {item.location}, {_days_phrase(days)}."
        if not known:
            speech = f"I didn't recognize that barcode, so I saved it as {item.name}. Rename it in the inventory."
        return {"item": item.as_public(inv.today), "recognized": known, "speech": speech}

    async def add_leftover(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        inv = entry.runtime_data.inventory
        item = inv.add(
            f"Leftover {call.data['name']}",
            leftover=True,
            days=call.data["days"],
            location=call.data["location"],
        )
        hass.bus.async_fire(EVENT_INVENTORY, {"action": "add", "name": item.name})
        return {
            "item": item.as_public(inv.today),
            "speech": f"Saved {item.name}. Eat it within {call.data['days']} days.",
        }

    async def consume_item(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        inv = entry.runtime_data.inventory
        matches = inv.find(call.data["name"])
        if not matches:
            return {"found": False, "speech": f"I couldn't find {call.data['name']} in the fridge."}
        # Use the one that expires first.
        result = inv.consume(matches[0], None if call.data["all"] else call.data["quantity"])
        note = await async_after_consume(hass, entry.runtime_data.coordinator, result)
        hass.bus.async_fire(EVENT_INVENTORY, {"action": "consume", "name": result.item.name})
        speech = (
            f"Marked {result.item.name} as finished."
            if result.remaining <= 0
            else f"OK, {result.remaining:g} {result.item.name} left."
        )
        if note:
            speech += f" {note}"
        return {"found": True, "remaining": result.remaining, "speech": speech}

    async def remove_item(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        inv = entry.runtime_data.inventory
        matches = inv.find(call.data["name"])
        for item in matches:
            inv.remove(item.id)
        return {
            "removed": [i.name for i in matches],
            "speech": f"Removed {_join([i.name for i in matches]) or 'nothing'}.",
        }

    async def find_item(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        inv = entry.runtime_data.inventory
        query = call.data["query"]
        matches = [i for i in inv.find(query) if i.quantity > 0]
        today = inv.today
        if not matches:
            speech = f"No, I don't see any {query} in the fridge."
        else:
            first = matches[0]
            qty = sum(i.quantity for i in matches)
            speech = f"Yes, you have {qty:g} {first.name} in the {first.location}"
            if phrase := _days_phrase(first.days_left(today)):
                speech += f", {phrase}"
            speech += "."
        return {"found": bool(matches), "items": [i.as_public(today) for i in matches], "speech": speech}

    async def get_status(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        coord = entry.runtime_data.coordinator
        inv = entry.runtime_data.inventory
        st = coord.data
        now = dt_util.utcnow()
        warn_days = entry.options.get(CONF_EXPIRY_WARNING_DAYS, DEFAULT_EXPIRY_WARNING_DAYS)
        fridge = st.temperature_f(COMPONENT_FRIDGE)
        if fridge is None:
            fridge = st.temperature_f(COMPONENT_MAIN)
        freezer = st.temperature_f(COMPONENT_FREEZER)
        expiring = inv.expiring(warn_days)
        expired = inv.expired()
        open_doors = [c for c in st.door_components() if st.door_open(c)]
        safety = coord.safety.state(now)
        filt = None
        for comp in st.components_with("custom.waterFilter", "waterFilterUsage"):
            filt = st.value(comp, "custom.waterFilter", "waterFilterUsage")

        parts = []
        if fridge is not None:
            parts.append(f"The fridge is {fridge:.0f} degrees")
        if freezer is not None:
            parts.append(f"the freezer is {freezer:.0f}")
        speech = (" and ".join(parts) + ". ") if parts else ""
        speech += "A door is open. " if open_doors else ""
        if safety not in ("ok", "unknown"):
            speech += coord.safety.advice(now) + " "
        if expiring:
            speech += f"Use soon: {_join([i.name for i in expiring[:5]])}. "
        if expired:
            speech += f"Expired: {_join([i.name for i in expired[:5]])}. "
        if not (expiring or expired):
            speech += "Nothing is about to expire. "
        if isinstance(filt, (int, float)) and filt >= 90:
            speech += "The water filter needs replacing. "

        return {
            "fridge_f": fridge,
            "freezer_f": freezer,
            "open_doors": open_doors,
            "door_openings_today": coord.doors.opens_today,
            "food_safety": safety,
            "food_safety_advice": coord.safety.advice(now),
            "inventory_count": sum(1 for i in inv.items() if i.quantity > 0),
            "expiring": [i.as_public(inv.today) for i in expiring],
            "expired": [i.as_public(inv.today) for i in expired],
            "low_staples": [i.name for i in inv.low_staples()],
            "water_filter_used": filt,
            "modes": {m: coord.modes.is_on(m) for m in MODES},
            "speech": speech.strip(),
        }

    async def suggest_recipes(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        inv = entry.runtime_data.inventory
        items = [i for i in inv.items() if i.quantity > 0 and i.category not in ("condiments", "beverages")]
        if not items:
            return {"recipes": [], "speech": "Your inventory is empty, so I can't suggest anything yet."}
        names = [i.name for i in items]
        agent = entry.options.get(CONF_RECIPE_AGENT)
        if agent:
            prompt = (
                "Suggest three dinner ideas using mostly these fridge items, most urgent first: "
                f"{', '.join(names[:25])}. Keep each to one sentence."
            )
            result = await hass.services.async_call(
                "conversation", "process", {"text": prompt, "agent_id": agent}, blocking=True, return_response=True
            )
            speech = (((result or {}).get("response") or {}).get("speech") or {}).get("plain", {}).get("speech")
            if speech:
                return {"recipes": [], "speech": speech, "source": agent}
        recipes = await recipes_for(hass, names, call.data["limit"])
        if not recipes:
            return {"recipes": [], "speech": f"I couldn't find recipes. Maybe something with {_join(names[:3])}?"}
        speech = f"How about {recipes[0]['name']}"
        if len(recipes) > 1:
            speech += f" or {recipes[1]['name']}"
        speech += f"? It uses your {_join(recipes[0]['uses'][:3])}."
        return {"recipes": recipes, "speech": speech, "source": "themealdb"}

    async def activate_mode(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        coord = entry.runtime_data.coordinator
        await coord.modes.async_set(call.data["mode"], call.data["on"], hours=call.data.get("hours"))
        until = coord.modes.until(call.data["mode"])
        state = "on" if call.data["on"] else "off"
        speech = f"{call.data['mode'].title()} mode is {state}"
        if until and call.data["on"]:
            speech += f" until {dt_util.as_local(until).strftime('%-I:%M %p')}"
        return {"speech": speech + "."}

    async def send_command(call: ServiceCall) -> None:
        entry = _entry(hass, call)
        await entry.runtime_data.coordinator.async_command(
            call.data["component"], call.data["capability"], call.data["command"], call.data["arguments"] or None
        )

    async def dump_capabilities(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        coord = entry.runtime_data.coordinator
        device = await coord.api.get_device(coord.device_id)
        status = await coord.api.get_status(coord.device_id)
        versions: dict[str, int] = {}
        for comp in device.get("components", []):
            for cap in comp.get("capabilities", []):
                versions[cap["id"]] = cap.get("version", 1)

        # Fetch each capability's definition so new controls can be built without guessing.
        sem = asyncio.Semaphore(4)
        definitions: dict[str, Any] = {}

        async def _definition(cap_id: str, version: int) -> None:
            async with sem:
                try:
                    definitions[cap_id] = await coord.api.get_capability(cap_id, version)
                except SmartThingsError as err:
                    definitions[cap_id] = {"error": str(err)}

        await asyncio.gather(*(_definition(c, v) for c, v in versions.items()))
        parsed = FridgeStatus(status)
        dump = {
            "device": device,
            "status": status,
            "disabled_components": sorted(parsed.disabled_components()),
            "disabled_capabilities": {c: sorted(parsed.disabled_capabilities(c)) for c in status},
            "definitions": definitions,
        }
        path = Path(hass.config.path("frostbox_capabilities.json"))
        await hass.async_add_executor_job(path.write_text, json.dumps(dump, indent=2, default=str))
        return {
            "path": str(path),
            "components": list(status),
            "disabled_components": dump["disabled_components"],
            "capabilities": sorted(versions),
            "definitions_fetched": sum(1 for d in definitions.values() if "error" not in d),
        }

    reg = hass.services.async_register
    reg(DOMAIN, "add_item", add_item, ADD_SCHEMA, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "add_leftover", add_leftover, LEFTOVER_SCHEMA, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "consume_item", consume_item, CONSUME_SCHEMA, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "remove_item", remove_item, REMOVE_SCHEMA, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "find_item", find_item, FIND_SCHEMA, SupportsResponse.ONLY)
    reg(DOMAIN, "get_status", get_status, STATUS_SCHEMA, SupportsResponse.ONLY)
    reg(DOMAIN, "suggest_recipes", suggest_recipes, RECIPE_SCHEMA, SupportsResponse.ONLY)
    reg(DOMAIN, "activate_mode", activate_mode, MODE_SCHEMA, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "send_command", send_command, COMMAND_SCHEMA)
    reg(DOMAIN, "dump_capabilities", dump_capabilities, STATUS_SCHEMA, SupportsResponse.OPTIONAL)
