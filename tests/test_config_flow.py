"""Config flow."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.frostbox.api import SmartThingsAuthError
from custom_components.frostbox.const import AUTH_PAT, CONF_AUTH_TYPE, CONF_DEVICE_ID, DOMAIN


async def test_pat_flow(hass: HomeAssistant, fake_api) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "pat"})
    assert result["step_id"] == "pat"
    with (
        patch("custom_components.frostbox.config_flow.SmartThingsApi", return_value=fake_api),
        patch("custom_components.frostbox.build_api", return_value=fake_api),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": " abc "})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Family Hub"
    assert result["data"][CONF_AUTH_TYPE] == AUTH_PAT
    assert result["data"][CONF_DEVICE_ID] == "fridge-1234"


async def test_pat_flow_bad_token(hass: HomeAssistant, fake_api) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "pat"})

    async def _deny():
        raise SmartThingsAuthError("401")

    fake_api.list_devices = _deny
    with patch("custom_components.frostbox.config_flow.SmartThingsApi", return_value=fake_api):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": "bad"})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_core_flow_requires_smartthings(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "core"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "core_missing"


async def test_core_token_provider_uses_core_refresh(hass: HomeAssistant) -> None:
    """Frostbox borrows the core SmartThings integration's (single) token refresher."""
    from types import SimpleNamespace

    from homeassistant.config_entries import ConfigEntryState
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.frostbox.auth import core_provider

    calls = []

    async def _refresh() -> str:
        calls.append(1)
        return "core-token"

    core = MockConfigEntry(domain="smartthings", data={"token": {}}, state=ConfigEntryState.LOADED)
    core.runtime_data = SimpleNamespace(client=SimpleNamespace(refresh_token_function=_refresh))
    provider = core_provider(hass, core.entry_id)
    with patch.object(hass.config_entries, "async_get_entry", return_value=core):
        assert await provider() == "core-token"
    assert calls == [1]
