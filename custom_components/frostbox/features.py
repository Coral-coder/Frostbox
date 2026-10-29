"""Samsung ``samsungce.*`` features mapped from the real RF90F29AEWAA capability dump.

Each entry only becomes an entity when the fridge reports the attribute with
a value; features Samsung ships but disables (or reports as null) are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.const import EntityCategory


@dataclass(frozen=True)
class OnOffFeature:
    """An ``on``/``off`` attribute. ``setter`` names a one-argument command;
    without it the capability has bare ``on``/``off`` commands."""

    key: str
    capability: str
    attribute: str
    name: str
    icon: str
    setter: str | None = None
    category: EntityCategory | None = None


ON_OFF_FEATURES: tuple[OnOffFeature, ...] = (
    OnOffFeature(
        "autofill_pitcher", "samsungce.autoFillPitcher", "autoFillPitcher", "AutoFill pitcher", "mdi:cup-water"
    ),
    OnOffFeature("auto_open_door", "samsungce.autoOpenDoor", "autoOpenDoor", "Auto Open Door", "mdi:door-open"),
    OnOffFeature("door_alarm", "samsungce.doorAlarm", "doorAlarm", "Door alarm", "mdi:bell-ring-outline"),
    OnOffFeature(
        "ai_precise_cooling", "samsungce.aiPreciseCooling", "aiPreciseCooling", "AI precise cooling", "mdi:brain"
    ),
    OnOffFeature(
        "icemaker_night_mode",
        "samsungce.icemakerNightMode",
        "icemakerNightMode",
        "Ice maker night mode",
        "mdi:weather-night",
    ),
    OnOffFeature("sabbath_mode", "samsungce.sabbathMode", "status", "Sabbath mode", "mdi:candle"),
    OnOffFeature("uv_deodorizing", "samsungce.uvDeodorizing", "uvDeodorizing", "UV deodorizing", "mdi:air-filter"),
    OnOffFeature(
        "defrost_delay",
        "samsungce.defrostDelay",
        "defrostDelay",
        "Defrost delay (peak hours)",
        "mdi:clock-time-four-outline",
        setter="setDefrostDelay",
        category=EntityCategory.CONFIG,
    ),
    OnOffFeature(
        "lights_brighten_gradually",
        "samsungce.fridgeInteriorLighting",
        "brightenGradually",
        "Lights brighten gradually",
        "mdi:lightbulb-on-outline",
        setter="setBrightenGradually",
        category=EntityCategory.CONFIG,
    ),
    OnOffFeature(
        "interior_lighting",
        "samsungce.fridgeInteriorLighting",
        "interiorLighting",
        "Interior lighting",
        "mdi:lightbulb",
        setter="setInteriorLighting",
    ),
    OnOffFeature(
        "night_light",
        "samsungce.fridgeInteriorLighting",
        "nightLight",
        "Night light",
        "mdi:lightbulb-night-outline",
        setter="setNightLight",
    ),
)

# Attributes covered by the entities above or in sensor.py/binary_sensor.py/button.py,
# so they don't also show up as raw diagnostic sensors.
MAPPED_ATTRIBUTES: set[tuple[str, str]] = {(f.capability, f.attribute) for f in ON_OFF_FEATURES} | {
    ("samsungce.fridgeIcemakerOperatingState", "operatingState"),
    ("samsungce.energyManagement", "thisMonthEnergyConsumption"),
    ("samsungce.energyManagement", "lastMonthEnergyConsumption"),
    ("samsungce.softwareUpdate", "newVersionAvailable"),
}
