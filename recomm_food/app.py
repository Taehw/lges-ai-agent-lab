import os

import streamlit as st

from recommender import config
from recommender.engine import Recommendation, Recommender
from recommender.normalizer import IngredientNormalizer
from recommender.repository import RecipeRepository

st.set_page_config(page_title="냉장고 요리 추천", page_icon="🍳", layout="wide")


@st.cache_resource
def load_repository() -> RecipeRepository:
    return RecipeRepository.from_json(config.RECIPES_PATH)


@st.cache_resource
def load_normalizer() -> IngredientNormalizer:
    return IngredientNormalizer.from_json(config.SYNONYMS_PATH)


@st.cache_resource
def load_recommender() -> Recommender:
    from recommender.explainer import LLMExplainer
    from recommender.retriever import ChromaRetriever, build_index, index_exists

    repo = load_repository()
    if not index_exists():
        with st.spinner("레시피 인덱스를 처음 만드는 중입니다..."):
            build_index(repo)
    return Recommender(
        repository=repo,
        retriever=ChromaRetriever(),
        normalizer=load_normalizer(),
        explainer=LLMExplainer(),
        candidate_k=config.CANDIDATE_K,
        top_n=config.TOP_N_PER_GROUP,
        max_missing=config.MAX_MISSING,
    )


def ingredient_options(repo: RecipeRepository, normalizer: IngredientNormalizer) -> list[str]:
    canonical = set(repo.main_ingredient_names())
    aliases = {a for a in normalizer.aliases() if normalizer.normalize(a) in canonical}
    return sorted(canonical | aliases)


def render_sidebar(repo: RecipeRepository) -> list[str]:
    st.sidebar.header("🧂 우리 집 양념")
    st.sidebar.caption("체크된 양념은 있다고 보고 추천합니다. 없는 양념은 체크를 해제하세요.")
    pantry = []
    cols = st.sidebar.columns(2)
    for i, name in enumerate(repo.seasoning_names()):
        if cols[i % 2].checkbox(name, value=name in config.DEFAULT_PANTRY, key=f"pantry_{name}"):
            pantry.append(name)
    return pantry


def render_card(rec: Recommendation) -> None:
    r = rec.recipe
    with st.container(border=True):
        st.subheader(r.name)
        st.caption(f"{r.category} · ⏱ {r.cook_time_min}분 · {r.difficulty} · {r.servings}인분")
        st.progress(rec.match_rate, text=f"주재료 일치 {len(rec.matched)}/{len(r.main_ingredients)}")
        st.markdown("✅ 있는 재료: " + ", ".join(rec.matched))
        if rec.missing_main:
            st.markdown("🛒 부족한 재료: **" + ", ".join(rec.missing_main) + "**")
        if rec.missing_seasonings:
            st.markdown("🧂 부족한 양념: **" + ", ".join(rec.missing_seasonings) + "**")
        if rec.explanation:
            st.info(rec.explanation, icon="💡")
        with st.expander("레시피 보기"):
            st.markdown("**주재료**")
            st.markdown("\n".join(f"- {i.name} {i.amount}" for i in r.main_ingredients))
            if r.seasonings:
                st.markdown("**양념**")
                st.markdown("\n".join(f"- {i.name} {i.amount}" for i in r.seasonings))
            st.markdown("**조리 순서**")
            st.markdown("\n".join(f"{n}. {step}" for n, step in enumerate(r.steps, 1)))


def render_group(title: str, items: list[Recommendation], empty_text: str) -> None:
    st.markdown(f"### {title}")
    if not items:
        st.caption(empty_text)
        return
    cols = st.columns(min(len(items), 3))
    for i, rec in enumerate(items):
        with cols[i % len(cols)]:
            render_card(rec)


def main() -> None:
    st.title("🍳 냉장고 재료로 뭐 해먹지?")
    st.caption("냉장고에 있는 재료를 고르면, 지금 만들 수 있는 요리를 추천해 드려요.")

    if not os.environ.get("OPENAI_API_KEY"):
        st.error("OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다. 키를 설정한 뒤 앱을 다시 실행하세요.")
        st.stop()

    repo = load_repository()
    normalizer = load_normalizer()
    pantry = render_sidebar(repo)

    ingredients = st.multiselect(
        "냉장고에 있는 재료",
        options=ingredient_options(repo, normalizer),
        placeholder="예: 계란, 양파, 김치 (목록에 없으면 직접 입력)",
        accept_new_options=True,
    )

    if st.button("🔍 요리 추천받기", type="primary"):
        if not ingredients:
            st.warning("재료를 하나 이상 입력해 주세요.")
            st.session_state.pop("result", None)
        else:
            with st.spinner("레시피를 찾고 있어요..."):
                st.session_state["result"] = load_recommender().recommend(ingredients, pantry)

    result = st.session_state.get("result")
    if result is None:
        return
    if result.is_empty:
        st.info("입력한 재료로 만들 수 있는 요리를 찾지 못했어요. 재료를 더 추가해 보세요.")
        return
    if result.explanation_failed:
        st.caption("⚠️ AI 설명을 불러오지 못해 추천 목록만 표시합니다.")

    render_group("🍽 지금 바로 만들 수 있어요", result.ready, "가진 재료만으로 만들 수 있는 요리는 아직 없어요.")
    render_group("🛒 1~2개만 더 있으면 돼요", result.almost, "조금만 더 사면 되는 요리는 없어요.")


main()
