"""Diagnostics: the full raw SmartThings status, for mapping new capabilities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_TOKEN, CONF_VIEW_INSIDE_TOKEN
from .models import FrostboxConfigEntry

REDACT = {CONF_TOKEN, CONF_VIEW_INSIDE_TOKEN, "locationId", "ownerId", "serialNumber", "macAddress"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FrostboxConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    now = dt_util.utcnow()
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), REDACT),
            "options": async_redact_data(dict(entry.options), REDACT),
        },
        "device": async_redact_data(coordinator.device, REDACT),
        "status": coordinator.data.raw if coordinator.data else None,
        "online": coordinator.online,
        "doors": coordinator.doors.as_dict(),
        "food_safety": {"state": coordinator.safety.state(now), **coordinator.safety.as_dict()},
        "modes": coordinator.modes.as_dict(),
        "virtual_switches": list(coordinator.virtual_switches),
        "inventory_items": len(entry.runtime_data.inventory.items()),
    }
