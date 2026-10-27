"""Category guessing."""

from custom_components.frostbox.shelf_life import guess_category, shelf_days


def test_keyword_guess_prefers_longest_match():
    assert guess_category("Sliced turkey breast").key == "deli"
    assert guess_category("Whole chicken").key == "poultry"
    assert guess_category("2% Milk").key == "milk"
    assert guess_category("Mystery box").key == "other"


def test_off_tags_win():
    assert guess_category("Brand X", ["en:dairies", "en:yogurts"]).key == "yogurt"


def test_freezer_days():
    cat = guess_category("ground beef")
    assert shelf_days(cat, "fridge") == 2
    assert shelf_days(cat, "freezer") == 120
