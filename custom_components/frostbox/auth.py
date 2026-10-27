"""Token providers.

The recommended setup reuses the OAuth session of Home Assistant's core
SmartThings integration, so there is nothing extra to register with Samsung.
A personal access token is supported for quick testing (Samsung now expires
those after 24 hours).
"""

from __future__ import annotations

import time

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow

from .api import TokenProvider

CORE_DOMAIN = "smartthings"


def pat_provider(token: str) -> TokenProvider:
    async def _provide() -> str:
        return token

    return _provide


def core_provider(hass: HomeAssistant, core_entry_id: str) -> TokenProvider:
    """Borrow the access token from the core SmartThings config entry.

    We prefer the core integration's own refresh function so only one
    OAuth2Session ever refreshes the (rotating) refresh token.
    """

    session: config_entry_oauth2_flow.OAuth2Session | None = None

    async def _provide() -> str:
        nonlocal session
        entry = hass.config_entries.async_get_entry(core_entry_id)
        if entry is None:
            raise ConfigEntryAuthFailed("The SmartThings integration was removed")
        if entry.state is not ConfigEntryState.LOADED:
            raise ConfigEntryNotReady("Waiting for the SmartThings integration to load")

        client = getattr(getattr(entry, "runtime_data", None), "client", None)
        refresh = getattr(client, "refresh_token_function", None)
        if refresh is not None:
            return await refresh()

        token = entry.data.get("token", {})
        if token.get("access_token") and token.get("expires_at", 0) > time.time() + 60:
            return token["access_token"]

        if session is None:
            impl = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
            session = config_entry_oauth2_flow.OAuth2Session(hass, entry, impl)
        await session.async_ensure_token_valid()
        return session.token["access_token"]

    return _provide
