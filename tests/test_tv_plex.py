"""Live TV (HDHomeRun PRIME) and Plex "what's new"."""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.frostbox.hdhomerun import parse_guide, parse_lineup
from custom_components.frostbox.plex import continue_watching, group_recent

TUNER = "10.0.0.50"
PLEX = "http://10.0.0.60:32400"

LINEUP = [
    {"GuideNumber": "704", "GuideName": "HBOHD", "HD": 1, "DRM": 1, "URL": f"http://{TUNER}:5004/auto/v704"},
    {"GuideNumber": "4.1", "GuideName": "WRC-HD", "HD": 1, "Favorite": 1, "URL": "SET_BY_TEST"},
    {"GuideNumber": "10", "GuideName": "CNN", "URL": f"http://{TUNER}:5004/auto/v10"},
]


def test_parse_lineup_sorts_and_flags_drm() -> None:
    channels = parse_lineup(LINEUP)
    assert [c.number for c in channels] == ["4.1", "10", "704"]
    assert channels[2].drm and channels[0].favorite and channels[0].hd


def test_parse_guide() -> None:
    guide = parse_guide(
        [
            {
                "GuideNumber": "10",
                "ImageURL": "logo.png",
                "Guide": [
                    {"StartTime": 200, "EndTime": 300, "Title": "B"},
                    {"StartTime": 100, "EndTime": 200, "Title": "A", "EpisodeTitle": "Pilot", "Synopsis": "x"},
                    {"StartTime": "bad", "EndTime": 1, "Title": "skip"},
                ],
            }
        ]
    )
    assert [p["title"] for p in guide["10"]["programs"]] == ["A", "B"]
    assert guide["10"]["programs"][0]["episode"] == "Pilot"


def test_plex_grouping() -> None:
    items = [
        {
            "type": "episode",
            "grandparentRatingKey": 9,
            "grandparentTitle": "Severance",
            "parentIndex": 2,
            "index": 3,
            "title": "Who Is Alive?",
            "grandparentThumb": "/t/9",
            "addedAt": 3,
        },
        {
            "type": "episode",
            "grandparentRatingKey": 9,
            "grandparentTitle": "Severance",
            "parentIndex": 2,
            "index": 2,
            "title": "Goodbye",
            "addedAt": 2,
        },
        {"type": "movie", "ratingKey": 5, "title": "Dune", "year": 2024, "duration": 9960000, "thumb": "/t/5"},
        {"type": "season", "parentRatingKey": 7, "parentTitle": "Bluey", "title": "Season 3", "leafCount": 10},
    ]
    grouped = group_recent(items)
    assert [m["title"] for m in grouped["movies"]] == ["Dune"]
    assert grouped["movies"][0]["duration_min"] == 166
    sev = grouped["shows"][0]
    assert sev["title"] == "Severance" and sev["new_episodes"] == 2 and sev["latest"] == "S2 E3 · Who Is Alive?"
    assert grouped["shows"][1] == {**grouped["shows"][1], "title": "Bluey", "latest": "Season 3", "new_episodes": 10}
    deck = continue_watching(
        [
            {
                "type": "episode",
                "grandparentTitle": "Severance",
                "parentIndex": 1,
                "index": 1,
                "title": "Good News",
                "duration": 1000,
                "viewOffset": 250,
            }
        ]
    )
    assert deck[0]["progress"] == 0.25 and deck[0]["subtitle"] == "S1 E1 · Good News"


@pytest.fixture
def broadcast_clip(tmp_path: Path) -> Path:
    """A few seconds of 480i MPEG-2 + AC-3 in MPEG-TS, like a cable channel from the PRIME."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    clip = tmp_path / "cable.ts"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=720x480:rate=30000/1001",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "8",
            "-c:v",
            "mpeg2video",
            "-flags",
            "+ilme+ildct",
            "-c:a",
            "ac3",
            "-f",
            "mpegts",
            str(clip),
        ],
        check=True,
    )
    return clip


@pytest.fixture
async def frostbox_media(hass: HomeAssistant, fake_api, aioclient_mock, broadcast_clip):
    hass.config.units = US_CUSTOMARY_SYSTEM
    lineup = [dict(c) for c in LINEUP]
    lineup[1]["URL"] = str(broadcast_clip)  # ffmpeg reads the clip as if it were the tuner
    aioclient_mock.get(
        f"http://{TUNER}/discover.json",
        json={
            "FriendlyName": "HDHomeRun PRIME",
            "ModelNumber": "HDHR3-CC",
            "TunerCount": 3,
            "DeviceAuth": "abc",
            "LineupURL": f"http://{TUNER}/lineup.json",
        },
    )
    aioclient_mock.get(f"http://{TUNER}/lineup.json", json=lineup)
    aioclient_mock.get(
        "https://api.hdhomerun.com/api/guide.php",
        json=[
            {
                "GuideNumber": "4.1",
                "ImageURL": "https://img/wrc.png",
                "Guide": [{"StartTime": 1, "EndTime": 4102444800, "Title": "News 4 at 6"}],
            }
        ],
    )
    aioclient_mock.get(
        f"{PLEX}/library/recentlyAdded",
        json={
            "MediaContainer": {
                "Metadata": [{"type": "movie", "ratingKey": 5, "title": "Dune", "thumb": "/t/5", "art": "/a/5"}]
            }
        },
    )
    aioclient_mock.get(f"{PLEX}/library/onDeck", json={"MediaContainer": {"Metadata": []}})
    aioclient_mock.get(f"{PLEX}/photo/:/transcode", content=b"\xff\xd8poster", headers={"Content-Type": "image/jpeg"})

    entry = MockConfigEntry(
        domain="frostbox",
        unique_id="fridge-1234",
        data={"auth_type": "personal_access_token", "token": "pat", "device_id": "fridge-1234", "location_id": "loc-1"},
        options={"hdhomerun_host": TUNER, "tv_quality": "480p", "plex_url": PLEX, "plex_token": "plextoken"},
    )
    with patch("custom_components.frostbox.build_api", return_value=fake_api):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry
        await hass.config_entries.async_unload(entry.entry_id)


async def test_tv_guide_and_live_stream(
    hass: HomeAssistant, frostbox_media, hass_ws_client, hass_client_no_auth
) -> None:
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "frostbox/tv/channels"})
    res = await ws.receive_json()
    assert res["success"], res
    assert res["result"]["tuner"]["model"] == "HDHR3-CC"
    assert [c["number"] for c in res["result"]["channels"]] == ["4.1", "10", "704"]

    await ws.send_json({"id": 2, "type": "frostbox/tv/guide", "hours": 4})
    res = await ws.receive_json()
    rows = {r["number"]: r for r in res["result"]["channels"]}
    assert rows["4.1"]["programs"][0]["title"] == "News 4 at 6"
    assert rows["4.1"]["image"] == "https://img/wrc.png"
    assert res["result"]["guide_error"] is None

    # Copy-protected channel: refused with a clear message.
    await ws.send_json({"id": 3, "type": "frostbox/tv/watch", "channel": "704"})
    res = await ws.receive_json()
    assert not res["success"] and "copy-protected" in res["error"]["message"]

    # Real ffmpeg: MPEG-2/AC-3 in, browser-friendly HLS out.
    await ws.send_json({"id": 4, "type": "frostbox/tv/watch", "channel": "4.1"})
    res = await ws.receive_json()
    assert res["success"], res
    url = res["result"]["url"]
    client = await hass_client_no_auth()
    playlist = await client.get(url)
    assert playlist.status == 200
    text = await playlist.text()
    assert "#EXTM3U" in text
    segment = next(line for line in text.splitlines() if line.endswith(".ts"))
    seg = await client.get(url.rsplit("/", 1)[0] + "/" + segment)
    assert seg.status == 200 and len(await seg.read()) > 1000
    assert (await client.get(url.replace(res["result"]["session"], "wrong"))).status == 404

    await ws.send_json({"id": 5, "type": "frostbox/tv/stop", "session": res["result"]["session"]})
    assert (await ws.receive_json())["success"]
    assert (await client.get(url)).status == 404


async def test_hls_output_codecs(
    hass: HomeAssistant, frostbox_media, hass_ws_client, hass_client_no_auth, tmp_path
) -> None:
    """The segments must be H.264 + AAC (what browsers and hls.js play)."""
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "frostbox/tv/watch", "channel": "WRC-HD"})
    res = await ws.receive_json()
    assert res["success"], res  # channels can be picked by name too
    data = frostbox_media.runtime_data
    session = data.tv.streamer.sessions[res["result"]["session"]]
    seg = sorted(session.directory.glob("*.ts"))[0]
    probe = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "csv=p=0",
        str(seg),
        stdout=asyncio.subprocess.PIPE,
    )
    out, _ = await probe.communicate()
    assert set(out.decode().split()) == {"h264", "aac"}


async def test_plex_whats_new_and_artwork(
    hass: HomeAssistant, frostbox_media, hass_ws_client, hass_client_no_auth
) -> None:
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "frostbox/plex/new"})
    res = await ws.receive_json()
    assert res["success"], res
    movie = res["result"]["movies"][0]
    assert movie["title"] == "Dune"
    assert "plextoken" not in movie["thumb"]  # token never reaches the browser
    client = await hass_client_no_auth()
    img = await client.get(movie["thumb"])
    assert img.status == 200 and await img.read() == b"\xff\xd8poster"
    import base64
    import re

    forged = re.sub(r"p=[^&]+", "p=" + base64.urlsafe_b64encode(b"/admin").decode().rstrip("="), movie["thumb"])
    assert forged != movie["thumb"]
    assert (await client.get(forged)).status == 404  # signature no longer matches


async def test_show_on_fridge_event(hass: HomeAssistant, frostbox_media) -> None:
    events = []
    hass.bus.async_listen("frostbox_kiosk_app", events.append)
    await hass.services.async_call("frostbox", "show_on_fridge", {"app": "tv", "channel": "CNN"}, blocking=True)
    await hass.services.async_call("frostbox", "show_on_fridge", {"app": "plex", "channel": ""}, blocking=True)
    await hass.async_block_till_done()
    received = [e.data for e in events]
    assert len(received) == 2
    assert {"app": "tv", "channel": "CNN"} in received and {"app": "plex"} in received
