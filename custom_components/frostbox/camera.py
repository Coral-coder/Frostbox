"""Inside-the-fridge cameras, streamable to Apple Home.

The Family Hub cameras produce still photos (taken after a door closes).
HomeKit needs a live video stream, so each Frostbox camera also serves its
latest photo as a steady MJPEG feed and reports an ffmpeg input for it as
its ``stream_source``. HomeKit Bridge passes that straight to ffmpeg, so the
fridge shows up in the Home app like any other camera, with snapshots and
live view, no add-on required.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from pathlib import Path
import secrets
from typing import Any

from homeassistant.components import camera as ha_camera
from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import SmartThingsError
from .const import (
    CONF_CAMERA_REFRESH,
    CONF_CAMERA_SOURCES,
    CONF_VIEW_INSIDE_TOKEN,
    CONF_VIEW_INSIDE_URL,
    DEFAULT_CAMERA_REFRESH,
    DOMAIN,
    MJPEG_FPS,
)
from .coordinator import FrostboxCoordinator
from .entity import FrostboxEntity
from .mjpeg import CAMERAS_KEY, MjpegView
from .models import FrostboxConfigEntry

_LOGGER = logging.getLogger(__name__)

PLACEHOLDER = (Path(__file__).parent / "placeholder.jpg").read_bytes()
# The fridge snaps a photo a few seconds after the door closes; look twice.
AFTER_CLOSE_DELAYS = (20, 90)


async def async_setup_entry(
    hass: HomeAssistant, entry: FrostboxConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    opts = entry.options
    cams: list[FrostboxCamera] = []

    for idx, entity_id in enumerate(opts.get(CONF_CAMERA_SOURCES) or [], start=1):
        cams.append(EntitySourceCamera(coordinator, idx, entity_id))

    if coordinator.data.components_with("samsungce.viewInside") and (opts.get(CONF_VIEW_INSIDE_URL) or not cams):
        areas = sorted({_area(c) for c in coordinator.data.view_inside_contents()}) or ["inside"]
        cams += [ViewInsideCamera(coordinator, area) for area in areas]

    async_add_entities(cams)


def _area(content: dict[str, Any]) -> str:
    area = str(content.get("focusArea") or "")
    return "inside" if area.lower() in ("", "none", "null") else area


class FrostboxCamera(FrostboxEntity, Camera):
    """Caching still camera with an always-on MJPEG feed for HomeKit."""

    _attr_frame_interval = 1.0
    _attr_icon = "mdi:fridge"

    def __init__(self, coordinator: FrostboxCoordinator, key: str, name: str) -> None:
        FrostboxEntity.__init__(self, coordinator, f"camera_{key}")
        Camera.__init__(self)
        self._attr_name = name
        self.camera_key = f"{coordinator.config_entry.entry_id}_{key}"
        self.stream_token = secrets.token_urlsafe(24)
        self._image: bytes | None = None
        self._fetched: datetime | None = None
        self._last_error: str | None = None
        self._refresh_lock = asyncio.Lock()
        self._last_closed_seen: datetime | None = None

    @property
    def available(self) -> bool:
        return True

    @property
    def refresh_interval(self) -> timedelta:
        return timedelta(seconds=self.coordinator.config_entry.options.get(CONF_CAMERA_REFRESH, DEFAULT_CAMERA_REFRESH))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.hass.data.setdefault(DOMAIN, {}).setdefault(CAMERAS_KEY, {})[self.camera_key] = self
        self.async_on_remove(lambda: self.hass.data[DOMAIN][CAMERAS_KEY].pop(self.camera_key, None))
        self.async_on_remove(async_track_time_interval(self.hass, self._interval_refresh, self.refresh_interval))
        self._last_closed_seen = self.coordinator.doors.last_closed
        self.hass.async_create_task(self.async_refresh_image())

    async def _interval_refresh(self, _now: datetime) -> None:
        await self.async_refresh_image()

    @callback
    def _handle_coordinator_update(self) -> None:
        closed = self.coordinator.doors.last_closed
        if closed and closed != self._last_closed_seen:
            self._last_closed_seen = closed
            for delay in AFTER_CLOSE_DELAYS:
                async_call_later(self.hass, delay, self._delayed_refresh)
        super()._handle_coordinator_update()

    async def _delayed_refresh(self, _now: datetime) -> None:
        await self.async_refresh_image(force=True)

    async def _async_fetch(self) -> bytes | None:
        raise NotImplementedError

    async def async_refresh_image(self, force: bool = False) -> None:
        async with self._refresh_lock:
            if not force and self._fetched and dt_util.utcnow() - self._fetched < timedelta(seconds=10):
                return
            try:
                image = await self._async_fetch()
                self._last_error = None
            except (TimeoutError, HomeAssistantError, SmartThingsError) as err:
                image = None
                if str(err) != self._last_error:
                    _LOGGER.warning("%s: could not fetch image: %s", self.entity_id, err)
                self._last_error = str(err)
            self._fetched = dt_util.utcnow()
            if image and image != self._image:
                self._image = image
                self._attr_image_last_updated = dt_util.utcnow()
                if self.hass is not None:
                    self.async_write_ha_state()

    @property
    def latest_image(self) -> bytes:
        return self._image or PLACEHOLDER

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        if self._fetched is None or dt_util.utcnow() - self._fetched > self.refresh_interval:
            await self.async_refresh_image()
        return self.latest_image

    async def stream_source(self) -> str | None:
        """ffmpeg input for HomeKit: our own fixed-rate MJPEG feed on loopback."""
        if self.hass is None:
            return None
        api = self.hass.config.api
        scheme = "https" if api and api.use_ssl else "http"
        port = api.port if api else 8123
        return (
            f"-r {MJPEG_FPS} -f mpjpeg -i "
            f"{scheme}://127.0.0.1:{port}{MjpegView.url_for(self.camera_key)}?token={self.stream_token}"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "has_image": self._image is not None,
            "last_fetch_error": self._last_error,
        }


class EntitySourceCamera(FrostboxCamera):
    """Wraps any HA camera that already gets fridge photos (e.g. a Family Hub integration)."""

    def __init__(self, coordinator: FrostboxCoordinator, index: int, source_entity_id: str) -> None:
        super().__init__(coordinator, f"source_{index}", f"Inside camera {index}")
        self.source_entity_id = source_entity_id

    async def _async_fetch(self) -> bytes | None:
        image = await ha_camera.async_get_image(self.hass, self.source_entity_id, timeout=15)
        return image.content

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {**super().extra_state_attributes, "source": self.source_entity_id}


class ViewInsideCamera(FrostboxCamera):
    """Downloads AI Vision Inside photos referenced by ``samsungce.viewInside``.

    Samsung does not document the download endpoint, so the URL template is
    configurable. Placeholders: ``{file_id}``, ``{device_id}``, ``{focus_area}``.
    """

    def __init__(self, coordinator: FrostboxCoordinator, focus_area: str) -> None:
        name = "Inside camera" if focus_area == "inside" else f"Inside camera {focus_area}"
        super().__init__(coordinator, f"view_inside_{focus_area}".lower(), name)
        self.focus_area = focus_area
        self._file_id: str | None = None

    def _current_file(self) -> dict[str, Any] | None:
        contents = self.coordinator.data.view_inside_contents()
        matching = [c for c in contents if _area(c) == self.focus_area] or contents
        if not matching:
            return None
        return max(matching, key=lambda c: str(c.get("createdTime") or ""))

    @callback
    def _handle_coordinator_update(self) -> None:
        current = self._current_file()
        if current and current.get("fileId") != self._file_id:
            self.hass.async_create_task(self.async_refresh_image(force=True))
        super()._handle_coordinator_update()

    async def _async_fetch(self) -> bytes | None:
        template = self.coordinator.config_entry.options.get(CONF_VIEW_INSIDE_URL)
        current = self._current_file()
        if not template or not current or not current.get("fileId"):
            return None
        url = template.format(
            file_id=current["fileId"], device_id=self.coordinator.device_id, focus_area=self.focus_area
        )
        token = self.coordinator.config_entry.options.get(CONF_VIEW_INSIDE_TOKEN) or None
        data = await self.coordinator.api.fetch_binary(url, token)
        self._file_id = current["fileId"]
        return data

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current = self._current_file()
        attrs = {**super().extra_state_attributes, "focus_area": self.focus_area}
        if current:
            attrs["file_id"] = current.get("fileId")
            attrs["taken"] = current.get("createdTime")
        if not self.coordinator.config_entry.options.get(CONF_VIEW_INSIDE_URL):
            attrs["setup"] = "Set a camera source or the View Inside URL template in Frostbox options"
        return attrs
