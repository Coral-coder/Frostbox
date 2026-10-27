"""Derived state: door analytics and food-safety tracking.

Pure logic, no Home Assistant imports, so it is easy to unit test. The
coordinator feeds it every status poll and persists ``as_dict()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .const import (
    FREEZER_THAWED_F,
    FREEZER_WARN_F,
    FREEZER_WARNING_AFTER,
    FRIDGE_SAFE_MAX_F,
    FRIDGE_UNSAFE_AFTER,
    FRIDGE_WARNING_AFTER,
)

SAFETY_OK = "ok"
SAFETY_WARNING = "warning"
SAFETY_UNSAFE = "unsafe"
SAFETY_UNKNOWN = "unknown"

OFFLINE_WARNING_AFTER = timedelta(minutes=30)
# A gap this long between readings (e.g. HA itself lost power) counts as an outage.
POLL_GAP_AS_OUTAGE = timedelta(minutes=10)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class DoorEvent:
    component: str
    opened: bool
    duration: timedelta | None = None


@dataclass
class DoorTracker:
    open_since: dict[str, datetime] = field(default_factory=dict)
    day: date | None = None
    opens_today: int = 0
    open_seconds_today: float = 0.0
    longest_open_seconds_today: float = 0.0
    last_opened: datetime | None = None
    last_closed: datetime | None = None
    last_component: str | None = None

    def _roll_day(self, now: datetime) -> None:
        today = now.date()
        if self.day != today:
            self.day = today
            self.opens_today = 0
            self.open_seconds_today = 0.0
            self.longest_open_seconds_today = 0.0

    def update(self, doors: dict[str, bool | None], now: datetime) -> list[DoorEvent]:
        self._roll_day(now)
        events: list[DoorEvent] = []
        for component, is_open in doors.items():
            if is_open is None:
                continue
            was_open = component in self.open_since
            if is_open and not was_open:
                self.open_since[component] = now
                self.opens_today += 1
                self.last_opened = now
                self.last_component = component
                events.append(DoorEvent(component, True))
            elif not is_open and was_open:
                started = self.open_since.pop(component)
                duration = now - started
                secs = duration.total_seconds()
                self.open_seconds_today += secs
                self.longest_open_seconds_today = max(self.longest_open_seconds_today, secs)
                self.last_closed = now
                events.append(DoorEvent(component, False, duration))
        return events

    def open_for(self, now: datetime) -> timedelta | None:
        """How long the longest-open door has been open."""
        if not self.open_since:
            return None
        return now - min(self.open_since.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "open_since": {k: _iso(v) for k, v in self.open_since.items()},
            "day": self.day.isoformat() if self.day else None,
            "opens_today": self.opens_today,
            "open_seconds_today": self.open_seconds_today,
            "longest_open_seconds_today": self.longest_open_seconds_today,
            "last_opened": _iso(self.last_opened),
            "last_closed": _iso(self.last_closed),
            "last_component": self.last_component,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DoorTracker:
        return cls(
            open_since={k: _dt(v) for k, v in (data.get("open_since") or {}).items() if v},
            day=date.fromisoformat(data["day"]) if data.get("day") else None,
            opens_today=data.get("opens_today", 0),
            open_seconds_today=data.get("open_seconds_today", 0.0),
            longest_open_seconds_today=data.get("longest_open_seconds_today", 0.0),
            last_opened=_dt(data.get("last_opened")),
            last_closed=_dt(data.get("last_closed")),
            last_component=data.get("last_component"),
        )


@dataclass
class FoodSafetyTracker:
    fridge_above_since: datetime | None = None
    fridge_max_f: float | None = None
    freezer_above_since: datetime | None = None
    freezer_max_f: float | None = None
    offline_since: datetime | None = None
    last_outage_minutes: float | None = None
    unsafe_since: datetime | None = None
    unsafe_reason: str | None = None
    last_fridge_f: float | None = None
    last_freezer_f: float | None = None
    last_update: datetime | None = None

    def update(self, fridge_f: float | None, freezer_f: float | None, online: bool, now: datetime) -> None:
        previous_update = self.last_update
        self.last_update = now
        if not online:
            if self.offline_since is None:
                self.offline_since = previous_update or now
            return

        outage_start = self.offline_since
        if outage_start is None and previous_update and now - previous_update >= POLL_GAP_AS_OUTAGE:
            outage_start = previous_update
        if outage_start is not None:
            self.last_outage_minutes = (now - outage_start).total_seconds() / 60
            self.offline_since = None

        if fridge_f is not None:
            self.last_fridge_f = fridge_f
            if fridge_f > FRIDGE_SAFE_MAX_F:
                if self.fridge_above_since is None:
                    # Coming back from an outage warm: assume it warmed up when it went dark.
                    self.fridge_above_since = outage_start or now
                self.fridge_max_f = max(self.fridge_max_f or fridge_f, fridge_f)
            else:
                self.fridge_above_since = None
                self.fridge_max_f = None

        if freezer_f is not None:
            self.last_freezer_f = freezer_f
            if freezer_f > FREEZER_WARN_F:
                if self.freezer_above_since is None:
                    self.freezer_above_since = outage_start or now
                self.freezer_max_f = max(self.freezer_max_f or freezer_f, freezer_f)
            else:
                self.freezer_above_since = None
                self.freezer_max_f = None

        if self.unsafe_since is None:
            reason = self._unsafe_reason(now)
            if reason:
                self.unsafe_since = now
                self.unsafe_reason = reason

    def _unsafe_reason(self, now: datetime) -> str | None:
        if self.fridge_above_since and now - self.fridge_above_since >= FRIDGE_UNSAFE_AFTER:
            return (
                f"Fridge above {FRIDGE_SAFE_MAX_F:.0f}°F for "
                f"{(now - self.fridge_above_since).total_seconds() / 3600:.1f} h "
                f"(peak {self.fridge_max_f:.0f}°F). Discard perishables: meat, dairy, leftovers."
            )
        if self.freezer_max_f is not None and self.freezer_max_f >= FREEZER_THAWED_F:
            return (
                f"Freezer reached {self.freezer_max_f:.0f}°F. Food that thawed fully "
                "should be cooked now or discarded; do not refreeze raw meat."
            )
        return None

    def acknowledge(self) -> None:
        self.unsafe_since = None
        self.unsafe_reason = None

    def fridge_excursion(self, now: datetime) -> timedelta | None:
        return now - self.fridge_above_since if self.fridge_above_since else None

    def freezer_excursion(self, now: datetime) -> timedelta | None:
        return now - self.freezer_above_since if self.freezer_above_since else None

    def state(self, now: datetime) -> str:
        if self.unsafe_since is not None:
            return SAFETY_UNSAFE
        if self.offline_since and now - self.offline_since >= OFFLINE_WARNING_AFTER:
            return SAFETY_WARNING
        fridge = self.fridge_excursion(now)
        if fridge and fridge >= FRIDGE_WARNING_AFTER:
            return SAFETY_WARNING
        freezer = self.freezer_excursion(now)
        if freezer and freezer >= FREEZER_WARNING_AFTER:
            return SAFETY_WARNING
        if self.last_fridge_f is None and self.last_freezer_f is None:
            return SAFETY_UNKNOWN
        return SAFETY_OK

    def advice(self, now: datetime) -> str:
        state = self.state(now)
        if state == SAFETY_UNSAFE:
            return self.unsafe_reason or "Food may be unsafe."
        if state == SAFETY_WARNING:
            if self.offline_since:
                mins = (now - self.offline_since).total_seconds() / 60
                return f"Fridge offline for {mins:.0f} min. Keep the doors shut."
            return "Temperature is high. Check the doors are sealed and avoid opening them."
        if state == SAFETY_UNKNOWN:
            return "Waiting for temperature readings."
        return "All good."

    def attributes(self, now: datetime) -> dict[str, Any]:
        fridge = self.fridge_excursion(now)
        freezer = self.freezer_excursion(now)
        return {
            "advice": self.advice(now),
            "fridge_minutes_above_40f": round(fridge.total_seconds() / 60) if fridge else 0,
            "freezer_minutes_above_10f": round(freezer.total_seconds() / 60) if freezer else 0,
            "fridge_peak_f": self.fridge_max_f,
            "freezer_peak_f": self.freezer_max_f,
            "offline_since": _iso(self.offline_since),
            "last_outage_minutes": round(self.last_outage_minutes) if self.last_outage_minutes else None,
            "unsafe_since": _iso(self.unsafe_since),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "fridge_above_since": _iso(self.fridge_above_since),
            "fridge_max_f": self.fridge_max_f,
            "freezer_above_since": _iso(self.freezer_above_since),
            "freezer_max_f": self.freezer_max_f,
            "offline_since": _iso(self.offline_since),
            "last_outage_minutes": self.last_outage_minutes,
            "unsafe_since": _iso(self.unsafe_since),
            "unsafe_reason": self.unsafe_reason,
            "last_fridge_f": self.last_fridge_f,
            "last_freezer_f": self.last_freezer_f,
            "last_update": _iso(self.last_update),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FoodSafetyTracker:
        return cls(
            fridge_above_since=_dt(data.get("fridge_above_since")),
            fridge_max_f=data.get("fridge_max_f"),
            freezer_above_since=_dt(data.get("freezer_above_since")),
            freezer_max_f=data.get("freezer_max_f"),
            offline_since=_dt(data.get("offline_since")),
            last_outage_minutes=data.get("last_outage_minutes"),
            unsafe_since=_dt(data.get("unsafe_since")),
            unsafe_reason=data.get("unsafe_reason"),
            last_fridge_f=data.get("last_fridge_f"),
            last_freezer_f=data.get("last_freezer_f"),
            last_update=_dt(data.get("last_update")),
        )
