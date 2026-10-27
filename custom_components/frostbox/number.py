"""Temperature setpoints for fridge, freezer and FlexZone."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import COMPONENT_FREEZER
from .coordinator import FrostboxCoordinator
from .entity import FrostboxEntity
from .models import FrostboxConfigEntry
from .status import component_label

# Samsung's published ranges, used when the fridge does not report its own.
DEFAULT_RANGES_F = {COMPONENT_FREEZER: (-8.0, 5.0)}
DEFAULT_FRIDGE_RANGE_F = (33.0, 44.0)
DEFAULT_RANGES_C = {COMPONENT_FREEZER: (-23.0, -15.0)}
DEFAULT_FRIDGE_RANGE_C = (1.0, 7.0)

CAP = "thermostatCoolingSetpoint"


async def async_setup_entry(
    hass: HomeAssistant, entry: FrostboxConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    comps = coordinator.data.components_with(CAP, "coolingSetpoint")
    if len(comps) > 1 and "main" in comps:
        comps.remove("main")
    async_add_entities(SetpointNumber(coordinator, c) for c in comps)


class SetpointNumber(FrostboxEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer-lines"

    def __init__(self, coordinator: FrostboxCoordinator, component: str) -> None:
        super().__init__(coordinator, f"{component}_setpoint")
        self._component = component
        self._attr_name = f"{component_label(component) or 'Fridge'} setpoint"

    @property
    def _celsius(self) -> bool:
        unit = self.coordinator.data.unit(self._component, CAP, "coolingSetpoint")
        return bool(unit and unit.upper().startswith("C"))

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTemperature.CELSIUS if self._celsius else UnitOfTemperature.FAHRENHEIT

    def _range(self) -> tuple[float, float, float]:
        rng = self.coordinator.data.value(self._component, CAP, "coolingSetpointRange")
        if isinstance(rng, dict) and "minimum" in rng and "maximum" in rng:
            return float(rng["minimum"]), float(rng["maximum"]), float(rng.get("step") or 1)
        if self._celsius:
            lo, hi = DEFAULT_RANGES_C.get(self._component, DEFAULT_FRIDGE_RANGE_C)
        else:
            lo, hi = DEFAULT_RANGES_F.get(self._component, DEFAULT_FRIDGE_RANGE_F)
        return lo, hi, 1.0

    @property
    def native_min_value(self) -> float:
        return self._range()[0]

    @property
    def native_max_value(self) -> float:
        return self._range()[1]

    @property
    def native_step(self) -> float:
        return self._range()[2]

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.value(self._component, CAP, "coolingSetpoint")
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        arg = int(value) if float(value).is_integer() else value
        await self.coordinator.async_command(self._component, CAP, "setCoolingSetpoint", [arg])
