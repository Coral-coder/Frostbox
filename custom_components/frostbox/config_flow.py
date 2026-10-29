"""Config and options flow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import SmartThingsApi, SmartThingsAuthError, SmartThingsError, is_refrigerator
from .auth import CORE_DOMAIN, core_provider, pat_provider
from .const import (
    AUTH_CORE,
    AUTH_PAT,
    CONF_AUTH_TYPE,
    CONF_CAMERA_REFRESH,
    CONF_CAMERA_SOURCES,
    CONF_CORE_ENTRY_ID,
    CONF_DEVICE_ID,
    CONF_DOOR_AJAR_SECONDS,
    CONF_EXPIRY_WARNING_DAYS,
    CONF_GROCERY_HOURS,
    CONF_HDHOMERUN_HOST,
    CONF_LOCATION_ID,
    CONF_PARTY_FLEX_MODE,
    CONF_PARTY_HOURS,
    CONF_PLEX_TOKEN,
    CONF_PLEX_URL,
    CONF_RECIPE_AGENT,
    CONF_SCAN_INTERVAL,
    CONF_SHOPPING_LIST,
    CONF_TOKEN,
    CONF_TV_QUALITY,
    CONF_VIEW_INSIDE_TOKEN,
    CONF_VIEW_INSIDE_URL,
    CONF_VIRTUAL_SWITCHES,
    DEFAULT_CAMERA_REFRESH,
    DEFAULT_DOOR_AJAR_SECONDS,
    DEFAULT_EXPIRY_WARNING_DAYS,
    DEFAULT_GROCERY_HOURS,
    DEFAULT_PARTY_HOURS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SHOPPING_LIST,
    DEFAULT_TV_QUALITY,
    DOMAIN,
)


class FrostboxConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._auth: dict[str, Any] = {}
        self._fridges: dict[str, dict[str, Any]] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FrostboxOptionsFlow:
        return FrostboxOptionsFlow()

    def _api(self) -> SmartThingsApi:
        if self._auth[CONF_AUTH_TYPE] == AUTH_CORE:
            provider = core_provider(self.hass, self._auth[CONF_CORE_ENTRY_ID])
        else:
            provider = pat_provider(self._auth[CONF_TOKEN])
        return SmartThingsApi(async_get_clientsession(self.hass), provider)

    async def _load_fridges(self) -> str | None:
        try:
            devices = await self._api().list_devices()
        except SmartThingsAuthError:
            return "invalid_auth"
        except SmartThingsError:
            return "cannot_connect"
        self._fridges = {d["deviceId"]: d for d in devices if is_refrigerator(d)}
        return None if self._fridges else "no_fridge"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["core", "pat"])

    async def async_step_core(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entries = [e for e in self.hass.config_entries.async_entries(CORE_DOMAIN) if e.state is ConfigEntryState.LOADED]
        if not entries:
            return self.async_abort(reason="core_missing")
        if len(entries) > 1 and user_input is None:
            return self.async_show_form(
                step_id="core",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_CORE_ENTRY_ID): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[selector.SelectOptionDict(value=e.entry_id, label=e.title) for e in entries]
                            )
                        )
                    }
                ),
            )
        entry_id = user_input[CONF_CORE_ENTRY_ID] if user_input else entries[0].entry_id
        self._auth = {CONF_AUTH_TYPE: AUTH_CORE, CONF_CORE_ENTRY_ID: entry_id}
        if error := await self._load_fridges():
            return self.async_abort(reason=error)
        return await self.async_step_device()

    async def async_step_pat(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._auth = {CONF_AUTH_TYPE: AUTH_PAT, CONF_TOKEN: user_input[CONF_TOKEN].strip()}
            if (error := await self._load_fridges()) is None:
                return await self.async_step_device()
            errors["base"] = error
        return self.async_show_form(
            step_id="pat",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None and len(self._fridges) > 1:
            return self.async_show_form(
                step_id="device",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_DEVICE_ID): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    selector.SelectOptionDict(value=k, label=v.get("label") or v.get("name") or k)
                                    for k, v in self._fridges.items()
                                ]
                            )
                        )
                    }
                ),
            )
        device_id = user_input[CONF_DEVICE_ID] if user_input else next(iter(self._fridges))
        device = self._fridges[device_id]
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=device.get("label") or device.get("name") or "Family Hub",
            data={**self._auth, CONF_DEVICE_ID: device_id, CONF_LOCATION_ID: device.get("locationId")},
            options={CONF_CAMERA_SOURCES: []},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        if entry_data.get(CONF_AUTH_TYPE) == AUTH_CORE:
            return self.async_abort(reason="reauth_core")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._auth = {CONF_AUTH_TYPE: AUTH_PAT, CONF_TOKEN: user_input[CONF_TOKEN].strip()}
            if (error := await self._load_fridges()) is None:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_TOKEN: self._auth[CONF_TOKEN]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )


class FrostboxOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options
        num = selector.NumberSelector
        cfg = selector.NumberSelectorConfig
        schema = vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL, default=o.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): num(
                    cfg(min=10, max=600, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_DOOR_AJAR_SECONDS, default=o.get(CONF_DOOR_AJAR_SECONDS, DEFAULT_DOOR_AJAR_SECONDS)
                ): num(cfg(min=15, max=900, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)),
                vol.Optional(
                    CONF_EXPIRY_WARNING_DAYS, default=o.get(CONF_EXPIRY_WARNING_DAYS, DEFAULT_EXPIRY_WARNING_DAYS)
                ): num(cfg(min=0, max=14, unit_of_measurement="days", mode=selector.NumberSelectorMode.BOX)),
                vol.Optional(
                    CONF_SHOPPING_LIST, default=o.get(CONF_SHOPPING_LIST, DEFAULT_SHOPPING_LIST)
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="todo")),
                vol.Optional(CONF_CAMERA_SOURCES, default=o.get(CONF_CAMERA_SOURCES, [])): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="camera", multiple=True)
                ),
                vol.Optional(CONF_CAMERA_REFRESH, default=o.get(CONF_CAMERA_REFRESH, DEFAULT_CAMERA_REFRESH)): num(
                    cfg(min=15, max=3600, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_VIEW_INSIDE_URL, description={"suggested_value": o.get(CONF_VIEW_INSIDE_URL)}
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_VIEW_INSIDE_TOKEN, description={"suggested_value": o.get(CONF_VIEW_INSIDE_TOKEN)}
                ): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
                vol.Optional(
                    CONF_VIRTUAL_SWITCHES, default=o.get(CONF_VIRTUAL_SWITCHES, False)
                ): selector.BooleanSelector(),
                vol.Optional(CONF_PARTY_HOURS, default=o.get(CONF_PARTY_HOURS, DEFAULT_PARTY_HOURS)): num(
                    cfg(min=0.5, max=24, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_GROCERY_HOURS, default=o.get(CONF_GROCERY_HOURS, DEFAULT_GROCERY_HOURS)): num(
                    cfg(min=0.5, max=12, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_PARTY_FLEX_MODE, description={"suggested_value": o.get(CONF_PARTY_FLEX_MODE)}
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_RECIPE_AGENT, description={"suggested_value": o.get(CONF_RECIPE_AGENT)}
                ): selector.ConversationAgentSelector(),
                vol.Optional(
                    CONF_HDHOMERUN_HOST, description={"suggested_value": o.get(CONF_HDHOMERUN_HOST)}
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_TV_QUALITY, default=o.get(CONF_TV_QUALITY, DEFAULT_TV_QUALITY)
                ): selector.SelectSelector(selector.SelectSelectorConfig(options=["480p", "720p", "1080p"])),
                vol.Optional(
                    CONF_PLEX_URL, description={"suggested_value": o.get(CONF_PLEX_URL)}
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_PLEX_TOKEN, description={"suggested_value": o.get(CONF_PLEX_TOKEN)}
                ): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
