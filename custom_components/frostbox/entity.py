"""Base entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_MODEL, DOMAIN, MANUFACTURER
from .coordinator import FrostboxCoordinator


def device_info(coordinator: FrostboxCoordinator) -> DeviceInfo:
    dev = coordinator.device
    ocf = dev.get("ocf") or {}
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.device_id)},
        name=dev.get("label") or dev.get("name") or "Family Hub",
        manufacturer=MANUFACTURER,
        model=ocf.get("modelNumber", "").split("|")[0] or DEFAULT_MODEL,
        sw_version=ocf.get("firmwareVersion"),
    )


class FrostboxEntity(CoordinatorEntity[FrostboxCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FrostboxCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_{key}"
        self._attr_device_info = device_info(coordinator)
