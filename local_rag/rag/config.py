from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

LLM_MODEL_PATH = BASE_DIR / "models" / "exaone_2.4b" / "EXAONE-3.5-2.4B-Instruct-Q5_K_M.gguf"
EMBEDDING_MODEL_PATH = BASE_DIR / "models" / "bge-m3"

UPLOAD_DIR = BASE_DIR / "uploads"
CHROMA_DIR = BASE_DIR / "chroma_db"

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
CHUNK_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", " ", ""]

RETRIEVE_K = 4
# Chroma cosine distance: 0 = identical, 1 = orthogonal. Above this, treat as no hit.
MAX_DISTANCE = 0.50

NO_INFO_MESSAGE = "정보가 없어서 답변할 수 없습니다"

COLLECTION_NAME = "pdf_rag"

LLM_N_CTX = 4096
LLM_MAX_TOKENS = 512
LLM_TEMPERATURE = 0.2
HISTORY_TURN_LIMIT = 6

SYSTEM_PROMPT = (
    "당신은 업로드된 PDF 문서 내용만을 근거로 답하는 한국어 도우미입니다. "
    "반드시 [문서]에 있는 정보만 사용해 답하세요. "
    "문서에 없는 내용은 추측하거나 일반 지식으로 채우지 마세요. "
    "질문과 관련된 내용이 문서에 있으면 그 내용을 바탕으로 간결하게 답하세요."
)
