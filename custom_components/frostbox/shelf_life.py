"""Shelf-life estimates (days) by food category and storage location.

Numbers follow USDA FoodKeeper guidance for opened/fresh food, rounded
conservatively. They are only defaults: every item's expiry can be edited.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    fridge_days: int
    freezer_days: int
    keywords: tuple[str, ...]
    pantry_days: int | None = None


CATEGORIES: tuple[Category, ...] = (
    Category("leftovers", 4, 90, ("leftover", "takeout", "cooked")),
    Category("poultry", 2, 270, ("chicken", "turkey", "duck", "poultry")),
    Category("seafood", 2, 180, ("fish", "salmon", "tuna", "shrimp", "seafood", "cod", "tilapia", "crab")),
    Category("ground_meat", 2, 120, ("ground beef", "ground pork", "mince", "burger", "sausage")),
    Category("meat", 4, 240, ("beef", "steak", "pork", "lamb", "veal", "roast", "meat", "bacon", "ham")),
    Category("deli", 5, 60, ("deli", "salami", "prosciutto", "hot dog", "lunch meat", "turkey breast")),
    Category("milk", 7, 90, ("milk", "cream", "half and half", "half & half", "oat milk", "almond milk")),
    Category("yogurt", 14, 60, ("yogurt", "yoghurt", "kefir", "sour cream", "cottage cheese")),
    Category("cheese", 21, 180, ("cheese", "cheddar", "mozzarella", "parmesan", "brie", "feta")),
    Category("eggs", 35, 365, ("egg",)),
    Category("butter", 60, 270, ("butter", "margarine")),
    Category("berries", 4, 240, ("berry", "berries", "strawberr", "raspberr", "blueberr", "blackberr")),
    Category(
        "leafy_greens",
        5,
        240,
        ("lettuce", "spinach", "salad", "kale", "arugula", "greens", "herb", "cilantro", "parsley"),
    ),
    Category(
        "vegetables",
        7,
        240,
        (
            "vegetable",
            "broccoli",
            "carrot",
            "pepper",
            "celery",
            "cucumber",
            "zucchini",
            "mushroom",
            "tomato",
            "asparagus",
            "corn",
        ),
    ),
    Category(
        "fruit",
        7,
        240,
        ("fruit", "apple", "grape", "orange", "lemon", "lime", "melon", "peach", "pear", "plum", "cherr"),
    ),
    Category("bread", 7, 90, ("bread", "tortilla", "bagel", "bun", "roll", "pita"), pantry_days=4),
    Category("sauces", 30, 180, ("sauce", "salsa", "pesto", "hummus", "dip", "gravy", "dressing")),
    Category(
        "condiments",
        120,
        365,
        (
            "ketchup",
            "mustard",
            "mayo",
            "mayonnaise",
            "jam",
            "jelly",
            "pickle",
            "relish",
            "soy sauce",
            "hot sauce",
            "syrup",
        ),
    ),
    Category(
        "beverages",
        10,
        180,
        ("juice", "soda", "beer", "wine", "kombucha", "water", "seltzer", "lemonade", "tea", "coffee"),
    ),
    Category("frozen", 3, 180, ("frozen", "ice cream", "pizza", "popsicle", "waffle")),
    Category("baby", 2, 90, ("formula", "breast milk", "baby food")),
)

DEFAULT_CATEGORY = Category("other", 7, 90, ())

BY_KEY = {c.key: c for c in CATEGORIES}

# Open Food Facts category tags -> our categories
OFF_TAG_MAP: dict[str, str] = {
    "en:milks": "milk",
    "en:dairies": "yogurt",
    "en:yogurts": "yogurt",
    "en:cheeses": "cheese",
    "en:eggs": "eggs",
    "en:butters": "butter",
    "en:meats": "meat",
    "en:poultries": "poultry",
    "en:seafood": "seafood",
    "en:fishes": "seafood",
    "en:sausages": "ground_meat",
    "en:hams": "deli",
    "en:fruits": "fruit",
    "en:berries": "berries",
    "en:vegetables": "vegetables",
    "en:salads": "leafy_greens",
    "en:breads": "bread",
    "en:sauces": "sauces",
    "en:condiments": "condiments",
    "en:beverages": "beverages",
    "en:frozen-foods": "frozen",
    "en:ice-creams": "frozen",
}


def guess_category(name: str, tags: list[str] | None = None) -> Category:
    """Pick a category from Open Food Facts tags, then from name keywords."""
    for tag in reversed(tags or []):  # OFF tags go from generic to specific
        key = OFF_TAG_MAP.get(tag)
        if key:
            return BY_KEY[key]
    lowered = name.lower()
    best: Category | None = None
    best_len = 0
    for category in CATEGORIES:
        for kw in category.keywords:
            if kw in lowered and len(kw) > best_len:
                best, best_len = category, len(kw)
    return best or DEFAULT_CATEGORY


def shelf_days(category: Category, location: str) -> int:
    if location == "freezer":
        return category.freezer_days
    if location == "pantry" and category.pantry_days is not None:
        return category.pantry_days
    return category.fridge_days
