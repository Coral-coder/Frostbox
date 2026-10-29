"""Plex "what's new" for the fridge: newest shows, movies and continue-watching.

Reuses the server URL and token from Home Assistant's own Plex integration
(or Frostbox options). Artwork goes through a signed proxy so the Plex token
never reaches the fridge's browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
IMAGE_PATH = "/api/frostbox/plex/image"


class PlexError(Exception):
    """Shown to the user in the Plex screen."""


def _season_episode(item: dict[str, Any]) -> str:
    season, episode = item.get("parentIndex"), item.get("index")
    if season is not None and episode is not None:
        return f"S{season} E{episode}"
    return ""


def group_recent(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split /library/recentlyAdded into movies and (deduplicated) shows."""
    movies: list[dict[str, Any]] = []
    shows: dict[str, dict[str, Any]] = {}
    for item in items:
        kind = item.get("type")
        if kind == "movie":
            movies.append(
                {
                    "key": item.get("ratingKey"),
                    "title": item.get("title"),
                    "year": item.get("year"),
                    "summary": item.get("summary"),
                    "rating": item.get("contentRating"),
                    "duration_min": round((item.get("duration") or 0) / 60000) or None,
                    "thumb": item.get("thumb"),
                    "art": item.get("art"),
                    "added": item.get("addedAt"),
                }
            )
        elif kind in ("episode", "season"):
            show_key = item.get("grandparentRatingKey") if kind == "episode" else item.get("parentRatingKey")
            show_key = str(show_key or item.get("ratingKey"))
            title = item.get("grandparentTitle") if kind == "episode" else item.get("parentTitle")
            entry = shows.get(show_key)
            if entry is None:
                entry = shows[show_key] = {
                    "key": show_key,
                    "title": title or item.get("title"),
                    "thumb": (item.get("grandparentThumb") if kind == "episode" else item.get("parentThumb"))
                    or item.get("thumb"),
                    "art": item.get("grandparentArt") or item.get("art"),
                    "added": item.get("addedAt"),
                    "new_episodes": 0,
                    "latest": None,
                }
            if kind == "episode":
                entry["new_episodes"] += 1
                if entry["latest"] is None:
                    entry["latest"] = f"{_season_episode(item)} · {item.get('title')}".strip(" ·")
            elif entry["latest"] is None:
                entry["latest"] = item.get("title") or "New season"
                entry["new_episodes"] += item.get("leafCount") or 0
    return {"movies": movies, "shows": list(shows.values())}


def continue_watching(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        duration = item.get("duration") or 0
        offset = item.get("viewOffset") or 0
        episode = item.get("type") == "episode"
        out.append(
            {
                "key": item.get("ratingKey"),
                "title": item.get("grandparentTitle") if episode else item.get("title"),
                "subtitle": f"{_season_episode(item)} · {item.get('title')}" if episode else item.get("year"),
                "thumb": item.get("grandparentThumb") if episode else item.get("thumb"),
                "art": item.get("art") or item.get("grandparentArt"),
                "progress": round(offset / duration, 3) if duration else 0,
            }
        )
    return out


class PlexClient:
    def __init__(self, hass: HomeAssistant, url: str | None, token: str | None) -> None:
        self.hass = hass
        self._opt_url = (url or "").rstrip("/") or None
        self._opt_token = token or None
        self._secret = secrets.token_bytes(16)

    def _credentials(self) -> tuple[str, str, bool]:
        if self._opt_url and self._opt_token:
            return self._opt_url, self._opt_token, True
        for entry in self.hass.config_entries.async_entries("plex"):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            cfg = entry.data.get("server_config") or {}
            if cfg.get("url") and cfg.get("token"):
                return str(cfg["url"]).rstrip("/"), cfg["token"], cfg.get("verify_ssl", True)
        raise PlexError("Set up Home Assistant's Plex integration (or add a Plex URL and token in Frostbox options).")

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        url, token, verify = self._credentials()
        session = async_get_clientsession(self.hass, verify_ssl=verify)
        try:
            async with session.get(
                f"{url}{path}",
                params={k: str(v) for k, v in params.items()},
                headers={"Accept": "application/json", "X-Plex-Token": token},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise PlexError("Plex rejected the token")
                if resp.status != 200:
                    raise PlexError(f"Plex answered HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise PlexError(f"Could not reach Plex: {err}") from err
        return data.get("MediaContainer") or {}

    async def whats_new(self, limit: int = 24) -> dict[str, Any]:
        recent = await self._get("/library/recentlyAdded", **{"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 80})
        grouped = group_recent(recent.get("Metadata") or [])
        try:
            deck = continue_watching((await self._get("/library/onDeck")).get("Metadata") or [])
        except PlexError as err:
            _LOGGER.debug("Plex on deck unavailable: %s", err)
            deck = []
        result = {
            "movies": grouped["movies"][:limit],
            "shows": grouped["shows"][:limit],
            "continue": deck[:limit],
        }
        for group in result.values():
            for item in group:
                item["thumb"] = self.image_url(item.get("thumb"), 300, 450)
                item["art"] = self.image_url(item.get("art"), 1280, 720)
        return result

    # --- artwork proxy ------------------------------------------------------

    def sign(self, path: str, width: int, height: int) -> str:
        msg = f"{path}|{width}|{height}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()[:24]

    def image_url(self, path: str | None, width: int, height: int) -> str | None:
        if not path:
            return None
        # Base64 because HA's security filter rejects query values that look like "x=/a/b".
        encoded = base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")
        query = urlencode({"p": encoded, "w": width, "h": height, "sig": self.sign(path, width, height)})
        return f"{IMAGE_PATH}?{query}"

    async def fetch_image(self, path: str, width: int, height: int) -> tuple[bytes, str]:
        url, token, verify = self._credentials()
        session = async_get_clientsession(self.hass, verify_ssl=verify)
        params = {"width": width, "height": height, "minSize": 1, "upscale": 1, "url": path}
        try:
            async with session.get(
                f"{url}/photo/:/transcode",
                params={k: str(v) for k, v in params.items()},
                headers={"X-Plex-Token": token},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    raise PlexError(f"Plex image HTTP {resp.status}")
                return await resp.read(), resp.headers.get("Content-Type", "image/jpeg")
        except (TimeoutError, aiohttp.ClientError) as err:
            raise PlexError(str(err)) from err


class PlexImageView(HomeAssistantView):
    """Artwork proxy; only paths signed by Frostbox are fetched."""

    url = IMAGE_PATH
    name = "api:frostbox:plex_image"
    requires_auth = False

    def __init__(self, client_lookup) -> None:
        self._lookup = client_lookup

    async def get(self, request: web.Request) -> web.Response:
        client: PlexClient | None = self._lookup()
        q = request.query
        try:
            raw = q["p"]
            path = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
            width, height = int(q["w"]), int(q["h"])
        except (KeyError, ValueError, UnicodeDecodeError) as err:
            raise web.HTTPNotFound from err
        if client is None or not hmac.compare_digest(q.get("sig", ""), client.sign(path, width, height)):
            raise web.HTTPNotFound
        try:
            body, content_type = await client.fetch_image(path, width, height)
        except PlexError as err:
            raise web.HTTPNotFound from err
        return web.Response(body=body, content_type=content_type, headers={"Cache-Control": "max-age=86400"})
