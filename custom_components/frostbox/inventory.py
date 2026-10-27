"""Fridge inventory with expiry tracking, persisted in .storage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STORAGE_VERSION
from .shelf_life import BY_KEY, guess_category, shelf_days


def _norm(name: str) -> str:
    name = name.strip().casefold()
    for prefix in ("the ", "some ", "a ", "an "):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    if name.endswith("es") and len(name) > 4 and (name[-3] in "sxz" or name.endswith("oes")):
        name = name[:-2]
    elif name.endswith("s") and not name.endswith("ss") and len(name) > 3:
        name = name[:-1]
    return name


@dataclass
class Item:
    name: str
    quantity: float = 1
    unit: str | None = None
    location: str = "fridge"
    category: str = "other"
    added: str = ""
    expires: str | None = None
    barcode: str | None = None
    brand: str | None = None
    staple: bool = False
    min_quantity: float = 0
    leftover: bool = False
    notes: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def expires_date(self) -> date | None:
        return date.fromisoformat(self.expires) if self.expires else None

    def days_left(self, today: date) -> int | None:
        exp = self.expires_date
        return (exp - today).days if exp else None

    def summary(self) -> str:
        qty = self.quantity
        qty_txt = str(int(qty)) if float(qty).is_integer() else f"{qty:g}"
        unit = f" {self.unit}" if self.unit else ""
        return self.name if qty == 1 and not self.unit else f"{self.name} ({qty_txt}{unit})"

    def as_public(self, today: date) -> dict[str, Any]:
        data = asdict(self)
        data["days_left"] = self.days_left(today)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Item:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ConsumeResult:
    item: Item
    remaining: float
    removed: bool
    now_low: bool


class Inventory:
    """In-memory inventory backed by a HA Store."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.inventory.{entry_id}")
        self._items: dict[str, Item] = {}
        self._listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._items = {i["id"]: Item.from_dict(i) for i in data.get("items", [])}

    @callback
    def _changed(self) -> None:
        self._store.async_delay_save(lambda: {"items": [asdict(i) for i in self._items.values()]}, 1)
        for listener in list(self._listeners):
            listener()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # --- queries ---------------------------------------------------------

    @property
    def today(self) -> date:
        return dt_util.now().date()

    def items(self) -> list[Item]:
        far = date.max
        return sorted(self._items.values(), key=lambda i: (i.expires_date or far, i.name.casefold()))

    def get(self, item_id: str) -> Item | None:
        return self._items.get(item_id)

    def find(self, query: str, location: str | None = None) -> list[Item]:
        """Items whose name matches ``query`` (or the item id)."""
        if query in self._items:
            return [self._items[query]]
        q = _norm(query)
        exact = [i for i in self.items() if _norm(i.name) == q]
        partial = [i for i in self.items() if q and q in _norm(i.name) and i not in exact]
        found = exact + partial
        if location:
            found = [i for i in found if i.location == location]
        return found

    def expiring(self, within_days: int) -> list[Item]:
        today = self.today
        return [i for i in self.items() if (d := i.days_left(today)) is not None and 0 <= d <= within_days]

    def expired(self) -> list[Item]:
        today = self.today
        return [i for i in self.items() if (d := i.days_left(today)) is not None and d < 0]

    def low_staples(self) -> list[Item]:
        return [i for i in self._items.values() if i.staple and i.quantity <= i.min_quantity]

    # --- mutations -------------------------------------------------------

    @callback
    def add(
        self,
        name: str,
        *,
        quantity: float = 1,
        unit: str | None = None,
        location: str = "fridge",
        category: str | None = None,
        category_tags: list[str] | None = None,
        expires: date | None = None,
        days: int | None = None,
        barcode: str | None = None,
        brand: str | None = None,
        staple: bool = False,
        min_quantity: float = 0,
        leftover: bool = False,
        notes: str | None = None,
    ) -> Item:
        cat = BY_KEY.get(category) if category else None
        if cat is None:
            cat = BY_KEY["leftovers"] if leftover else guess_category(name, category_tags)
        today = self.today
        if expires is None:
            expires = today + timedelta(days=days if days is not None else shelf_days(cat, location))

        # Merge into an existing item when it is clearly the same thing.
        for existing in self.find(name, location):
            if _norm(existing.name) != _norm(name) or existing.unit != unit or existing.leftover:
                continue
            existing.quantity += quantity
            if existing.expires_date is None or expires < existing.expires_date:
                existing.expires = expires.isoformat()
            existing.staple = existing.staple or staple
            existing.min_quantity = max(existing.min_quantity, min_quantity)
            self._changed()
            return existing

        item = Item(
            name=name.strip(),
            quantity=quantity,
            unit=unit,
            location=location,
            category=cat.key,
            added=dt_util.now().isoformat(timespec="seconds"),
            expires=expires.isoformat(),
            barcode=barcode,
            brand=brand,
            staple=staple,
            min_quantity=min_quantity,
            leftover=leftover,
            notes=notes,
        )
        self._items[item.id] = item
        self._changed()
        return item

    @callback
    def consume(self, item: Item, quantity: float | None = 1) -> ConsumeResult:
        """Use up ``quantity`` (None = all) of an item."""
        remaining = 0 if quantity is None else max(item.quantity - quantity, 0)
        was_low = item.quantity <= item.min_quantity
        item.quantity = remaining
        removed = remaining <= 0 and not item.staple
        if removed:
            self._items.pop(item.id, None)
        self._changed()
        now_low = item.staple and remaining <= item.min_quantity and not was_low
        return ConsumeResult(item, remaining, removed, now_low)

    @callback
    def remove(self, item_id: str) -> Item | None:
        item = self._items.pop(item_id, None)
        if item:
            self._changed()
        return item

    @callback
    def update(self, item_id: str, **changes: Any) -> Item | None:
        item = self._items.get(item_id)
        if item is None:
            return None
        for key, value in changes.items():
            if key == "expires" and isinstance(value, (date, datetime)):
                value = value.isoformat()[:10]
            if hasattr(item, key) and key != "id":
                setattr(item, key, value)
        self._changed()
        return item

    @callback
    def clear(self) -> None:
        self._items.clear()
        self._changed()
