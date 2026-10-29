"""Live TV from an HDHomeRun tuner, transcoded for the fridge's browser.

The HDHomeRun PRIME (CableCARD) streams cable channels as raw MPEG-TS,
usually MPEG-2 video that browsers cannot play. Each watch session runs one
ffmpeg process that turns the tuner stream into short H.264/AAC HLS
segments, served by ``TvHlsView``. Sessions stop on their own once nobody
has fetched a segment for a while, which also frees the tuner.

Channels the cable company copy-protects are flagged ``DRM`` in the tuner's
lineup; the PRIME refuses to stream those to third-party clients, so they are
listed but not playable.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import logging
from pathlib import Path
import secrets
import shutil
import tempfile
import time
from typing import Any

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10)
CLOUD_DISCOVER = "https://ipv4-api.hdhomerun.com/discover"
GUIDE_URL = "https://api.hdhomerun.com/api/guide.php"
GUIDE_TTL = 30 * 60
LINEUP_TTL = 6 * 3600
IDLE_TIMEOUT = 45  # seconds without a playlist/segment request
START_TIMEOUT = 20  # seconds to wait for the first segments
MAX_SESSIONS = 2  # the PRIME has 3 tuners; leave one for the house

QUALITY = {
    "720p": {"height": 720, "maxrate": "4M", "bufsize": "8M"},
    "1080p": {"height": 1080, "maxrate": "8M", "bufsize": "16M"},
    "480p": {"height": 480, "maxrate": "2M", "bufsize": "4M"},
}


class TvError(Exception):
    """Something the TV app should show the user."""


@dataclass
class Channel:
    number: str
    name: str
    url: str
    hd: bool = False
    drm: bool = False
    favorite: bool = False
    codec: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name,
            "hd": self.hd,
            "drm": self.drm,
            "favorite": self.favorite,
            "codec": self.codec,
        }


def parse_lineup(data: list[dict[str, Any]]) -> list[Channel]:
    channels = []
    for item in data or []:
        number = str(item.get("GuideNumber") or "").strip()
        url = item.get("URL")
        if not number or not url:
            continue
        channels.append(
            Channel(
                number=number,
                name=item.get("GuideName") or number,
                url=url,
                hd=bool(item.get("HD")),
                drm=bool(item.get("DRM")),
                favorite=bool(item.get("Favorite")),
                codec=item.get("VideoCodec"),
            )
        )

    def sort_key(ch: Channel) -> tuple:
        parts = ch.number.replace("-", ".").split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts)

    return sorted(channels, key=sort_key)


def parse_guide(data: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{channel_number: {"image": url, "programs": [...]}}`` sorted by start time."""
    guide: dict[str, dict[str, Any]] = {}
    for channel in data or []:
        number = str(channel.get("GuideNumber") or "")
        if not number:
            continue
        programs = []
        for prog in channel.get("Guide") or []:
            start, end = prog.get("StartTime"), prog.get("EndTime")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            programs.append(
                {
                    "start": int(start),
                    "end": int(end),
                    "title": prog.get("Title") or "",
                    "episode": prog.get("EpisodeTitle"),
                    "episode_number": prog.get("EpisodeNumber"),
                    "synopsis": prog.get("Synopsis"),
                    "image": prog.get("ImageURL"),
                }
            )
        programs.sort(key=lambda p: p["start"])
        guide[number] = {"image": channel.get("ImageURL"), "affiliate": channel.get("Affiliate"), "programs": programs}
    return guide


class HdHomeRun:
    """Tuner discovery, lineup and guide (cached)."""

    def __init__(self, hass: HomeAssistant, host: str | None) -> None:
        self.hass = hass
        self.configured_host = (host or "").strip().removeprefix("http://").rstrip("/") or None
        self.host: str | None = self.configured_host
        self.info: dict[str, Any] = {}
        self._lineup: list[Channel] = []
        self._lineup_at = 0.0
        self._guide: dict[str, dict[str, Any]] = {}
        self._guide_at = 0.0
        self._lock = asyncio.Lock()

    async def _get_json(self, url: str, **params: str) -> Any:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, params=params or None, timeout=TIMEOUT) as resp:
                if resp.status != 200:
                    raise TvError(f"{url} answered HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise TvError(f"Could not reach {url}: {err}") from err

    async def _discover(self) -> None:
        """Find the tuner: configured host, then HDHomeRun's cloud discovery, then mDNS name."""
        candidates = [self.configured_host] if self.configured_host else []
        if not candidates:
            try:
                found = await self._get_json(CLOUD_DISCOVER)
                candidates += [d["LocalIP"] for d in found or [] if d.get("LocalIP") and d.get("DeviceID")]
            except TvError as err:
                _LOGGER.debug("HDHomeRun cloud discovery failed: %s", err)
            candidates.append("hdhomerun.local")
        last_error: TvError | None = None
        for host in candidates:
            try:
                self.info = await self._get_json(f"http://{host}/discover.json")
                self.host = host
                return
            except TvError as err:
                last_error = err
        raise TvError(
            "No HDHomeRun found. Set its IP address in Frostbox options." + (f" ({last_error})" if last_error else "")
        )

    async def ensure(self) -> None:
        async with self._lock:
            if not self.info:
                await self._discover()

    async def lineup(self, force: bool = False) -> list[Channel]:
        await self.ensure()
        if force or not self._lineup or time.monotonic() - self._lineup_at > LINEUP_TTL:
            url = self.info.get("LineupURL") or f"http://{self.host}/lineup.json"
            self._lineup = parse_lineup(await self._get_json(url))
            self._lineup_at = time.monotonic()
        return self._lineup

    async def channel(self, wanted: str) -> Channel:
        """Find a channel by number ("4.1") or name ("CNN", "wrc")."""
        lineup = await self.lineup()
        w = str(wanted).strip().lower()
        for match in (
            lambda c: c.number == w,
            lambda c: c.name.lower() == w,
            lambda c: c.name.lower().startswith(w),
            lambda c: w in c.name.lower(),
        ):
            for ch in lineup:
                if match(ch):
                    return ch
        raise TvError(f"Channel {wanted} is not in the lineup")

    async def guide(self) -> dict[str, dict[str, Any]]:
        """Program guide from SiliconDust (the tuner's DeviceAuth rotates, so re-read it)."""
        if self._guide and time.monotonic() - self._guide_at < GUIDE_TTL:
            return self._guide
        await self.ensure()
        with contextlib.suppress(TvError):
            self.info = await self._get_json(f"http://{self.host}/discover.json")
        auth = self.info.get("DeviceAuth")
        if not auth:
            raise TvError("The tuner did not provide a DeviceAuth for the guide")
        self._guide = parse_guide(await self._get_json(GUIDE_URL, DeviceAuth=auth))
        self._guide_at = time.monotonic()
        return self._guide


@dataclass
class TvSession:
    id: str
    channel: Channel
    directory: Path
    process: asyncio.subprocess.Process | None = None
    last_access: float = field(default_factory=time.monotonic)
    started: float = field(default_factory=time.monotonic)

    @property
    def playlist(self) -> Path:
        return self.directory / "index.m3u8"


def ffmpeg_args(source_url: str, out_dir: Path, quality: str) -> list[str]:
    q = QUALITY.get(quality, QUALITY["720p"])
    return [
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts+discardcorrupt",
        "-i",
        source_url,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"yadif=0:-1:0,scale=-2:{q['height']}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-profile:v",
        "main",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "23",
        "-maxrate",
        q["maxrate"],
        "-bufsize",
        q["bufsize"],
        "-g",
        "60",
        "-keyint_min",
        "60",
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "8",
        "-hls_flags",
        "delete_segments+independent_segments+omit_endlist",
        "-hls_segment_filename",
        str(out_dir / "seg%05d.ts"),
        str(out_dir / "index.m3u8"),
    ]


class TvStreamer:
    """Owns the ffmpeg transcode sessions."""

    def __init__(self, hass: HomeAssistant, tuner: HdHomeRun, ffmpeg_binary: str, quality: str) -> None:
        self.hass = hass
        self.tuner = tuner
        self.ffmpeg = ffmpeg_binary
        self.quality = quality
        self.sessions: dict[str, TvSession] = {}
        self._reaper: asyncio.Task | None = None

    async def start(self, number: str, replace: str | None = None) -> TvSession:
        channel = await self.tuner.channel(number)
        if channel.drm:
            raise TvError(f"{channel.name} is copy-protected by your cable provider and can't be streamed")
        if replace:
            await self.stop(replace)
        while len(self.sessions) >= MAX_SESSIONS:
            oldest = min(self.sessions.values(), key=lambda s: s.last_access)
            await self.stop(oldest.id)

        directory = Path(await self.hass.async_add_executor_job(tempfile.mkdtemp, "", "frostbox_tv_"))
        session = TvSession(id=secrets.token_urlsafe(18), channel=channel, directory=directory)
        args = ffmpeg_args(channel.url, directory, self.quality)
        _LOGGER.debug("Starting live TV %s: %s %s", channel.number, self.ffmpeg, " ".join(args))
        try:
            session.process = await asyncio.create_subprocess_exec(
                self.ffmpeg,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as err:
            await self._cleanup(session)
            raise TvError(f"Could not start ffmpeg: {err}") from err
        self.sessions[session.id] = session
        self._ensure_reaper()

        # Wait for a playable playlist (or for ffmpeg to give up).
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if await self.hass.async_add_executor_job(_has_segments, session.playlist):
                return session
            if session.process.returncode is not None:
                err = (await session.process.stderr.read()).decode(errors="replace").strip()[-300:]
                await self.stop(session.id)
                raise TvError(f"The tuner stream failed: {err or 'ffmpeg exited'}")
            await asyncio.sleep(0.4)
        await self.stop(session.id)
        raise TvError("Timed out waiting for the channel. All tuners may be busy.")

    def touch(self, session_id: str) -> TvSession | None:
        session = self.sessions.get(session_id)
        if session:
            session.last_access = time.monotonic()
        return session

    async def stop(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        proc = session.process
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                proc.kill()
        await self._cleanup(session)

    async def stop_all(self) -> None:
        for session_id in list(self.sessions):
            await self.stop(session_id)
        if self._reaper:
            self._reaper.cancel()
            self._reaper = None

    async def _cleanup(self, session: TvSession) -> None:
        await self.hass.async_add_executor_job(shutil.rmtree, session.directory, True)

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = self.hass.async_create_background_task(self._reap(), f"{DOMAIN}_tv_reaper")

    async def _reap(self) -> None:
        while self.sessions:
            await asyncio.sleep(5)
            now = time.monotonic()
            for session in list(self.sessions.values()):
                ended = session.process is not None and session.process.returncode is not None
                if ended or now - session.last_access > IDLE_TIMEOUT:
                    _LOGGER.debug("Stopping idle live TV session on %s", session.channel.number)
                    await self.stop(session.id)


def _has_segments(playlist: Path) -> bool:
    try:
        return playlist.exists() and ".ts" in playlist.read_text()
    except OSError:
        return False


class TvHlsView(HomeAssistantView):
    """Serves a session's playlist and segments. The session id is the credential."""

    url = "/api/frostbox/tv/{session_id}/{filename}"
    name = "api:frostbox:tv"
    requires_auth = False

    def __init__(self, streamer_lookup) -> None:
        self._lookup = streamer_lookup

    async def get(self, request: web.Request, session_id: str, filename: str) -> web.StreamResponse:
        streamer: TvStreamer | None = self._lookup()
        session = streamer.touch(session_id) if streamer else None
        if session is None or "/" in filename or ".." in filename:
            raise web.HTTPNotFound
        path = session.directory / filename
        if filename.endswith(".m3u8"):
            content_type = "application/vnd.apple.mpegurl"
        elif filename.endswith(".ts"):
            content_type = "video/mp2t"
        else:
            raise web.HTTPNotFound
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await hass.async_add_executor_job(path.read_bytes)
        except OSError as err:
            raise web.HTTPNotFound from err
        return web.Response(body=body, content_type=content_type, headers={"Cache-Control": "no-cache"})
