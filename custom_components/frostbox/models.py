"""Runtime data stored on the config entry."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .coordinator import FrostboxCoordinator
from .inventory import Inventory


@dataclass
class FrostboxData:
    coordinator: FrostboxCoordinator
    inventory: Inventory


type FrostboxConfigEntry = ConfigEntry[FrostboxData]
