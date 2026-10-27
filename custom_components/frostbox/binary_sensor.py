"""Binary sensors: doors, door ajar, filter, food safety, connectivity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import FrostboxCoordinator
from .entity import FrostboxEntity
from .models import FrostboxConfigEntry
from .status import component_label
from .tracker import SAFETY_UNSAFE, SAFETY_WARNING

FILTER_REPLACE_AT = 90


async def async_setup_entry(
    hass: HomeAssistant, entry: FrostboxConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    status = coordinator.data
    entities: list[BinarySensorEntity] = [DoorSensor(coordinator, c) for c in status.door_components()]
    entities += [
        AnyDoorOpen(coordinator),
        DoorAjar(coordinator),
        FoodSafetyProblem(coordinator),
        FridgeOnline(coordinator),
    ]
    if status.components_with("custom.waterFilter", "waterFilterUsage") or status.components_with(
        "custom.waterFilter", "waterFilterStatus"
    ):
        entities.append(WaterFilterReplace(coordinator))
    if status.components_with("refrigeration", "defrost"):
        entities.append(Defrosting(coordinator))
    async_add_entities(entities)


class DoorSensor(FrostboxEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, coordinator: FrostboxCoordinator, component: str) -> None:
        super().__init__(coordinator, f"{component}_door")
        self._component = component
        label = component_label(component)
        self._attr_name = f"{label} door" if label else "Door"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.door_open(self._component)


class AnyDoorOpen(FrostboxEntity, BinarySensorEntity):
    _attr_name = "Any door open"
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "any_door")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.any_door_open()


class DoorAjar(FrostboxEntity, BinarySensorEntity):
    """On when any door has been open longer than the configured threshold."""

    _attr_name = "Door ajar"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:fridge-alert"

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "door_ajar")

    @property
    def is_on(self) -> bool:
        return self.coordinator.door_ajar()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        doors = self.coordinator.doors
        open_for = doors.open_for(dt_util.utcnow())
        return {
            "open_doors": [component_label(c) or "Door" for c in doors.open_since],
            "open_seconds": round(open_for.total_seconds()) if open_for else 0,
            "threshold_seconds": round(self.coordinator.ajar_threshold.total_seconds()),
        }


class WaterFilterReplace(FrostboxEntity, BinarySensorEntity):
    _attr_name = "Water filter replacement needed"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:water-alert"

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "water_filter_replace")

    @property
    def is_on(self) -> bool | None:
        st = self.coordinator.data
        for comp in st.components_with("custom.waterFilter"):
            status = st.value(comp, "custom.waterFilter", "waterFilterStatus")
            usage = st.value(comp, "custom.waterFilter", "waterFilterUsage")
            if isinstance(status, str) and status.lower() in ("replace", "change", "expired"):
                return True
            if isinstance(usage, (int, float)):
                return usage >= FILTER_REPLACE_AT
        return None


class FoodSafetyProblem(FrostboxEntity, BinarySensorEntity):
    """HomeKit-friendly flag for the food safety sensor."""

    _attr_name = "Food safety alert"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "food_safety_alert")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.safety.state(dt_util.utcnow()) in (SAFETY_WARNING, SAFETY_UNSAFE)


class FridgeOnline(FrostboxEntity, BinarySensorEntity):
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "online")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.online


class Defrosting(FrostboxEntity, BinarySensorEntity):
    _attr_name = "Defrosting"
    _attr_icon = "mdi:snowflake-melt"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "defrost")

    @property
    def is_on(self) -> bool | None:
        comps = self.coordinator.data.components_with("refrigeration", "defrost")
        return self.coordinator.is_on((comps[0], "refrigeration", "defrost")) if comps else None
