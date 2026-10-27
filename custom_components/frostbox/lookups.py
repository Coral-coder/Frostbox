"""External lookups: Open Food Facts (barcodes) and TheMealDB (recipes)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "Frostbox/0.1 (Home Assistant custom integration)"
TIMEOUT = aiohttp.ClientTimeout(total=10)
OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
MEALDB_FILTER = "https://www.themealdb.com/api/json/v1/1/filter.php"
MEALDB_PAGE = "https://www.themealdb.com/meal/{id}"

# Words that do not help a recipe search ("2% milk" -> "milk").
_NOISE = {
    "organic",
    "fresh",
    "large",
    "small",
    "whole",
    "low",
    "fat",
    "free",
    "reduced",
    "%",
    "2%",
    "1%",
    "skim",
    "leftover",
    "leftovers",
}


async def lookup_barcode(hass: HomeAssistant, barcode: str) -> dict[str, Any] | None:
    """Return ``{name, brand, quantity, category_tags}`` or None if unknown."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            OFF_URL.format(barcode=barcode),
            params={"fields": "product_name,generic_name,brands,quantity,categories_tags"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as err:
        _LOGGER.warning("Open Food Facts lookup for %s failed: %s", barcode, err)
        return None
    product = data.get("product") if data.get("status") == 1 else None
    if not product:
        return None
    name = product.get("product_name") or product.get("generic_name")
    if not name:
        return None
    brand = (product.get("brands") or "").split(",")[0].strip() or None
    return {
        "name": name.strip(),
        "brand": brand,
        "quantity": product.get("quantity"),
        "category_tags": product.get("categories_tags") or [],
    }


def ingredient_keyword(name: str) -> str:
    words = [w for w in name.lower().replace(",", " ").split() if w not in _NOISE]
    return words[-1] if words else name.lower()


async def recipes_for(hass: HomeAssistant, ingredients: list[str], limit: int = 5) -> list[dict[str, Any]]:
    """Meals ranked by how many of ``ingredients`` (most urgent first) they use."""
    session = async_get_clientsession(hass)
    scores: dict[str, dict[str, Any]] = {}

    async def _one(rank: int, ingredient: str) -> None:
        keyword = ingredient_keyword(ingredient)
        try:
            async with session.get(
                MEALDB_FILTER, params={"i": keyword}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
            ) as resp:
                data = await resp.json(content_type=None) if resp.status == 200 else {}
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug("TheMealDB lookup for %s failed: %s", keyword, err)
            return
        for meal in (data or {}).get("meals") or []:
            entry = scores.setdefault(
                meal["idMeal"],
                {
                    "name": meal["strMeal"],
                    "image": meal.get("strMealThumb"),
                    "url": MEALDB_PAGE.format(id=meal["idMeal"]),
                    "uses": [],
                    "score": 0.0,
                },
            )
            entry["uses"].append(ingredient)
            # Earlier (more urgent) ingredients weigh more.
            entry["score"] += 1.0 + 1.0 / (rank + 1)

    await asyncio.gather(*(_one(i, ing) for i, ing in enumerate(ingredients[:6])))
    ranked = sorted(scores.values(), key=lambda m: m["score"], reverse=True)[:limit]
    for meal in ranked:
        meal.pop("score")
    return ranked
