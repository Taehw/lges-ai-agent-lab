import pytest

from recommender.engine import Recommender
from recommender.normalizer import IngredientNormalizer
from recommender.repository import Recipe, RecipeRepository


def make_recipe(rid: str, mains: list[str], seasonings: list[str] = ()) -> Recipe:
    return Recipe.from_dict(
        {
            "id": rid,
            "name": f"요리{rid}",
            "category": "밥",
            "cook_time_min": 10,
            "difficulty": "쉬움",
            "servings": 1,
            "main_ingredients": [{"name": n, "amount": "1"} for n in mains],
            "seasonings": [{"name": n, "amount": "1"} for n in seasonings],
            "steps": ["만든다."],
        }
    )


class AllRetriever:
    """벡터 DB 대신 모든 레시피를 후보로 돌려준다."""

    def __init__(self, repo: RecipeRepository):
        self._ids = [r.id for r in repo.all()]
        self.queries: list[str] = []

    def search(self, query: str, k: int) -> list[str]:
        self.queries.append(query)
        return self._ids[:k]


class FakeExplainer:
    def __init__(self):
        self.calls = 0

    def explain(self, owned, recommendations):
        self.calls += 1
        return {s.recipe.id: f"{s.recipe.name} 추천" for s in recommendations}


class FailingExplainer:
    def explain(self, owned, recommendations):
        raise RuntimeError("LLM down")


RECIPES = [
    make_recipe("egg_rice", ["밥", "계란"], ["간장", "참기름"]),
    make_recipe("kimchi_rice", ["밥", "김치", "계란"], ["식용유"]),
    make_recipe("kimchi_stew", ["김치", "돼지고기", "두부", "대파"], ["고춧가루"]),
    make_recipe("japchae", ["당면", "시금치", "당근", "양파", "돼지고기"], ["간장"]),
    make_recipe("oyster_rice", ["밥", "버섯"], ["굴소스"]),
]
PANTRY = ["간장", "참기름", "식용유", "고춧가루"]


@pytest.fixture
def repo():
    return RecipeRepository(RECIPES)


def build(repo, explainer=None, **kwargs):
    normalizer = IngredientNormalizer({"달걀": "계란", "쪽파": "대파"})
    return Recommender(repo, AllRetriever(repo), normalizer, explainer=explainer, **kwargs)


def ids(items):
    return [s.recipe.id for s in items]


def test_all_main_ingredients_owned_goes_to_ready(repo):
    result = build(repo).recommend(["밥", "계란"], PANTRY)
    assert ids(result.ready) == ["egg_rice"]


def test_one_or_two_missing_goes_to_almost_with_exact_missing_names(repo):
    result = build(repo).recommend(["밥", "계란"], PANTRY)
    kimchi_rice = next(s for s in result.almost if s.recipe.id == "kimchi_rice")
    assert kimchi_rice.missing_main == ["김치"]
    assert kimchi_rice.matched == ["밥", "계란"]


def test_three_or_more_missing_is_excluded(repo):
    result = build(repo).recommend(["돼지고기"], PANTRY)
    all_ids = ids(result.ready) + ids(result.almost)
    assert "kimchi_stew" not in all_ids
    assert "japchae" not in all_ids


def test_recipe_without_any_owned_main_ingredient_is_excluded(repo):
    result = build(repo).recommend(["버섯"], PANTRY)
    assert "egg_rice" not in ids(result.almost)


def test_seasonings_in_pantry_are_not_counted_as_missing(repo):
    result = build(repo).recommend(["밥", "계란"], PANTRY)
    assert result.ready[0].missing == []


def test_unchecked_seasoning_is_counted_as_missing(repo):
    result = build(repo).recommend(["밥", "계란"], ["간장"])
    egg_rice = next(s for s in result.almost if s.recipe.id == "egg_rice")
    assert egg_rice.missing_seasonings == ["참기름"]
    assert egg_rice.missing_main == []


def test_synonym_input_matches_canonical_ingredient(repo):
    result = build(repo).recommend(["밥", "달걀"], PANTRY)
    assert ids(result.ready) == ["egg_rice"]


def test_input_whitespace_and_duplicates_are_normalized(repo):
    result = build(repo).recommend([" 밥 ", "계 란", "달걀", ""], PANTRY)
    assert ids(result.ready) == ["egg_rice"]


def test_sorted_by_missing_count_then_match_rate(repo):
    result = build(repo).recommend(["김치", "돼지고기", "밥"], PANTRY)
    # 부족 1: kimchi_rice(2/3) > egg_rice(1/2) / 부족 2: kimchi_stew(2/4, 일치 2개) > oyster_rice(1/2, 일치 1개)
    assert ids(result.almost) == ["kimchi_rice", "egg_rice", "kimchi_stew", "oyster_rice"]


def test_top_n_limits_each_group(repo):
    result = build(repo, top_n=1).recommend(["밥", "계란", "김치"], PANTRY)
    assert len(result.ready) == 1


def test_empty_input_returns_empty_result_without_search(repo):
    rec = build(repo)
    result = rec.recommend([], PANTRY)
    assert result.is_empty
    assert rec._retriever.queries == []


def test_no_match_returns_empty_result(repo):
    result = build(repo).recommend(["초콜릿"], PANTRY)
    assert result.is_empty


def test_explanations_are_attached(repo):
    explainer = FakeExplainer()
    result = build(repo, explainer=explainer).recommend(["밥", "계란"], PANTRY)
    assert result.ready[0].explanation == "요리egg_rice 추천"
    assert explainer.calls == 1
    assert not result.explanation_failed


def test_explainer_failure_still_returns_recommendations(repo):
    result = build(repo, explainer=FailingExplainer()).recommend(["밥", "계란"], PANTRY)
    assert ids(result.ready) == ["egg_rice"]
    assert result.ready[0].explanation is None
    assert result.explanation_failed


def test_retriever_receives_normalized_ingredients(repo):
    rec = build(repo)
    rec.recommend(["달걀", "밥"], PANTRY)
    assert "계란" in rec._retriever.queries[0]
