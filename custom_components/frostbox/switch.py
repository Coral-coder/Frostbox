"""Switches: Power Cool/Freeze, ice makers, and Frostbox modes."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MODE_GROCERY, MODE_PARTY, MODE_VACATION, MODES
from .coordinator import FrostboxCoordinator
from .entity import FrostboxEntity
from .features import ON_OFF_FEATURES, OnOffFeature
from .models import FrostboxConfigEntry
from .status import component_label

MODE_META = {
    MODE_PARTY: ("Party mode", "mdi:party-popper"),
    MODE_GROCERY: ("Grocery mode", "mdi:cart-arrow-down"),
    MODE_VACATION: ("Vacation mode", "mdi:airplane"),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: FrostboxConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = []
    if coordinator.power_cool_ref():
        entities.append(
            ToggleSwitch(coordinator, "power_cool", "Power Cool", "mdi:snowflake", coordinator.power_cool_ref)
        )
    if coordinator.power_freeze_ref():
        entities.append(
            ToggleSwitch(
                coordinator, "power_freeze", "Power Freeze", "mdi:snowflake-variant", coordinator.power_freeze_ref
            )
        )
    for comp in coordinator.data.components_with("switch", "switch"):
        if comp.startswith("icemaker"):
            entities.append(IceMakerSwitch(coordinator, comp))
    st = coordinator.data
    for feature in ON_OFF_FEATURES:
        comps = st.components_with(feature.capability, feature.attribute)
        comps = [c for c in comps if st.value(c, feature.capability, feature.attribute) is not None]
        if comps:
            entities.append(FeatureSwitch(coordinator, feature, "main" if "main" in comps else comps[0]))
    entities += [ModeSwitch(coordinator, mode) for mode in MODES]
    async_add_entities(entities)


class ToggleSwitch(FrostboxEntity, SwitchEntity):
    def __init__(self, coordinator: FrostboxCoordinator, key: str, name: str, icon: str, ref_fn) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_icon = icon
        self._ref_fn = ref_fn

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.is_on(self._ref_fn())

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_toggle(self._ref_fn(), True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_toggle(self._ref_fn(), False)


class FeatureSwitch(FrostboxEntity, SwitchEntity):
    """A Samsung on/off feature (AutoFill pitcher, Auto Open Door, door alarm, ...)."""

    def __init__(self, coordinator: FrostboxCoordinator, feature: OnOffFeature, component: str) -> None:
        super().__init__(coordinator, feature.key)
        self._feature = feature
        self._component = component
        self._attr_name = feature.name
        self._attr_icon = feature.icon
        self._attr_entity_category = feature.category

    @property
    def is_on(self) -> bool | None:
        f = self._feature
        return self.coordinator.is_on((self._component, f.capability, f.attribute))

    async def _set(self, on: bool) -> None:
        f = self._feature
        if f.setter:
            await self.coordinator.async_command(self._component, f.capability, f.setter, ["on" if on else "off"])
        else:
            await self.coordinator.async_command(self._component, f.capability, "on" if on else "off")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class IceMakerSwitch(FrostboxEntity, SwitchEntity):
    _attr_icon = "mdi:cube-outline"

    def __init__(self, coordinator: FrostboxCoordinator, component: str) -> None:
        super().__init__(coordinator, f"{component}_switch")
        self._component = component
        self._attr_name = component_label(component)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.value(self._component, "switch", "switch")
        return None if value is None else value == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(self._component, "switch", "on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(self._component, "switch", "off")


class ModeSwitch(FrostboxEntity, SwitchEntity):
    """A Frostbox scene for the fridge that can expire on its own."""

    def __init__(self, coordinator: FrostboxCoordinator, mode: str) -> None:
        super().__init__(coordinator, f"mode_{mode}")
        self._mode = mode
        self._attr_name, self._attr_icon = MODE_META[mode]

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.modes.async_add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.coordinator.modes.is_on(self._mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        until = self.coordinator.modes.until(self._mode)
        return {"ends_at": until.isoformat() if until else None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.modes.async_set(self._mode, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.modes.async_set(self._mode, False)
