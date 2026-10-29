"""Compact, paste-friendly summary of the fridge's unmapped capabilities.

``frostbox.dump_capabilities`` writes the full JSON to /config, but that file
is awkward to get off Home Assistant OS. This summary goes in the action's
on-screen response instead: current values plus each command and its
arguments, which is everything needed to map a capability to entities.
"""

from __future__ import annotations

import json
from typing import Any

from .status import FridgeStatus

# Already mapped to entities, or plumbing that never becomes an entity.
SKIP = {
    "contactSensor",
    "temperatureMeasurement",
    "thermostatCoolingSetpoint",
    "custom.thermostatSetpointControl",
    "custom.waterFilter",
    "custom.deodorFilter",
    "custom.fridgeMode",
    "powerConsumptionReport",
    "refrigeration",
    "switch",
    "samsungce.powerCool",
    "samsungce.powerFreeze",
    "custom.disabledCapabilities",
    "custom.disabledComponents",
    "samsungce.unavailableCapabilities",
    "custom.deviceReportStateConfiguration",
    "execute",
    "refresh",
    "samsungce.refresh",
    "ocf",
    "logTrigger",
    "sec.diagnosticsInformation",
    "sec.wifiConfiguration",
    "samsungce.wifiConnectionInfo",
    "samsungce.deviceIdentification",
    "samsungce.driverVersion",
    "samsungce.driverState",
    "samsungce.softwareVersion",
    "samsungce.timeZone",
    "samsungce.fileTransfer",
    "samsungce.sendContents",
    "samsungce.runestoneHomeContext",
    "samsungce.deviceApplicationsAndSettings",
}


def _short(value: Any, limit: int = 120) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, list) and len(value) > 6:
        value = [*value[:6], f"... {len(value) - 6} more"]
    text = value if isinstance(value, str) else json.dumps(value, default=str, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _schema_text(schema: dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        return "?"
    if "enum" in schema:
        return "|".join(str(v) for v in schema["enum"])
    kind = schema.get("type", "?")
    bounds = [f"{k}={schema[k]}" for k in ("minimum", "maximum", "maxLength") if k in schema]
    if kind == "array" and isinstance(schema.get("items"), dict):
        return f"list of {_schema_text(schema['items'])}"
    return f"{kind}({', '.join(bounds)})" if bounds else str(kind)


def _commands(definition: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, cmd in (definition.get("commands") or {}).items():
        args = [
            f"{a.get('name', '?')}{'?' if a.get('optional') else ''}: {_schema_text(a.get('schema') or {})}"
            for a in (cmd or {}).get("arguments") or []
        ]
        out[name] = ", ".join(args) or "(no arguments)"
    return out


def summarize(status: FridgeStatus, definitions: dict[str, Any]) -> dict[str, Any]:
    """``{capability: {"values": {component: {attr: value}}, "commands": {...}}}``."""
    summary: dict[str, Any] = {}
    for component, caps in status.raw.items():
        if not status.component_enabled(component):
            continue
        disabled = status.disabled_capabilities(component)
        for capability, attrs in (caps or {}).items():
            if capability in SKIP or capability in disabled:
                continue
            entry = summary.setdefault(capability, {"values": {}})
            entry["values"][component] = {
                attr: _short((data or {}).get("value")) for attr, data in (attrs or {}).items()
            }
    for capability, entry in summary.items():
        definition = definitions.get(capability) or {}
        if "error" in definition:
            entry["commands"] = f"definition unavailable ({definition['error'][:60]})"
        elif cmds := _commands(definition):
            entry["commands"] = cmds
    return dict(sorted(summary.items()))
