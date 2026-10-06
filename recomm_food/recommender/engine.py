import logging
from dataclasses import dataclass, field, replace
from typing import Iterable, Protocol

from recommender.normalizer import IngredientNormalizer
from recommender.repository import Recipe, RecipeRepository

logger = logging.getLogger(__name__)


class Retriever(Protocol):
    def search(self, query: str, k: int) -> list[str]:
        """질의와 가까운 레시피 id를 유사도 순으로 돌려준다."""


class Explainer(Protocol):
    def explain(self, owned: list[str], recommendations: list["Recommendation"]) -> dict[str, str]:
        """레시피 id → 추천 설명."""


@dataclass(frozen=True)
class Recommendation:
    recipe: Recipe
    matched: list[str]
    missing_main: list[str]
    missing_seasonings: list[str]
    match_rate: float
    explanation: str | None = None

    @property
    def missing(self) -> list[str]:
        return self.missing_main + self.missing_seasonings


@dataclass
class RecommendationResult:
    ready: list[Recommendation] = field(default_factory=list)
    almost: list[Recommendation] = field(default_factory=list)
    explanation_failed: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.ready and not self.almost


class Recommender:
    """판정과 순위는 코드가, 설명은 Explainer(LLM)가 맡는다."""

    def __init__(
        self,
        repository: RecipeRepository,
        retriever: Retriever,
        normalizer: IngredientNormalizer,
        explainer: Explainer | None = None,
        candidate_k: int = 30,
        top_n: int = 5,
        max_missing: int = 2,
    ):
        self._repo = repository
        self._retriever = retriever
        self._normalizer = normalizer
        self._explainer = explainer
        self._candidate_k = candidate_k
        self._top_n = top_n
        self._max_missing = max_missing

    def recommend(self, ingredients: Iterable[str], pantry: Iterable[str]) -> RecommendationResult:
        owned = self._normalizer.normalize_all(ingredients)
        if not owned:
            return RecommendationResult()
        pantry_set = set(self._normalizer.normalize_all(pantry))

        query = "주재료: " + ", ".join(owned)
        candidates = [
            r for r in (self._repo.get(i) for i in self._retriever.search(query, self._candidate_k)) if r
        ]

        scored = [self._score(r, set(owned), pantry_set) for r in candidates]
        scored = [s for s in scored if s.matched and len(s.missing) <= self._max_missing]
        scored.sort(key=lambda s: (len(s.missing), -s.match_rate, -len(s.matched), s.recipe.id))

        result = RecommendationResult(
            ready=[s for s in scored if not s.missing][: self._top_n],
            almost=[s for s in scored if s.missing][: self._top_n],
        )
        return self._attach_explanations(owned, result)

    @staticmethod
    def _score(recipe: Recipe, owned: set[str], pantry: set[str]) -> Recommendation:
        mains = recipe.main_names
        matched = [n for n in mains if n in owned]
        missing_main = [n for n in mains if n not in owned]
        missing_seasonings = [n for n in recipe.seasoning_names if n not in pantry and n not in owned]
        return Recommendation(
            recipe=recipe,
            matched=matched,
            missing_main=missing_main,
            missing_seasonings=missing_seasonings,
            match_rate=len(matched) / len(mains) if mains else 0.0,
        )

    def _attach_explanations(self, owned: list[str], result: RecommendationResult) -> RecommendationResult:
        if self._explainer is None or result.is_empty:
            return result
        try:
            explanations = self._explainer.explain(owned, result.ready + result.almost)
        except Exception:
            logger.exception("추천 설명 생성 실패")
            result.explanation_failed = True
            return result

        def attach(items: list[Recommendation]) -> list[Recommendation]:
            return [replace(s, explanation=explanations.get(s.recipe.id)) for s in items]

        result.ready = attach(result.ready)
        result.almost = attach(result.almost)
        return result
