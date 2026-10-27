"""Polling coordinator plus the fridge 'brain': trackers, modes and fridge-screen switches."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later, async_track_point_in_time, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SmartThingsApi, SmartThingsAuthError, SmartThingsError
from .const import (
    COMPONENT_FLEXZONE,
    COMPONENT_FREEZER,
    COMPONENT_FRIDGE,
    COMPONENT_MAIN,
    CONF_DOOR_AJAR_SECONDS,
    CONF_GROCERY_HOURS,
    CONF_PARTY_FLEX_MODE,
    CONF_PARTY_HOURS,
    CONF_SCAN_INTERVAL,
    CONF_VIRTUAL_SWITCHES,
    DEFAULT_DOOR_AJAR_SECONDS,
    DEFAULT_GROCERY_HOURS,
    DEFAULT_PARTY_HOURS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_DINNER_READY,
    EVENT_DOOR,
    MODE_GROCERY,
    MODE_PARTY,
    MODE_VACATION,
    MODES,
    STORAGE_VERSION,
    VIRTUAL_SWITCHES,
)
from .status import FridgeStatus
from .tracker import DoorTracker, FoodSafetyTracker

_LOGGER = logging.getLogger(__name__)


class FrostboxCoordinator(DataUpdateCoordinator[FridgeStatus]):
    """Polls the fridge and owns all derived state."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: SmartThingsApi,
        device: dict[str, Any],
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {device.get('label') or device.get('name')}",
            update_interval=timedelta(seconds=interval),
            request_refresh_debouncer=Debouncer(hass, _LOGGER, cooldown=1.5, immediate=False),
        )
        self.api = api
        self.device = device
        self.device_id: str = device["deviceId"]
        self.location_id: str | None = device.get("locationId")
        self.online = True
        self.doors = DoorTracker()
        self.safety = FoodSafetyTracker()
        self.modes = ModeManager(self)
        self.virtual_switches: dict[str, str] = {}
        self._vs_last: dict[str, bool] = {}
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.state.{entry.entry_id}")
        self._ajar_timer: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    # --- lifecycle -------------------------------------------------------

    async def async_load_state(self) -> None:
        data = await self._store.async_load() or {}
        self.doors = DoorTracker.from_dict(data.get("doors") or {})
        self.safety = FoodSafetyTracker.from_dict(data.get("safety") or {})
        self.virtual_switches = data.get("virtual_switches") or {}
        self.modes.load(data.get("modes") or {})

    @callback
    def async_save_state(self) -> None:
        self._store.async_delay_save(
            lambda: {
                "doors": self.doors.as_dict(),
                "safety": self.safety.as_dict(),
                "modes": self.modes.as_dict(),
                "virtual_switches": self.virtual_switches,
            },
            5,
        )

    async def async_start(self) -> None:
        """Wire up push-ish updates and fridge-screen switches after first refresh."""
        self._unsubs.append(self._track_core_entities())
        self.modes.async_start()
        if self.config_entry.options.get(CONF_VIRTUAL_SWITCHES, False):
            await self._ensure_virtual_switches()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._ajar_timer:
            self._ajar_timer()
            self._ajar_timer = None
        self.modes.async_stop()

    @callback
    def _track_core_entities(self) -> CALLBACK_TYPE:
        """Refresh right away when the core SmartThings integration gets a push event.

        The core integration receives SmartThings events over SSE, so its
        door sensors flip within a second. We piggyback on that instead of
        polling aggressively.
        """
        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)
        device = dev_reg.async_get_device(identifiers={("smartthings", self.device_id)})
        if device is None:
            return lambda: None
        entity_ids = [e.entity_id for e in er.async_entries_for_device(ent_reg, device.id)]
        if not entity_ids:
            return lambda: None

        @callback
        def _changed(_event: Event[EventStateChangedData]) -> None:
            self.hass.async_create_task(self.async_request_refresh())

        return async_track_state_change_event(self.hass, entity_ids, _changed)

    # --- polling ---------------------------------------------------------

    async def _async_update_data(self) -> FridgeStatus:
        try:
            status = FridgeStatus(await self.api.get_status(self.device_id))
            health = await self.api.get_health(self.device_id)
        except SmartThingsAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SmartThingsError as err:
            # Treat cloud/network failures as "offline" for the safety tracker.
            self.safety.update(None, None, False, dt_util.utcnow())
            self.async_save_state()
            raise UpdateFailed(str(err)) from err

        now = dt_util.utcnow()
        self.online = health != "OFFLINE"
        self._process(status, now)
        if self.virtual_switches:
            await self._sync_virtual_switches()
        return status

    @callback
    def _process(self, status: FridgeStatus, now: datetime) -> None:
        doors = {c: status.door_open(c) for c in status.door_components()}
        for event in self.doors.update(doors, now):
            self.hass.bus.async_fire(
                EVENT_DOOR,
                {
                    "device_id": self.device_id,
                    "component": event.component,
                    "state": "open" if event.opened else "closed",
                    "duration": event.duration.total_seconds() if event.duration else None,
                },
            )
        self._schedule_ajar_check(now)

        fridge = status.temperature_f(COMPONENT_FRIDGE)
        if fridge is None:
            fridge = status.temperature_f(COMPONENT_MAIN)
        freezer = status.temperature_f(COMPONENT_FREEZER)
        self.safety.update(fridge, freezer, self.online, now)
        self.async_save_state()

    # --- door ajar -------------------------------------------------------

    @property
    def ajar_threshold(self) -> timedelta:
        return timedelta(seconds=self.config_entry.options.get(CONF_DOOR_AJAR_SECONDS, DEFAULT_DOOR_AJAR_SECONDS))

    def door_ajar(self, now: datetime | None = None) -> bool:
        open_for = self.doors.open_for(now or dt_util.utcnow())
        return open_for is not None and open_for >= self.ajar_threshold

    @callback
    def _schedule_ajar_check(self, now: datetime) -> None:
        if self._ajar_timer:
            self._ajar_timer()
            self._ajar_timer = None
        open_for = self.doors.open_for(now)
        if open_for is None or open_for >= self.ajar_threshold:
            return
        delay = (self.ajar_threshold - open_for).total_seconds() + 0.5

        @callback
        def _fire(_now: datetime) -> None:
            self._ajar_timer = None
            self.async_update_listeners()

        self._ajar_timer = async_call_later(self.hass, delay, _fire)

    # --- commands --------------------------------------------------------

    async def async_command(
        self, component: str, capability: str, command: str, arguments: list[Any] | None = None
    ) -> None:
        try:
            await self.api.send_command(self.device_id, component, capability, command, arguments)
        except SmartThingsAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"SmartThings rejected the token: {err}") from err
        except SmartThingsError as err:
            raise HomeAssistantError(f"Fridge command failed: {err}") from err
        await self.async_request_refresh()

    def _find(self, *candidates: tuple[str, str]) -> tuple[str, str, str] | None:
        """First (component, capability, attribute) the fridge supports."""
        if self.data is None:
            return None
        for capability, attribute in candidates:
            comps = self.data.components_with(capability, attribute)
            if comps:
                comp = COMPONENT_MAIN if COMPONENT_MAIN in comps else comps[0]
                return comp, capability, attribute
        return None

    def power_cool_ref(self) -> tuple[str, str, str] | None:
        return self._find(("samsungce.powerCool", "activated"), ("refrigeration", "rapidCooling"))

    def power_freeze_ref(self) -> tuple[str, str, str] | None:
        return self._find(("samsungce.powerFreeze", "activated"), ("refrigeration", "rapidFreezing"))

    def vacation_ref(self) -> tuple[str, str, str] | None:
        return self._find(
            ("samsungce.fridgeVacationMode", "vacationMode"), ("custom.fridgeVacationMode", "vacationMode")
        )

    def is_on(self, ref: tuple[str, str, str] | None) -> bool | None:
        if ref is None or self.data is None:
            return None
        value = self.data.value(*ref)
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        return str(value).lower() in ("on", "true", "enabled", "activated")

    async def async_set_toggle(self, ref: tuple[str, str, str] | None, on: bool) -> None:
        if ref is None:
            return
        component, capability, attribute = ref
        if capability.startswith("samsungce.power"):
            await self.async_command(component, capability, "activate" if on else "deactivate")
        elif capability == "refrigeration":
            cmd = "setRapidCooling" if attribute == "rapidCooling" else "setRapidFreezing"
            await self.async_command(component, capability, cmd, ["on" if on else "off"])
        elif "vacation" in capability.lower():
            await self.async_command(component, capability, "setVacationMode", ["on" if on else "off"])
        else:
            await self.async_command(component, capability, "on" if on else "off")

    def flex_component(self) -> str | None:
        if self.data is None:
            return None
        comps = self.data.components_with("custom.fridgeMode", "fridgeMode")
        return COMPONENT_FLEXZONE if COMPONENT_FLEXZONE in comps else (comps[0] if comps else None)

    def flex_mode(self) -> str | None:
        comp = self.flex_component()
        return self.data.value(comp, "custom.fridgeMode", "fridgeMode") if comp else None

    def party_flex_mode(self) -> str | None:
        comp = self.flex_component()
        if comp is None:
            return None
        configured = self.config_entry.options.get(CONF_PARTY_FLEX_MODE)
        modes = self.data.fridge_modes(comp)
        if configured and configured in modes:
            return configured
        for mode in modes:
            if any(word in mode.upper() for word in ("BEVER", "DRINK", "SOFT", "COOL")):
                return mode
        return None

    async def async_set_flex_mode(self, mode: str) -> None:
        comp = self.flex_component()
        if comp is None:
            raise HomeAssistantError("This fridge has no FlexZone drawer")
        await self.async_command(comp, "custom.fridgeMode", "setFridgeMode", [mode])

    # --- fridge-screen virtual switches -----------------------------------

    async def _ensure_virtual_switches(self) -> None:
        if not self.location_id:
            _LOGGER.warning("Fridge has no locationId; cannot create fridge-screen switches")
            return
        try:
            existing = {d.get("label") or d.get("name"): d["deviceId"] for d in await self.api.list_devices()}
            for key, label in VIRTUAL_SWITCHES.items():
                if key in self.virtual_switches and self.virtual_switches[key] in existing.values():
                    continue
                if label in existing:
                    self.virtual_switches[key] = existing[label]
                    continue
                created = await self.api.create_virtual_switch(label, self.location_id)
                self.virtual_switches[key] = created["deviceId"]
                _LOGGER.info("Created SmartThings switch '%s' for the Family Hub screen", label)
        except SmartThingsError as err:
            _LOGGER.warning(
                "Could not create fridge-screen switches (%s). The SmartThings token may lack "
                "the w:devices scope; everything else keeps working",
                err,
            )
        self.async_save_state()

    async def _sync_virtual_switches(self) -> None:
        for key, vs_id in list(self.virtual_switches.items()):
            try:
                st = FridgeStatus(await self.api.get_status(vs_id))
            except SmartThingsError as err:
                _LOGGER.debug("Virtual switch %s unavailable: %s", key, err)
                continue
            value = st.value(COMPONENT_MAIN, "switch", "switch") == "on"
            last = self._vs_last.get(key)
            if last is None:
                self._vs_last[key] = value
                if key in MODES and value != self.modes.is_on(key):
                    await self.async_push_virtual_switch(key, self.modes.is_on(key))
                continue
            if value == last:
                continue
            self._vs_last[key] = value
            _LOGGER.debug("Fridge-screen switch %s turned %s", key, value)
            if key == "dinner":
                if value:
                    self.hass.bus.async_fire(EVENT_DINNER_READY, {"source": "fridge"})
                    await self.async_push_virtual_switch(key, False)
            elif key in MODES:
                await self.modes.async_set(key, value, source="fridge")

    async def async_push_virtual_switch(self, key: str, on: bool) -> None:
        vs_id = self.virtual_switches.get(key)
        if not vs_id:
            return
        try:
            await self.api.send_command(vs_id, COMPONENT_MAIN, "switch", "on" if on else "off")
            self._vs_last[key] = on
        except SmartThingsError as err:
            _LOGGER.debug("Could not update fridge-screen switch %s: %s", key, err)


class ModeManager:
    """Party / grocery / vacation modes with auto-expiry and restore."""

    def __init__(self, coordinator: FrostboxCoordinator) -> None:
        self.coordinator = coordinator
        self.state: dict[str, dict[str, Any]] = {m: {"on": False} for m in MODES}
        self._timers: dict[str, CALLBACK_TYPE] = {}
        self._listeners: list[Callable[[], None]] = []

    def load(self, data: dict[str, Any]) -> None:
        for mode in MODES:
            if isinstance(data.get(mode), dict):
                self.state[mode] = data[mode]

    def as_dict(self) -> dict[str, Any]:
        return self.state

    def is_on(self, mode: str) -> bool:
        return bool(self.state[mode].get("on"))

    def until(self, mode: str) -> datetime | None:
        until = self.state[mode].get("until")
        return dt_util.parse_datetime(until) if until else None

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_start(self) -> None:
        now = dt_util.utcnow()
        for mode in MODES:
            until = self.until(mode)
            if self.is_on(mode) and until:
                if until <= now:
                    self.coordinator.hass.async_create_task(self.async_set(mode, False, source="timer"))
                else:
                    self._schedule(mode, until)

    @callback
    def async_stop(self) -> None:
        for unsub in self._timers.values():
            unsub()
        self._timers.clear()

    @callback
    def _schedule(self, mode: str, until: datetime) -> None:
        if unsub := self._timers.pop(mode, None):
            unsub()

        async def _expire(_now: datetime) -> None:
            self._timers.pop(mode, None)
            await self.async_set(mode, False, source="timer")

        self._timers[mode] = async_track_point_in_time(self.coordinator.hass, _expire, until)

    def _default_hours(self, mode: str) -> float | None:
        opts = self.coordinator.config_entry.options
        if mode == MODE_PARTY:
            return opts.get(CONF_PARTY_HOURS, DEFAULT_PARTY_HOURS)
        if mode == MODE_GROCERY:
            return opts.get(CONF_GROCERY_HOURS, DEFAULT_GROCERY_HOURS)
        return None

    async def async_set(self, mode: str, on: bool, *, hours: float | None = None, source: str = "ha") -> None:
        c = self.coordinator
        state = self.state[mode]
        if on:
            hours = hours if hours is not None else self._default_hours(mode)
            until = dt_util.utcnow() + timedelta(hours=hours) if hours else None
            if mode == MODE_PARTY:
                if not state.get("on"):
                    state["restore_flex"] = c.flex_mode()
                await c.async_set_toggle(c.power_cool_ref(), True)
                await c.async_set_toggle(c.power_freeze_ref(), True)
                if (target := c.party_flex_mode()) and target != c.flex_mode():
                    await c.async_set_flex_mode(target)
            elif mode == MODE_GROCERY:
                await c.async_set_toggle(c.power_cool_ref(), True)
            elif mode == MODE_VACATION:
                await c.async_set_toggle(c.vacation_ref(), True)
            state["on"] = True
            state["until"] = until.isoformat() if until else None
            state["since"] = dt_util.utcnow().isoformat()
            if until:
                self._schedule(mode, until)
        else:
            if unsub := self._timers.pop(mode, None):
                unsub()
            if state.get("on"):
                if mode == MODE_PARTY:
                    if not self.is_on(MODE_GROCERY):
                        await c.async_set_toggle(c.power_cool_ref(), False)
                    await c.async_set_toggle(c.power_freeze_ref(), False)
                    restore = state.pop("restore_flex", None)
                    if restore and restore != c.flex_mode():
                        await c.async_set_flex_mode(restore)
                elif mode == MODE_GROCERY and not self.is_on(MODE_PARTY):
                    await c.async_set_toggle(c.power_cool_ref(), False)
                elif mode == MODE_VACATION:
                    await c.async_set_toggle(c.vacation_ref(), False)
            self.state[mode] = {"on": False}

        c.async_save_state()
        if source != "fridge":
            await c.async_push_virtual_switch(mode, on)
        for listener in list(self._listeners):
            listener()
