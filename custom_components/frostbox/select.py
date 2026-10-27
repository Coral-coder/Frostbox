"""FlexZone drawer mode and the fridge-screen screensaver choice."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SCREENSAVERS
from .coordinator import FrostboxCoordinator
from .entity import FrostboxEntity
from .models import FrostboxConfigEntry
from .status import component_label

CAP = "custom.fridgeMode"


async def async_setup_entry(
    hass: HomeAssistant, entry: FrostboxConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SelectEntity] = [
        FridgeModeSelect(coordinator, c)
        for c in coordinator.data.components_with(CAP, "fridgeMode")
        if coordinator.data.fridge_modes(c)
    ]
    entities.append(ScreensaverSelect(coordinator))
    async_add_entities(entities)


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


class ScreensaverSelect(FrostboxEntity, SelectEntity, RestoreEntity):
    """Which screensaver the fridge kiosk page shows when idle."""

    _attr_name = "Screensaver"
    _attr_icon = "mdi:fishbowl-outline"
    _attr_options = list(SCREENSAVERS)

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "screensaver")
        self._attr_current_option = SCREENSAVERS[0]

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in SCREENSAVERS:
            self._attr_current_option = last.state

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()
