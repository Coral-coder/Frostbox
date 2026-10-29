"""Runtime data stored on the config entry."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .coordinator import FrostboxCoordinator
from .hdhomerun import HdHomeRun, TvStreamer
from .inventory import Inventory
from .plex import PlexClient


@dataclass
class TvMedia:
    tuner: HdHomeRun
    streamer: TvStreamer


@dataclass
class FrostboxData:
    coordinator: FrostboxCoordinator
    inventory: Inventory
    tv: TvMedia | None = None
    plex: PlexClient | None = None


type FrostboxConfigEntry = ConfigEntry[FrostboxData]
