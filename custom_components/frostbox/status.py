"""Helpers for reading the raw SmartThings device status."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .const import COMPONENT_NAMES


def to_fahrenheit(value: float, unit: str | None) -> float:
    if unit and unit.upper().startswith("C"):
        return value * 9 / 5 + 32
    return value


def from_fahrenheit(value_f: float, unit: str | None) -> float:
    if unit and unit.upper().startswith("C"):
        return (value_f - 32) * 5 / 9
    return value_f


def component_label(component: str) -> str:
    if component in COMPONENT_NAMES:
        return COMPONENT_NAMES[component]
    return component.replace("-", " ").replace("_", " ").title()


@dataclass(frozen=True)
class AttributeRef:
    component: str
    capability: str
    attribute: str


class FridgeStatus:
    """Read-only view over ``GET /devices/{id}/status`` components."""

    def __init__(self, components: dict[str, Any] | None = None) -> None:
        self.raw: dict[str, Any] = components or {}

    def has(self, component: str, capability: str, attribute: str | None = None) -> bool:
        cap = self.raw.get(component, {}).get(capability)
        if cap is None:
            return False
        if attribute is None:
            return True
        return attribute in cap

    def disabled_components(self) -> set[str]:
        """Components Samsung ships but switches off on this model (e.g. regional features)."""
        value = self.value("main", "custom.disabledComponents", "disabledComponents")
        return {str(v) for v in value} if isinstance(value, list) else set()

    def disabled_capabilities(self, component: str) -> set[str]:
        disabled: set[str] = set()
        value = self.value(component, "custom.disabledCapabilities", "disabledCapabilities")
        if isinstance(value, list):
            disabled.update(str(v) for v in value)
        # samsungce.unavailableCapabilities: lists of capability ids (or dicts naming them).
        for attrs in (self.raw.get(component, {}).get("samsungce.unavailableCapabilities") or {}).values():
            items = (attrs or {}).get("value")
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str):
                    disabled.add(item)
                elif isinstance(item, dict) and isinstance(item.get("capability"), str):
                    disabled.add(item["capability"])
        return disabled

    def component_enabled(self, component: str) -> bool:
        return component in self.raw and component not in self.disabled_components()

    def supports(self, component: str, capability: str, attribute: str | None = None) -> bool:
        return (
            self.component_enabled(component)
            and self.has(component, capability, attribute)
            and capability not in self.disabled_capabilities(component)
        )

    def _attr(self, component: str, capability: str, attribute: str) -> dict[str, Any]:
        return self.raw.get(component, {}).get(capability, {}).get(attribute) or {}

    def value(self, component: str, capability: str, attribute: str) -> Any:
        return self._attr(component, capability, attribute).get("value")

    def unit(self, component: str, capability: str, attribute: str) -> str | None:
        return self._attr(component, capability, attribute).get("unit")

    def timestamp(self, component: str, capability: str, attribute: str) -> str | None:
        return self._attr(component, capability, attribute).get("timestamp")

    def components_with(self, capability: str, attribute: str | None = None) -> list[str]:
        return [c for c in self.raw if self.supports(c, capability, attribute)]

    def iter_attributes(self) -> Iterator[tuple[AttributeRef, Any]]:
        """Every attribute of enabled components and capabilities."""
        for component, caps in self.raw.items():
            if not self.component_enabled(component):
                continue
            disabled = self.disabled_capabilities(component)
            for capability, attrs in (caps or {}).items():
                if capability in disabled:
                    continue
                for attribute, data in (attrs or {}).items():
                    yield AttributeRef(component, capability, attribute), (data or {}).get("value")

    # --- convenience -----------------------------------------------------

    def temperature_f(self, component: str) -> float | None:
        value = self.value(component, "temperatureMeasurement", "temperature")
        if not isinstance(value, (int, float)):
            return None
        return to_fahrenheit(float(value), self.unit(component, "temperatureMeasurement", "temperature"))

    def door_open(self, component: str) -> bool | None:
        value = self.value(component, "contactSensor", "contact")
        if value is None:
            return None
        return value == "open"

    def door_components(self) -> list[str]:
        """Door contacts; ``main`` only counts when no per-door contact exists."""
        doors = self.components_with("contactSensor", "contact")
        specific = [c for c in doors if c != "main"]
        return specific or doors

    def any_door_open(self) -> bool | None:
        states = [self.door_open(c) for c in self.door_components()]
        known = [s for s in states if s is not None]
        if not known:
            return None
        return any(known)

    def view_inside_contents(self) -> list[dict[str, Any]]:
        for component in self.components_with("samsungce.viewInside", "contents"):
            value = self.value(component, "samsungce.viewInside", "contents")
            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]
        return []

    def fridge_modes(self, component: str) -> list[str]:
        value = self.value(component, "custom.fridgeMode", "supportedFridgeModes")
        return [str(v) for v in value] if isinstance(value, list) else []
