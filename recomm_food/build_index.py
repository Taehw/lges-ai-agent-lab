"""recipes.json이 바뀌면 실행해 Chroma 인덱스를 다시 만든다: python build_index.py"""

import os
import sys

from recommender import config
from recommender.repository import RecipeRepository
from recommender.retriever import build_index


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다.")
    repo = RecipeRepository.from_json(config.RECIPES_PATH)
    count = build_index(repo)
    print(f"레시피 {count}개를 인덱싱했습니다 → {config.CHROMA_DIR}")


if __name__ == "__main__":
    main()
