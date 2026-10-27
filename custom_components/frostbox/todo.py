"""The fridge inventory as a native HA to-do list.

Each item is a to-do whose due date is its expiry date, so the standard HA
to-do UI (and the fridge-screen dashboard) shows what to use first. Checking
an item off means "used it up".
"""

from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FrostboxCoordinator
from .entity import FrostboxEntity
from .inventory import Inventory, Item
from .models import FrostboxConfigEntry
from .services import async_after_consume


async def async_setup_entry(
    hass: HomeAssistant, entry: FrostboxConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([InventoryTodo(entry.runtime_data.coordinator, entry.runtime_data.inventory)])


def _description(item: Item) -> str:
    parts = [item.location.title(), item.category.replace("_", " ")]
    if item.brand:
        parts.append(item.brand)
    if item.leftover:
        parts.append("leftover")
    if item.staple:
        parts.append(f"staple (keep ≥ {item.min_quantity:g})")
    if item.notes:
        parts.append(item.notes)
    return " · ".join(parts)


class InventoryTodo(FrostboxEntity, TodoListEntity):
    _attr_name = "Inventory"
    _attr_icon = "mdi:fridge-variant"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: FrostboxCoordinator, inventory: Inventory) -> None:
        super().__init__(coordinator, "inventory_todo")
        self.inventory = inventory

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.inventory.async_add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def todo_items(self) -> list[TodoItem]:
        return [
            TodoItem(
                uid=item.id,
                summary=item.summary(),
                status=TodoItemStatus.NEEDS_ACTION,
                due=item.expires_date,
                description=_description(item),
            )
            for item in self.inventory.items()
            if item.quantity > 0
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        self.inventory.add(
            item.summary or "Item",
            expires=item.due if item.due and not hasattr(item.due, "hour") else None,
            notes=item.description,
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        existing = self.inventory.get(item.uid or "")
        if existing is None:
            return
        if item.status == TodoItemStatus.COMPLETED:
            result = self.inventory.consume(existing, None)
            await async_after_consume(self.hass, self.coordinator, result)
            return
        changes: dict = {}
        if item.summary and item.summary != existing.summary():
            changes["name"] = item.summary
        if item.due is not None:
            changes["expires"] = item.due
        if item.description is not None and item.description != _description(existing):
            changes["notes"] = item.description
        if changes:
            self.inventory.update(existing.id, **changes)

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            self.inventory.remove(uid)
