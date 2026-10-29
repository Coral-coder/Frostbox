"""End-to-end: set up Frostbox against a fake SmartThings and drive it."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.camera import async_get_stream_source
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.frostbox.const import DOMAIN

FRIDGE = "fridge-1234"


def _eid(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{FRIDGE}_{key}")
    assert entity_id, f"missing {platform} {key}"
    return entity_id


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float = 21) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_entities_created(hass: HomeAssistant, setup_fridge) -> None:
    assert float(hass.states.get(_eid(hass, "sensor", "cooler_temperature")).state) == 37
    assert float(hass.states.get(_eid(hass, "sensor", "freezer_temperature")).state) == 0
    assert hass.states.get(_eid(hass, "sensor", "energy")).state == "123.456"
    assert hass.states.get(_eid(hass, "sensor", "main_custom.waterFilter_waterFilterUsage")).state == "42.0"
    assert hass.states.get(_eid(hass, "binary_sensor", "cooler_door")).state == STATE_OFF
    assert hass.states.get(_eid(hass, "binary_sensor", "water_filter_replace")).state == STATE_OFF
    assert hass.states.get(_eid(hass, "switch", "power_cool")).state == STATE_OFF
    assert hass.states.get(_eid(hass, "switch", "icemaker_switch")).state == STATE_ON
    assert hass.states.get(_eid(hass, "number", "cooler_setpoint")).state == "37.0"
    assert hass.states.get(_eid(hass, "select", "cvroom_mode")).state == "Deli"
    assert hass.states.get(_eid(hass, "sensor", "food_safety")).state == "ok"
    assert hass.states.get(_eid(hass, "todo", "inventory_todo")).state == "0"
    assert hass.states.get(_eid(hass, "camera", "camera_view_inside_inside"))


async def test_controls_send_commands(hass: HomeAssistant, setup_fridge, fake_api) -> None:
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _eid(hass, "switch", "power_cool")}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": _eid(hass, "number", "freezer_setpoint"), "value": -2}, blocking=True
    )
    await hass.services.async_call(
        "select", "select_option", {"entity_id": _eid(hass, "select", "cvroom_mode"), "option": "Wine"}, blocking=True
    )
    sent = [(c[1], c[2], c[3], c[4]) for c in fake_api.commands]
    assert ("main", "samsungce.powerCool", "activate", None) in sent
    assert ("freezer", "thermostatCoolingSetpoint", "setCoolingSetpoint", [-2]) in sent
    assert ("cvroom", "custom.fridgeMode", "setFridgeMode", ["CV_FDR_WINE"]) in sent


async def test_door_ajar_fires_after_threshold(
    hass: HomeAssistant, setup_fridge, fake_api, freezer: FrozenDateTimeFactory
) -> None:
    events = []
    hass.bus.async_listen("frostbox_door", events.append)
    fake_api.set("cooler", "contactSensor", "contact", "open")
    await _poll(hass, freezer)
    ajar = _eid(hass, "binary_sensor", "door_ajar")
    assert hass.states.get(_eid(hass, "binary_sensor", "cooler_door")).state == STATE_ON
    assert hass.states.get(ajar).state == STATE_OFF
    assert events[-1].data["state"] == "open"
    await _poll(hass, freezer, 45)
    await _poll(hass, freezer, 20)
    assert hass.states.get(ajar).state == STATE_ON
    fake_api.set("cooler", "contactSensor", "contact", "closed")
    await _poll(hass, freezer)
    assert hass.states.get(ajar).state == STATE_OFF
    assert hass.states.get(_eid(hass, "sensor", "door_opens_today")).state == "1"


async def test_party_mode_and_restore(
    hass: HomeAssistant, setup_fridge, fake_api, freezer: FrozenDateTimeFactory
) -> None:
    resp = await hass.services.async_call(
        DOMAIN, "activate_mode", {"mode": "party", "hours": 1}, blocking=True, return_response=True
    )
    assert "Party mode is on" in resp["speech"]
    await _poll(hass, freezer, 2)
    assert fake_api.status["main"]["samsungce.powerCool"]["activated"]["value"] is True
    assert fake_api.status["main"]["samsungce.powerFreeze"]["activated"]["value"] is True
    assert fake_api.status["cvroom"]["custom.fridgeMode"]["fridgeMode"]["value"] == "CV_FDR_BEVERAGE"
    assert hass.states.get(_eid(hass, "switch", "mode_party")).state == STATE_ON

    freezer.tick(timedelta(hours=1, seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, "switch", "mode_party")).state == STATE_OFF
    assert fake_api.status["main"]["samsungce.powerCool"]["activated"]["value"] is False
    assert fake_api.status["cvroom"]["custom.fridgeMode"]["fridgeMode"]["value"] == "CV_FDR_DELI"


async def test_inventory_services(hass: HomeAssistant, setup_fridge) -> None:
    hass.states.async_set("todo.shopping_list", "0")
    added = []

    async def _todo_add(call):
        added.append(call.data["item"])

    hass.services.async_register("todo", "add_item", _todo_add)

    await hass.services.async_call(
        DOMAIN, "add_item", {"name": "Milk", "quantity": 2, "staple": True, "min_quantity": 1}, blocking=True
    )
    resp = await hass.services.async_call(
        DOMAIN, "add_item", {"name": "Strawberries"}, blocking=True, return_response=True
    )
    assert "good for 4 more days" in resp["speech"]
    resp = await hass.services.async_call(DOMAIN, "find_item", {"query": "milks"}, blocking=True, return_response=True)
    assert resp["found"] and resp["speech"].startswith("Yes, you have 2 Milk")
    resp = await hass.services.async_call(DOMAIN, "consume_item", {"name": "milk"}, blocking=True, return_response=True)
    assert "shopping list" in resp["speech"]
    assert added == ["Milk"]
    resp = await hass.services.async_call(
        DOMAIN, "add_leftover", {"name": "lasagna", "days": 1}, blocking=True, return_response=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, "sensor", "expiring_soon")).state == "1"
    assert hass.states.get(_eid(hass, "todo", "inventory_todo")).state == "3"
    status = await hass.services.async_call(DOMAIN, "get_status", {}, blocking=True, return_response=True)
    assert status["fridge_f"] == 37
    assert "Leftover lasagna" in status["speech"]


async def test_barcode_lookup(hass: HomeAssistant, setup_fridge) -> None:
    product = {
        "name": "Chobani Greek Yogurt",
        "brand": "Chobani",
        "quantity": "5.3 oz",
        "category_tags": ["en:dairies", "en:yogurts"],
    }
    with patch("custom_components.frostbox.services.lookup_barcode", return_value=product):
        resp = await hass.services.async_call(
            DOMAIN, "add_item", {"barcode": "818290011824"}, blocking=True, return_response=True
        )
    assert resp["item"]["name"] == "Chobani Greek Yogurt"
    assert resp["item"]["category"] == "yogurt"
    with patch("custom_components.frostbox.services.lookup_barcode", return_value=None):
        resp = await hass.services.async_call(
            DOMAIN, "add_item", {"barcode": "000"}, blocking=True, return_response=True
        )
    assert resp["recognized"] is False


async def test_todo_complete_consumes(hass: HomeAssistant, setup_fridge) -> None:
    todo = _eid(hass, "todo", "inventory_todo")
    await hass.services.async_call("todo", "add_item", {"entity_id": todo, "item": "Eggs"}, blocking=True)
    assert hass.states.get(todo).state == "1"
    await hass.services.async_call(
        "todo", "update_item", {"entity_id": todo, "item": "Eggs", "status": "completed"}, blocking=True
    )
    assert hass.states.get(todo).state == "0"


async def test_food_safety_unsafe_after_outage(
    hass: HomeAssistant, setup_fridge, fake_api, freezer: FrozenDateTimeFactory
) -> None:
    fake_api.health = "OFFLINE"
    await _poll(hass, freezer)
    freezer.tick(timedelta(hours=5))
    fake_api.health = "ONLINE"
    fake_api.set("cooler", "temperatureMeasurement", "temperature", 49)
    await _poll(hass, freezer)
    assert hass.states.get(_eid(hass, "sensor", "food_safety")).state == "unsafe"
    assert hass.states.get(_eid(hass, "binary_sensor", "food_safety_alert")).state == STATE_ON
    await hass.services.async_call(
        "button", "press", {"entity_id": _eid(hass, "button", "ack_food_safety")}, blocking=True
    )
    # Still warm, so it drops to "warning" rather than "ok".
    assert hass.states.get(_eid(hass, "sensor", "food_safety")).state == "warning"


async def test_camera_stream_for_homekit(hass: HomeAssistant, setup_fridge, hass_client_no_auth) -> None:
    cam = _eid(hass, "camera", "camera_view_inside_inside")
    source = await async_get_stream_source(hass, cam)
    assert source.startswith("-r 10 -f mpjpeg -i http://127.0.0.1:")
    path = source.split("127.0.0.1:", 1)[1].split("/", 1)[1]
    client = await hass_client_no_auth()
    bad = await client.get("/" + path.split("?")[0] + "?token=nope")
    assert bad.status == 404
    resp = await client.get("/" + path)
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("multipart/x-mixed-replace")
    chunk = await resp.content.read(64)
    assert chunk.startswith(b"--frame\r\nContent-Type: image/jpeg")
    resp.close()


async def test_fridge_screen_switches(
    hass: HomeAssistant, fake_api, mock_entry, freezer: FrozenDateTimeFactory
) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={"fridge_screen_switches": True})
    with patch("custom_components.frostbox.build_api", return_value=fake_api):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        labels = sorted(v["label"] for v in fake_api.virtual.values())
        assert labels == [
            "Frostbox Dinner Ready",
            "Frostbox Grocery Mode",
            "Frostbox Party Mode",
            "Frostbox Vacation Mode",
        ]
        await _poll(hass, freezer)  # first sight of each switch

        dinner = []
        hass.bus.async_listen("frostbox_dinner_ready", dinner.append)
        party_vs = next(k for k, v in fake_api.virtual.items() if v["label"] == "Frostbox Party Mode")
        dinner_vs = next(k for k, v in fake_api.virtual.items() if v["label"] == "Frostbox Dinner Ready")
        fake_api.virtual[party_vs]["switch"] = "on"  # tapped on the fridge screen
        fake_api.virtual[dinner_vs]["switch"] = "on"
        await _poll(hass, freezer)
        assert hass.states.get(_eid(hass, "switch", "mode_party")).state == STATE_ON
        assert len(dinner) == 1 and dinner[0].data["source"] == "fridge"
        assert fake_api.virtual[dinner_vs]["switch"] == "off"  # momentary

        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": _eid(hass, "switch", "mode_party")}, blocking=True
        )
        assert fake_api.virtual[party_vs]["switch"] == "off"


async def test_screensaver_entities_and_pages(hass: HomeAssistant, setup_fridge, hass_client_no_auth) -> None:
    select = _eid(hass, "select", "screensaver")
    assert hass.states.get(select).state == "Aquarium"
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Matrix"}, blocking=True)
    assert hass.states.get(select).state == "Matrix"

    events = []
    hass.bus.async_listen("frostbox_screensaver", events.append)
    await hass.services.async_call(
        "button", "press", {"entity_id": _eid(hass, "button", "start_screensaver")}, blocking=True
    )
    await hass.async_block_till_done()
    assert events[0].data == {"action": "start"}

    client = await hass_client_no_auth()
    for page, marker in (("kiosk", "frostbox-wake"), ("matrix", "<canvas"), ("aquarium", "<canvas")):
        resp = await client.get(f"/frostbox/{page}.html")
        assert resp.status == 200, page
        assert marker in await resp.text()


async def test_disabled_components_are_skipped(hass: HomeAssistant, setup_fridge) -> None:
    """Samsung ships regional features switched off; Frostbox must not expose them."""
    reg = er.async_get(hass)
    ids = {e.unique_id for e in er.async_entries_for_config_entry(reg, setup_fridge.entry_id)}
    assert f"{FRIDGE}_icemaker-03_switch" in ids
    assert not any("curdmaker" in i or "scale-10" in i or "meatAging" in i for i in ids)


async def test_dump_capabilities_includes_definitions(hass: HomeAssistant, setup_fridge) -> None:
    resp = await hass.services.async_call(DOMAIN, "dump_capabilities", {}, blocking=True, return_response=True)
    assert resp["disabled_components"] == ["curdmaker", "scale-10", "scale-11"]
    assert resp["definitions_fetched"] > 0
    import json
    from pathlib import Path

    dump = json.loads(await hass.async_add_executor_job(Path(resp["path"]).read_text))
    assert "error" in dump["definitions"]["custom.waterFilter"]
    assert dump["definitions"]["samsungce.powerCool"]["id"] == "samsungce.powerCool"
    assert dump["disabled_capabilities"]["main"] == ["samsungce.meatAging"]
    summary = resp["summary"]
    assert summary["samsungce.fridgeVacationMode"] == {
        "values": {"main": {"vacationMode": "off"}},
        "commands": {"setVacationMode": "mode: on|off"},
    }
    assert "samsungce.meatAging" not in summary  # unavailable on this model
    assert "samsungce.weightMeasurement" not in summary  # scale component is disabled
    assert "contactSensor" not in summary  # already mapped


async def test_samsung_features_from_real_dump(hass: HomeAssistant, setup_fridge, fake_api) -> None:
    """Controls mapped from the real RF90F29AEWAA capability summary."""
    assert hass.states.get(_eid(hass, "switch", "autofill_pitcher")).state == STATE_ON
    assert hass.states.get(_eid(hass, "switch", "auto_open_door")).state == STATE_ON
    assert hass.states.get(_eid(hass, "switch", "door_alarm")).state == STATE_ON
    assert hass.states.get(_eid(hass, "switch", "ai_precise_cooling")).state == STATE_OFF
    assert hass.states.get(_eid(hass, "switch", "icemaker_night_mode")).state == STATE_OFF
    assert hass.states.get(_eid(hass, "switch", "lights_brighten_gradually")).state == STATE_ON
    reg = er.async_get(hass)
    # Reported as null on this model: no entity.
    assert reg.async_get_entity_id("switch", DOMAIN, f"{FRIDGE}_interior_lighting") is None
    assert reg.async_get_entity_id("switch", DOMAIN, f"{FRIDGE}_night_light") is None

    assert (
        hass.states.get(_eid(hass, "sensor", "icemaker_samsungce.fridgeIcemakerOperatingState_operatingState")).state
        == "running"
    )
    assert (
        float(hass.states.get(_eid(hass, "sensor", "main_samsungce.energyManagement_thisMonthEnergyConsumption")).state)
        == 2.432
    )
    assert hass.states.get(_eid(hass, "binary_sensor", "software_update")).state == STATE_OFF

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": _eid(hass, "switch", "autofill_pitcher")}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _eid(hass, "switch", "defrost_delay")}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": _eid(hass, "button", "refresh_inside_photos")}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": _eid(hass, "button", "refresh_food_list")}, blocking=True
    )
    sent = [(c[1], c[2], c[3], c[4]) for c in fake_api.commands]
    assert ("main", "samsungce.autoFillPitcher", "off", None) in sent
    assert ("main", "samsungce.defrostDelay", "setDefrostDelay", ["on"]) in sent
    assert ("main", "samsungce.viewInside", "refreshAll", None) in sent
    food = [c for c in sent if c[1] == "samsungce.fridgeFoodList"]
    assert food and food[0][2] == "refresh" and len(food[0][3][0]) == 32


async def test_shipped_configs_reference_real_entities(hass: HomeAssistant, setup_fridge) -> None:
    """Every family_hub_* entity in the dashboard and HomeKit package must exist."""
    from pathlib import Path
    import re

    root = Path(__file__).parent.parent / "homeassistant"
    files = [root / "dashboards/frostbox_fridge.yaml", root / "packages/frostbox_homekit.yaml"]
    text = "\n".join([await hass.async_add_executor_job(f.read_text) for f in files])
    referenced = set(re.findall(r"\b[a-z_]+\.family_hub_[a-z0-9_]+", text))
    existing = {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), setup_fridge.entry_id)}
    assert referenced, "no entity references found"
    assert referenced - existing == set()
