"""Websocket commands used by the fridge-screen apps (Live TV, guide, Plex)."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
import voluptuous as vol

from .const import DOMAIN
from .hdhomerun import TvError
from .plex import PlexError


def _media(hass: HomeAssistant):
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


@callback
def async_register(hass: HomeAssistant) -> None:
    for handler in (ws_channels, ws_guide, ws_watch, ws_stop, ws_plex_new):
        websocket_api.async_register_command(hass, handler)


async def _run(hass: HomeAssistant, connection, msg, coro_fn) -> None:
    data = _media(hass)
    try:
        result = await coro_fn(data)
    except (TvError, PlexError) as err:
        connection.send_error(msg["id"], "frostbox_error", str(err))
        return
    connection.send_result(msg["id"], result)


def _require_tv(data):
    if data is None or data.tv is None:
        raise TvError("Live TV isn't set up. Frostbox needs to be loaded with an HDHomeRun on the network.")
    return data.tv


@websocket_api.websocket_command({vol.Required("type"): "frostbox/tv/channels"})
@websocket_api.async_response
async def ws_channels(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def run(data):
        tv = _require_tv(data)
        channels = await tv.tuner.lineup()
        return {
            "tuner": {
                "name": tv.tuner.info.get("FriendlyName"),
                "model": tv.tuner.info.get("ModelNumber"),
                "tuners": tv.tuner.info.get("TunerCount"),
            },
            "channels": [c.as_dict() for c in channels],
        }

    await _run(hass, connection, msg, run)


@websocket_api.websocket_command(
    {vol.Required("type"): "frostbox/tv/guide", vol.Optional("hours", default=4): vol.All(int, vol.Range(1, 24))}
)
@websocket_api.async_response
async def ws_guide(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def run(data):
        tv = _require_tv(data)
        channels = await tv.tuner.lineup()
        now = int(time.time())
        until = now + msg["hours"] * 3600
        try:
            guide = await tv.tuner.guide()
            error = None
        except TvError as err:
            guide, error = {}, str(err)
        rows = []
        for ch in channels:
            g = guide.get(ch.number) or {}
            programs = [p for p in g.get("programs", []) if p["end"] > now and p["start"] < until]
            rows.append({**ch.as_dict(), "image": g.get("image"), "programs": programs})
        return {"now": now, "until": until, "channels": rows, "guide_error": error}

    await _run(hass, connection, msg, run)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "frostbox/tv/watch",
        vol.Required("channel"): str,
        vol.Optional("replace"): str,
    }
)
@websocket_api.async_response
async def ws_watch(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def run(data):
        tv = _require_tv(data)
        session = await tv.streamer.start(msg["channel"], replace=msg.get("replace"))
        return {
            "session": session.id,
            "url": f"/api/frostbox/tv/{session.id}/index.m3u8",
            "channel": session.channel.as_dict(),
        }

    await _run(hass, connection, msg, run)


@websocket_api.websocket_command({vol.Required("type"): "frostbox/tv/stop", vol.Required("session"): str})
@websocket_api.async_response
async def ws_stop(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def run(data):
        tv = _require_tv(data)
        await tv.streamer.stop(msg["session"])
        return {}

    await _run(hass, connection, msg, run)


@websocket_api.websocket_command({vol.Required("type"): "frostbox/plex/new"})
@websocket_api.async_response
async def ws_plex_new(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def run(data):
        if data is None or data.plex is None:
            raise PlexError("Frostbox isn't loaded")
        return await data.plex.whats_new()

    await _run(hass, connection, msg, run)
