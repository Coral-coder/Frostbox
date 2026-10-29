"""Frostbox: deep Home Assistant integration for the Samsung Bespoke AI Family Hub (RF90F29AEWAA)."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from . import websocket_api
from .api import SmartThingsApi, SmartThingsAuthError, SmartThingsError
from .auth import core_provider, pat_provider
from .const import (
    AUTH_CORE,
    CONF_AUTH_TYPE,
    CONF_CORE_ENTRY_ID,
    CONF_DEVICE_ID,
    CONF_HDHOMERUN_HOST,
    CONF_PLEX_TOKEN,
    CONF_PLEX_URL,
    CONF_TOKEN,
    CONF_TV_QUALITY,
    DEFAULT_TV_QUALITY,
    DOMAIN,
    STATIC_URL,
)
from .coordinator import FrostboxCoordinator
from .hdhomerun import HdHomeRun, TvHlsView, TvStreamer
from .inventory import Inventory
from .mjpeg import MjpegView
from .models import FrostboxConfigEntry, FrostboxData, TvMedia
from .plex import PlexClient, PlexImageView
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
    hass.http.register_view(TvHlsView(lambda: _media_part(hass, "tv")))
    hass.http.register_view(PlexImageView(lambda: _media_part(hass, "plex")))
    websocket_api.async_register(hass)
    # Kiosk page and screensavers for the Family Hub browser: /frostbox/kiosk.html
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "www"), cache_headers=False)]
    )
    await async_setup_services(hass)
    return True


def _media_part(hass: HomeAssistant, part: str):
    for entry in hass.config_entries.async_entries(DOMAIN):
        data = getattr(entry, "runtime_data", None)
        if isinstance(data, FrostboxData):
            value = getattr(data, part)
            return value.streamer if part == "tv" and value else value
    return None


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

    tuner = HdHomeRun(hass, entry.options.get(CONF_HDHOMERUN_HOST))
    streamer = TvStreamer(
        hass, tuner, get_ffmpeg_manager(hass).binary, entry.options.get(CONF_TV_QUALITY, DEFAULT_TV_QUALITY)
    )
    plex = PlexClient(hass, entry.options.get(CONF_PLEX_URL), entry.options.get(CONF_PLEX_TOKEN))
    entry.runtime_data = FrostboxData(coordinator, inventory, TvMedia(tuner, streamer), plex)
    entry.async_on_unload(streamer.stop_all)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()
    entry.async_on_unload(coordinator.async_stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FrostboxConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
