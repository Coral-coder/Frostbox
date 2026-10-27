"""Door analytics and food-safety logic."""

from datetime import UTC, datetime, timedelta

from custom_components.frostbox.tracker import (
    SAFETY_OK,
    SAFETY_UNSAFE,
    SAFETY_WARNING,
    DoorTracker,
    FoodSafetyTracker,
)

T0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def test_door_open_close_counts():
    doors = DoorTracker()
    assert doors.update({"cooler": False, "freezer": False}, T0) == []
    events = doors.update({"cooler": True, "freezer": False}, T0 + timedelta(seconds=10))
    assert [(e.component, e.opened) for e in events] == [("cooler", True)]
    assert doors.open_for(T0 + timedelta(seconds=70)) == timedelta(seconds=60)
    events = doors.update({"cooler": False, "freezer": False}, T0 + timedelta(seconds=40))
    assert events[0].duration == timedelta(seconds=30)
    assert doors.opens_today == 1
    assert doors.open_seconds_today == 30
    assert doors.open_for(T0 + timedelta(seconds=50)) is None


def test_door_counts_reset_daily():
    doors = DoorTracker()
    doors.update({"cooler": True}, T0)
    doors.update({"cooler": False}, T0 + timedelta(seconds=5))
    doors.update({"cooler": True}, T0 + timedelta(days=1))
    assert doors.opens_today == 1


def test_door_tracker_roundtrip():
    doors = DoorTracker()
    doors.update({"cooler": True}, T0)
    restored = DoorTracker.from_dict(doors.as_dict())
    assert restored.open_since == {"cooler": T0}
    assert restored.opens_today == 1


def test_food_safety_ok_then_warning_then_unsafe():
    fs = FoodSafetyTracker()
    fs.update(37, 0, True, T0)
    assert fs.state(T0) == SAFETY_OK
    t = T0
    for _ in range(30):  # 45 minutes warm, polled every 90 s
        t += timedelta(seconds=90)
        fs.update(45, 0, True, t)
    assert fs.state(t) == SAFETY_WARNING
    late = T0 + timedelta(hours=4, minutes=5)
    fs.update(45, 0, True, late)
    assert fs.state(late) == SAFETY_UNSAFE
    # Sticky until acknowledged, even once it cools down again.
    fs.update(37, 0, True, late + timedelta(minutes=1))
    assert fs.state(late + timedelta(minutes=1)) == SAFETY_UNSAFE
    fs.acknowledge()
    assert fs.state(late + timedelta(minutes=1)) == SAFETY_OK


def test_outage_counts_from_when_fridge_went_dark():
    fs = FoodSafetyTracker()
    fs.update(37, 0, True, T0)
    fs.update(None, None, False, T0 + timedelta(minutes=1))
    assert fs.state(T0 + timedelta(minutes=45)) == SAFETY_WARNING
    back = T0 + timedelta(hours=5)
    fs.update(48, 20, True, back)
    assert fs.last_outage_minutes and fs.last_outage_minutes > 290
    # Warm on return: the excursion started when it went offline, 5 h ago.
    assert fs.state(back) == SAFETY_UNSAFE


def test_ha_downtime_gap_counts_as_outage():
    fs = FoodSafetyTracker()
    fs.update(37, 0, True, T0)
    later = T0 + timedelta(hours=6)  # HA itself was off; no readings at all
    fs.update(50, 30, True, later)
    assert fs.state(later) == SAFETY_UNSAFE


def test_freezer_thaw_is_unsafe():
    fs = FoodSafetyTracker()
    fs.update(37, 0, True, T0)
    fs.update(37, 41, True, T0 + timedelta(minutes=1))
    assert fs.state(T0 + timedelta(minutes=1)) == SAFETY_UNSAFE
    assert "Freezer" in fs.advice(T0 + timedelta(minutes=1))


def test_food_safety_roundtrip():
    fs = FoodSafetyTracker()
    fs.update(45, 0, True, T0)
    restored = FoodSafetyTracker.from_dict(fs.as_dict())
    assert restored.fridge_above_since == T0
    assert restored.last_update == T0
