"""Constants for the Frostbox integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "frostbox"
MANUFACTURER: Final = "Samsung"
DEFAULT_MODEL: Final = "RF90F29AEWAA"

API_BASE: Final = "https://api.smartthings.com/v1"

# Config entry data
CONF_AUTH_TYPE: Final = "auth_type"
AUTH_CORE: Final = "core_smartthings"
AUTH_PAT: Final = "personal_access_token"
CONF_CORE_ENTRY_ID: Final = "core_entry_id"
CONF_TOKEN: Final = "token"
CONF_DEVICE_ID: Final = "device_id"
CONF_LOCATION_ID: Final = "location_id"

# Options
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_DOOR_AJAR_SECONDS: Final = "door_ajar_seconds"
CONF_EXPIRY_WARNING_DAYS: Final = "expiry_warning_days"
CONF_SHOPPING_LIST: Final = "shopping_list_entity"
CONF_CAMERA_SOURCES: Final = "camera_sources"
CONF_VIEW_INSIDE_URL: Final = "view_inside_url_template"
CONF_VIEW_INSIDE_TOKEN: Final = "view_inside_token"
CONF_CAMERA_REFRESH: Final = "camera_refresh_seconds"
CONF_VIRTUAL_SWITCHES: Final = "fridge_screen_switches"
CONF_PARTY_HOURS: Final = "party_mode_hours"
CONF_GROCERY_HOURS: Final = "grocery_mode_hours"
CONF_PARTY_FLEX_MODE: Final = "party_flexzone_mode"
CONF_RECIPE_AGENT: Final = "recipe_conversation_agent"
CONF_HDHOMERUN_HOST: Final = "hdhomerun_host"
CONF_TV_QUALITY: Final = "tv_quality"
CONF_PLEX_URL: Final = "plex_url"
CONF_PLEX_TOKEN: Final = "plex_token"

DEFAULT_SCAN_INTERVAL: Final = 20
DEFAULT_DOOR_AJAR_SECONDS: Final = 60
DEFAULT_EXPIRY_WARNING_DAYS: Final = 2
DEFAULT_SHOPPING_LIST: Final = "todo.shopping_list"
DEFAULT_CAMERA_REFRESH: Final = 60
MJPEG_FPS: Final = 10
DEFAULT_PARTY_HOURS: Final = 6.0
DEFAULT_GROCERY_HOURS: Final = 3.0
DEFAULT_TV_QUALITY: Final = "720p"

MIN_SCAN_INTERVAL: Final = timedelta(seconds=10)

# SmartThings component ids used by Samsung refrigerators
COMPONENT_MAIN: Final = "main"
COMPONENT_FRIDGE: Final = "cooler"
COMPONENT_FREEZER: Final = "freezer"
COMPONENT_FLEXZONE: Final = "cvroom"

COMPONENT_NAMES: Final = {
    COMPONENT_MAIN: "",
    COMPONENT_FRIDGE: "Fridge",
    COMPONENT_FREEZER: "Freezer",
    COMPONENT_FLEXZONE: "FlexZone",
    "onedoor": "Door",
    "icemaker": "Ice maker",
    "icemaker-02": "Ice Bites maker",
    "icemaker-03": "Ice maker 3",
    "pantry-01": "Pantry 1",
    "pantry-02": "Pantry 2",
    "scale-10": "Scale 1",
    "scale-11": "Scale 2",
    "camera-01": "Camera",
    "curdmaker": "Curd maker",
}

# Food safety thresholds (USDA guidance), in Fahrenheit
FRIDGE_SAFE_MAX_F: Final = 40.0
FREEZER_WARN_F: Final = 10.0
FREEZER_THAWED_F: Final = 40.0
FRIDGE_WARNING_AFTER: Final = timedelta(minutes=30)
FRIDGE_UNSAFE_AFTER: Final = timedelta(hours=4)
FREEZER_WARNING_AFTER: Final = timedelta(hours=1)

# Modes
MODE_PARTY: Final = "party"
MODE_GROCERY: Final = "grocery"
MODE_VACATION: Final = "vacation"
MODES: Final = (MODE_PARTY, MODE_GROCERY, MODE_VACATION)

# Fridge-screen virtual switches (SmartThings virtual devices)
VIRTUAL_SWITCHES: Final = {
    MODE_PARTY: "Frostbox Party Mode",
    MODE_GROCERY: "Frostbox Grocery Mode",
    MODE_VACATION: "Frostbox Vacation Mode",
    "dinner": "Frostbox Dinner Ready",
}

# Events fired on the HA bus
EVENT_DINNER_READY: Final = f"{DOMAIN}_dinner_ready"
EVENT_DOOR: Final = f"{DOMAIN}_door"
EVENT_INVENTORY: Final = f"{DOMAIN}_inventory_changed"
EVENT_SCREENSAVER: Final = f"{DOMAIN}_screensaver"
EVENT_KIOSK_APP: Final = f"{DOMAIN}_kiosk_app"

# Fridge-screen kiosk and screensavers (served from ./www)
STATIC_URL: Final = "/frostbox"
SCREENSAVERS: Final = ("Aquarium", "Matrix", "Plex", "Cycle", "Off")

LOCATIONS: Final = ("fridge", "freezer", "flexzone", "door", "pantry")

STORAGE_VERSION: Final = 1
