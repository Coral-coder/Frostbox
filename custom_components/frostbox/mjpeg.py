"""Fixed-rate MJPEG feed used as the HomeKit input for Frostbox cameras."""

from __future__ import annotations

import asyncio
import hmac

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, MJPEG_FPS

CAMERAS_KEY = "cameras"


class MjpegView(HomeAssistantView):
    """Fixed-rate MJPEG feed, authenticated by a per-camera random token."""

    url = "/api/frostbox/mjpeg/{camera_key}"
    name = "api:frostbox:mjpeg"
    requires_auth = False

    @staticmethod
    def url_for(camera_key: str) -> str:
        return f"/api/frostbox/mjpeg/{camera_key}"

    async def get(self, request: web.Request, camera_key: str) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        cam = hass.data.get(DOMAIN, {}).get(CAMERAS_KEY, {}).get(camera_key)
        token = request.query.get("token", "")
        if cam is None or not hmac.compare_digest(token, cam.stream_token):
            raise web.HTTPNotFound

        resp = web.StreamResponse()
        resp.content_type = "multipart/x-mixed-replace;boundary=frame"
        await resp.prepare(request)
        interval = 1 / MJPEG_FPS
        try:
            while True:
                img = cam.latest_image
                await resp.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(img)).encode()
                    + b"\r\n\r\n"
                    + img
                    + b"\r\n"
                )
                await asyncio.sleep(interval)
        except ConnectionResetError:
            pass  # HomeKit closed the stream
        return resp
