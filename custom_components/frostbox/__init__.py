"""Frostbox: deep Home Assistant integration for the Samsung Bespoke AI Family Hub (RF90F29AEWAA)."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import SmartThingsApi, SmartThingsAuthError, SmartThingsError
from .auth import core_provider, pat_provider
from .const import AUTH_CORE, CONF_AUTH_TYPE, CONF_CORE_ENTRY_ID, CONF_DEVICE_ID, CONF_TOKEN, DOMAIN, STATIC_URL
from .coordinator import FrostboxCoordinator
from .inventory import Inventory
from .mjpeg import MjpegView
from .models import FrostboxConfigEntry, FrostboxData
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TODO,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.http.register_view(MjpegView())
    # Kiosk page and screensavers for the Family Hub browser: /frostbox/kiosk.html
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "www"), cache_headers=False)]
    )
    await async_setup_services(hass)
    return True


def build_api(hass: HomeAssistant, entry: FrostboxConfigEntry) -> SmartThingsApi:
    if entry.data[CONF_AUTH_TYPE] == AUTH_CORE:
        provider = core_provider(hass, entry.data[CONF_CORE_ENTRY_ID])
    else:
        provider = pat_provider(entry.data[CONF_TOKEN])
    return SmartThingsApi(async_get_clientsession(hass), provider)


async def async_setup_entry(hass: HomeAssistant, entry: FrostboxConfigEntry) -> bool:
    api = build_api(hass, entry)
    try:
        device = await api.get_device(entry.data[CONF_DEVICE_ID])
    except SmartThingsAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SmartThingsError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = FrostboxCoordinator(hass, entry, api, device)
    await coordinator.async_load_state()
    inventory = Inventory(hass, entry.entry_id)
    await inventory.async_load()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = FrostboxData(coordinator, inventory)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()
    entry.async_on_unload(coordinator.async_stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FrostboxConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
