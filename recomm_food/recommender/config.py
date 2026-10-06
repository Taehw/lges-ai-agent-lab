from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RECIPES_PATH = DATA_DIR / "recipes.json"
SYNONYMS_PATH = DATA_DIR / "synonyms.json"
CHROMA_DIR = PROJECT_ROOT / "chroma_db"
CHROMA_COLLECTION = "recipes"

CHAT_MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-small"

CANDIDATE_K = 30
TOP_N_PER_GROUP = 5
MAX_MISSING = 2

# 대부분의 가정에 있다고 가정하는 양념. 사이드바에서 기본으로 체크된다.
DEFAULT_PANTRY = [
    "소금",
    "설탕",
    "간장",
    "국간장",
    "식용유",
    "후추",
    "참기름",
    "다진마늘",
    "고춧가루",
    "깨",
    "식초",
    "고추장",
    "된장",
    "물엿",
]
