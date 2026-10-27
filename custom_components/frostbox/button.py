"""Buttons: dinner ready, water filter reset, food safety acknowledge, refresh."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_DINNER_READY
from .coordinator import FrostboxCoordinator
from .entity import FrostboxEntity
from .models import FrostboxConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: FrostboxConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[ButtonEntity] = [DinnerReady(coordinator), AckFoodSafety(coordinator), Refresh(coordinator)]
    if coordinator.data.components_with("custom.waterFilter", "waterFilterUsage"):
        entities.append(ResetWaterFilter(coordinator))
    async_add_entities(entities)


class DinnerReady(FrostboxEntity, ButtonEntity):
    """Fires ``frostbox_dinner_ready``; the Plex/TV blueprint listens for it."""

    _attr_name = "Dinner ready"
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "dinner_ready")

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        self.hass.bus.async_fire(EVENT_DINNER_READY, {"source": "home_assistant"})


class AckFoodSafety(FrostboxEntity, ButtonEntity):
    _attr_name = "Acknowledge food safety alert"
    _attr_icon = "mdi:check-decagram"

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "ack_food_safety")

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        self.coordinator.safety.acknowledge()
        self.coordinator.async_save_state()
        self.coordinator.async_update_listeners()


class ResetWaterFilter(FrostboxEntity, ButtonEntity):
    _attr_name = "Reset water filter"
    _attr_icon = "mdi:water-sync"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "reset_water_filter")

    async def async_press(self) -> None:
        comp = self.coordinator.data.components_with("custom.waterFilter", "waterFilterUsage")[0]
        await self.coordinator.async_command(comp, "custom.waterFilter", "resetWaterFilter")


class Refresh(FrostboxEntity, ButtonEntity):
    _attr_name = "Refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
