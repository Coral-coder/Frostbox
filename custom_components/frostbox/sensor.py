"""Sensors: temperatures, filters, energy, door analytics, food safety, inventory."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_EXPIRY_WARNING_DAYS, DEFAULT_EXPIRY_WARNING_DAYS
from .coordinator import FrostboxCoordinator
from .entity import FrostboxEntity
from .inventory import Inventory
from .models import FrostboxConfigEntry
from .status import AttributeRef, component_label
from .tracker import SAFETY_OK, SAFETY_UNKNOWN, SAFETY_UNSAFE, SAFETY_WARNING

# Attributes that have a dedicated entity somewhere; everything else becomes
# a disabled-by-default diagnostic sensor so nothing the fridge reports is lost.
MAPPED: set[tuple[str, str]] = {
    ("temperatureMeasurement", "temperature"),
    ("contactSensor", "contact"),
    ("custom.waterFilter", "waterFilterUsage"),
    ("custom.waterFilter", "waterFilterStatus"),
    ("custom.deodorFilter", "deodorFilterUsage"),
    ("powerConsumptionReport", "powerConsumption"),
    ("thermostatCoolingSetpoint", "coolingSetpoint"),
    ("custom.fridgeMode", "fridgeMode"),
    ("refrigeration", "rapidCooling"),
    ("refrigeration", "rapidFreezing"),
    ("samsungce.powerCool", "activated"),
    ("samsungce.powerFreeze", "activated"),
    ("samsungce.viewInside", "contents"),
    ("samsungce.fridgeVacationMode", "vacationMode"),
    ("custom.fridgeVacationMode", "vacationMode"),
    ("refrigeration", "defrost"),
    ("switch", "switch"),
}
SKIP_CAPABILITIES = {
    "execute",
    "refresh",
    "healthCheck",
    "custom.disabledCapabilities",
    "custom.disabledComponents",
    "samsungce.unavailableCapabilities",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: FrostboxConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    inventory = entry.runtime_data.inventory
    status = coordinator.data
    entities: list[SensorEntity] = []

    for comp in status.components_with("temperatureMeasurement", "temperature"):
        if comp == "main" and len(status.components_with("temperatureMeasurement", "temperature")) > 1:
            continue
        entities.append(TemperatureSensor(coordinator, comp))

    simple: list[tuple[str, str, str, dict[str, Any], Callable[[Any], Any]]] = [
        (
            "custom.waterFilter",
            "waterFilterUsage",
            "Water filter used",
            {
                "native_unit_of_measurement": PERCENTAGE,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:water-check",
            },
            _num,
        ),
        ("custom.waterFilter", "waterFilterStatus", "Water filter status", {"icon": "mdi:water-alert"}, str),
        (
            "custom.deodorFilter",
            "deodorFilterUsage",
            "Deodorizer filter used",
            {
                "native_unit_of_measurement": PERCENTAGE,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:air-filter",
            },
            _num,
        ),
    ]
    for cap, attr, name, attrs, fn in simple:
        for comp in status.components_with(cap, attr):
            entities.append(SimpleSensor(coordinator, AttributeRef(comp, cap, attr), name, attrs, fn))
            break

    for comp in status.components_with("powerConsumptionReport", "powerConsumption"):
        entities.append(EnergySensor(coordinator, comp))
        entities.append(PowerSensor(coordinator, comp))
        break

    entities += [
        DoorOpensToday(coordinator),
        DoorOpenTimeToday(coordinator),
        DoorLastOpened(coordinator),
        FoodSafetySensor(coordinator),
        InventoryCount(coordinator, inventory),
        ExpiringSoon(coordinator, inventory),
        ExpiredItems(coordinator, inventory),
    ]

    for ref, value in status.iter_attributes():
        if (ref.capability, ref.attribute) in MAPPED or ref.capability in SKIP_CAPABILITIES:
            continue
        if isinstance(value, (str, int, float, bool)) and not (isinstance(value, str) and len(value) > 250):
            entities.append(RawSensor(coordinator, ref))

    async_add_entities(entities)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


class TemperatureSensor(FrostboxEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FrostboxCoordinator, component: str) -> None:
        super().__init__(coordinator, f"{component}_temperature")
        self._component = component
        self._attr_name = f"{component_label(component) or 'Fridge'} temperature".strip()

    @property
    def native_unit_of_measurement(self) -> str:
        unit = self.coordinator.data.unit(self._component, "temperatureMeasurement", "temperature")
        return UnitOfTemperature.CELSIUS if unit and unit.upper().startswith("C") else UnitOfTemperature.FAHRENHEIT

    @property
    def native_value(self) -> float | None:
        return _num(self.coordinator.data.value(self._component, "temperatureMeasurement", "temperature"))


class SimpleSensor(FrostboxEntity, SensorEntity):
    def __init__(
        self,
        coordinator: FrostboxCoordinator,
        ref: AttributeRef,
        name: str,
        attrs: dict[str, Any],
        fn: Callable[[Any], Any],
    ) -> None:
        super().__init__(coordinator, f"{ref.component}_{ref.capability}_{ref.attribute}")
        self._ref = ref
        self._fn = fn
        self._attr_name = name
        for key, val in attrs.items():
            setattr(self, f"_attr_{key}", val)

    @property
    def native_value(self) -> Any:
        value = self.coordinator.data.value(self._ref.component, self._ref.capability, self._ref.attribute)
        return None if value is None else self._fn(value)


class EnergySensor(FrostboxEntity, SensorEntity):
    _attr_name = "Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: FrostboxCoordinator, component: str) -> None:
        super().__init__(coordinator, "energy")
        self._component = component

    @property
    def native_value(self) -> float | None:
        report = self.coordinator.data.value(self._component, "powerConsumptionReport", "powerConsumption")
        if isinstance(report, dict) and isinstance(report.get("energy"), (int, float)):
            return report["energy"] / 1000  # SmartThings reports Wh
        return None


class PowerSensor(EnergySensor):
    _attr_name = "Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: FrostboxCoordinator, component: str) -> None:
        FrostboxEntity.__init__(self, coordinator, "power")
        self._component = component

    @property
    def native_value(self) -> float | None:
        report = self.coordinator.data.value(self._component, "powerConsumptionReport", "powerConsumption")
        if isinstance(report, dict) and isinstance(report.get("power"), (int, float)):
            return float(report["power"])
        return None


class DoorOpensToday(FrostboxEntity, SensorEntity):
    _attr_name = "Door openings today"
    _attr_icon = "mdi:fridge-outline"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "door_opens_today")

    @property
    def native_value(self) -> int:
        self.coordinator.doors._roll_day(dt_util.utcnow())
        return self.coordinator.doors.opens_today

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        doors = self.coordinator.doors
        return {
            "longest_open_seconds": round(doors.longest_open_seconds_today),
            "last_door": component_label(doors.last_component) if doors.last_component else None,
        }


class DoorOpenTimeToday(FrostboxEntity, SensorEntity):
    _attr_name = "Door open time today"
    _attr_icon = "mdi:timer-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "door_open_time_today")

    @property
    def native_value(self) -> float:
        doors = self.coordinator.doors
        now = dt_util.utcnow()
        doors._roll_day(now)
        current = doors.open_for(now)
        return (doors.open_seconds_today + (current.total_seconds() if current else 0)) / 60


class DoorLastOpened(FrostboxEntity, SensorEntity):
    _attr_name = "Door last opened"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "door_last_opened")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.doors.last_opened


class FoodSafetySensor(FrostboxEntity, SensorEntity):
    _attr_name = "Food safety"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [SAFETY_OK, SAFETY_WARNING, SAFETY_UNSAFE, SAFETY_UNKNOWN]

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        super().__init__(coordinator, "food_safety")

    @property
    def available(self) -> bool:
        # Stay available during outages: that is when this sensor matters most.
        return True

    @property
    def native_value(self) -> str:
        return self.coordinator.safety.state(dt_util.utcnow())

    @property
    def icon(self) -> str:
        return {
            SAFETY_OK: "mdi:shield-check",
            SAFETY_WARNING: "mdi:shield-alert",
            SAFETY_UNSAFE: "mdi:biohazard",
        }.get(self.native_value, "mdi:shield-outline")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.safety.attributes(dt_util.utcnow())


class _InventorySensor(FrostboxEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "items"

    def __init__(self, coordinator: FrostboxCoordinator, inventory: Inventory, key: str) -> None:
        super().__init__(coordinator, key)
        self.inventory = inventory

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.inventory.async_add_listener(self._inv_changed))

    @callback
    def _inv_changed(self) -> None:
        self.async_write_ha_state()

    @property
    def warning_days(self) -> int:
        return self.coordinator.config_entry.options.get(CONF_EXPIRY_WARNING_DAYS, DEFAULT_EXPIRY_WARNING_DAYS)

    def _brief(self, items: list) -> list[dict[str, Any]]:
        today = self.inventory.today
        return [
            {"name": i.name, "qty": i.quantity, "location": i.location, "days_left": i.days_left(today), "id": i.id}
            for i in items[:40]
        ]


class InventoryCount(_InventorySensor):
    _attr_name = "Inventory"
    _attr_icon = "mdi:food-variant"

    def __init__(self, coordinator: FrostboxCoordinator, inventory: Inventory) -> None:
        super().__init__(coordinator, inventory, "inventory")

    @property
    def native_value(self) -> int:
        return sum(1 for i in self.inventory.items() if i.quantity > 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = [i for i in self.inventory.items() if i.quantity > 0]
        by_loc: dict[str, int] = {}
        for item in items:
            by_loc[item.location] = by_loc.get(item.location, 0) + 1
        return {
            "by_location": by_loc,
            "items": self._brief(items),
            "low_staples": [i.name for i in self.inventory.low_staples()],
        }


class ExpiringSoon(_InventorySensor):
    _attr_name = "Expiring soon"
    _attr_icon = "mdi:clock-alert-outline"

    def __init__(self, coordinator: FrostboxCoordinator, inventory: Inventory) -> None:
        super().__init__(coordinator, inventory, "expiring_soon")

    @property
    def native_value(self) -> int:
        return len(self.inventory.expiring(self.warning_days))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = self.inventory.expiring(self.warning_days)
        return {
            "items": self._brief(items),
            "use_first": ", ".join(i.name for i in items[:5]),
            "within_days": self.warning_days,
        }


class ExpiredItems(_InventorySensor):
    _attr_name = "Expired"
    _attr_icon = "mdi:delete-clock-outline"

    def __init__(self, coordinator: FrostboxCoordinator, inventory: Inventory) -> None:
        super().__init__(coordinator, inventory, "expired")

    @property
    def native_value(self) -> int:
        return len(self.inventory.expired())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"items": self._brief(self.inventory.expired())}


class RawSensor(FrostboxEntity, SensorEntity):
    """Every other attribute the fridge reports, for discovery and power users."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: FrostboxCoordinator, ref: AttributeRef) -> None:
        super().__init__(coordinator, f"raw_{ref.component}_{ref.capability}_{ref.attribute}")
        self._ref = ref
        comp = component_label(ref.component)
        self._attr_name = f"{comp + ' ' if comp else ''}{ref.capability} {ref.attribute}"

    @property
    def native_value(self) -> Any:
        value = self.coordinator.data.value(self._ref.component, self._ref.capability, self._ref.attribute)
        if isinstance(value, bool):
            return "on" if value else "off"
        return value

    @property
    def native_unit_of_measurement(self) -> str | None:
        value = self.coordinator.data.value(self._ref.component, self._ref.capability, self._ref.attribute)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return self.coordinator.data.unit(self._ref.component, self._ref.capability, self._ref.attribute)
