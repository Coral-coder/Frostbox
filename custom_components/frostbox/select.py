"""FlexZone drawer mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FrostboxCoordinator
from .entity import FrostboxEntity
from .models import FrostboxConfigEntry
from .status import component_label

CAP = "custom.fridgeMode"


async def async_setup_entry(
    hass: HomeAssistant, entry: FrostboxConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        FridgeModeSelect(coordinator, c)
        for c in coordinator.data.components_with(CAP, "fridgeMode")
        if coordinator.data.fridge_modes(c)
    )


def pretty_mode(mode: str) -> str:
    """``CV_FDR_BEVERAGE`` -> ``Beverage``."""
    text = mode
    for prefix in ("CV_FDR_", "CV_TTYPE_", "CV_"):
        if text.upper().startswith(prefix):
            text = text[len(prefix) :]
    return text.replace("_", " ").title()


class FridgeModeSelect(FrostboxEntity, SelectEntity):
    _attr_icon = "mdi:tray-full"

    def __init__(self, coordinator: FrostboxCoordinator, component: str) -> None:
        super().__init__(coordinator, f"{component}_mode")
        self._component = component
        label = component_label(component)
        self._attr_name = f"{label} mode" if label else "Mode"

    @property
    def _modes(self) -> list[str]:
        return self.coordinator.data.fridge_modes(self._component)

    @property
    def options(self) -> list[str]:
        return [pretty_mode(m) for m in self._modes]

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.value(self._component, CAP, "fridgeMode")
        return pretty_mode(value) if value else None

    async def async_select_option(self, option: str) -> None:
        raw = next(m for m in self._modes if pretty_mode(m) == option)
        await self.coordinator.async_command(self._component, CAP, "setFridgeMode", [raw])
