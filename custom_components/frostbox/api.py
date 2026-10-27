"""Minimal SmartThings REST client used by Frostbox.

The core SmartThings integration only maps the capabilities that
pysmartthings knows about. Samsung refrigerators expose many extra
``samsungce.*`` capabilities, so Frostbox talks to the REST API directly and
keeps the full raw status around.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from typing import Any

import aiohttp

from .const import API_BASE

_LOGGER = logging.getLogger(__name__)

TokenProvider = Callable[[], Awaitable[str]]

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)


class SmartThingsError(Exception):
    """Generic SmartThings API error."""


class SmartThingsAuthError(SmartThingsError):
    """Token rejected."""


class SmartThingsApi:
    """Thin async wrapper around the SmartThings v1 REST API."""

    def __init__(self, session: aiohttp.ClientSession, token_provider: TokenProvider) -> None:
        self._session = session
        self._token_provider = token_provider

    async def _request(self, method: str, path: str, *, json: Any = None, params: dict[str, str] | None = None) -> Any:
        token = await self._token_provider()
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            async with self._session.request(
                method, url, headers=headers, json=json, params=params, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise SmartThingsAuthError(f"{method} {path}: HTTP {resp.status}")
                if resp.status >= 400:
                    body = await resp.text()
                    raise SmartThingsError(f"{method} {path}: HTTP {resp.status} {body[:300]}")
                if resp.status == 204 or resp.content_length == 0:
                    return None
                return await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise SmartThingsError(f"{method} {path}: {err}") from err

    async def list_devices(self) -> list[dict[str, Any]]:
        """Return every device visible to the token (follows paging)."""
        items: list[dict[str, Any]] = []
        url: str | None = "/devices"
        while url:
            page = await self._request("GET", url)
            items.extend(page.get("items", []))
            url = (page.get("_links") or {}).get("next", {}).get("href") if page else None
        return items

    async def get_device(self, device_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/devices/{device_id}")

    async def get_status(self, device_id: str) -> dict[str, Any]:
        """Return ``{component: {capability: {attribute: {value, unit, timestamp}}}}``."""
        data = await self._request("GET", f"/devices/{device_id}/status")
        return (data or {}).get("components", {})

    async def get_health(self, device_id: str) -> str:
        """Return ONLINE / OFFLINE / UNKNOWN."""
        try:
            data = await self._request("GET", f"/devices/{device_id}/health")
        except SmartThingsAuthError:
            raise
        except SmartThingsError:
            return "UNKNOWN"
        return (data or {}).get("state", "UNKNOWN")

    async def send_command(
        self,
        device_id: str,
        component: str,
        capability: str,
        command: str,
        arguments: list[Any] | None = None,
    ) -> None:
        cmd: dict[str, Any] = {"component": component, "capability": capability, "command": command}
        if arguments:
            cmd["arguments"] = arguments
        _LOGGER.debug("SmartThings command %s/%s.%s(%s)", component, capability, command, arguments)
        await self._request("POST", f"/devices/{device_id}/commands", json={"commands": [cmd]})

    async def create_virtual_switch(self, name: str, location_id: str) -> dict[str, Any]:
        """Create a virtual switch that shows up in SmartThings on the Family Hub screen."""
        return await self._request(
            "POST",
            "/virtualdevices",
            json={
                "name": name,
                "owner": {"ownerType": "LOCATION", "ownerId": location_id},
                "prototype": "VIRTUAL_SWITCH",
            },
        )

    async def fetch_binary(self, url: str, token: str | None = None) -> bytes:
        """Download a file (camera snapshot) with the SmartThings or a custom token."""
        token = token or await self._token_provider()
        try:
            async with self._session.get(
                url, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status >= 400:
                    raise SmartThingsError(f"GET {url}: HTTP {resp.status}")
                return await resp.read()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise SmartThingsError(f"GET {url}: {err}") from err


def is_refrigerator(device: dict[str, Any]) -> bool:
    """Best-effort detection of a Samsung refrigerator in a device listing."""
    ocf = (device.get("ocf") or {}).get("ocfDeviceType", "")
    if "refrigerator" in ocf.lower():
        return True
    for comp in device.get("components", []):
        for cap in comp.get("capabilities", []):
            if cap.get("id") in ("refrigeration", "samsungce.viewInside", "custom.fridgeMode"):
                return True
        for cat in comp.get("categories", []):
            if cat.get("name") == "Refrigerator":
                return True
    return False
