import json

from recommender import config
from recommender.repository import RecipeRepository

CATEGORIES = {"밥", "국/찌개", "면", "반찬", "일품", "분식/간식", "양식"}
DIFFICULTIES = {"쉬움", "보통", "어려움"}


def load_raw():
    with open(config.RECIPES_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_has_100_recipes_with_unique_ids():
    raw = load_raw()
    assert len(raw) == 100
    assert len({r["id"] for r in raw}) == 100


def test_recipes_have_valid_fields():
    for r in RecipeRepository.from_json(config.RECIPES_PATH).all():
        assert r.category in CATEGORIES, r.id
        assert r.difficulty in DIFFICULTIES, r.id
        assert r.main_ingredients, r.id
        assert r.steps, r.id
        assert r.cook_time_min > 0, r.id


def test_ingredient_names_have_no_spaces():
    for r in RecipeRepository.from_json(config.RECIPES_PATH).all():
        for name in r.main_names + r.seasoning_names:
            assert " " not in name, (r.id, name)


def test_synonyms_point_to_names_used_in_recipes():
    repo = RecipeRepository.from_json(config.RECIPES_PATH)
    known = set(repo.main_ingredient_names()) | set(repo.seasoning_names())
    with open(config.SYNONYMS_PATH, encoding="utf-8") as f:
        synonyms = json.load(f)
    unknown = {k: v for k, v in synonyms.items() if v not in known}
    assert not unknown


def test_default_pantry_uses_seasoning_names():
    repo = RecipeRepository.from_json(config.RECIPES_PATH)
    assert set(config.DEFAULT_PANTRY) <= set(repo.seasoning_names())
