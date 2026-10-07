"""경로, 모델, 분할 기준을 한곳에 둔다."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 테스트에서는 환경 변수로 임시 폴더를 가리킬 수 있다.
CHROMA_DIR = Path(os.environ.get("CONTRACT_CHROMA_DIR", ROOT / "chroma_db"))
UPLOAD_DIR = Path(os.environ.get("CONTRACT_UPLOAD_DIR", ROOT / "uploads"))
RAG_UPLOAD_DIR = UPLOAD_DIR / "rag"
CONTRACT_UPLOAD_DIR = UPLOAD_DIR / "contracts"

COLLECTION_NAME = "contract_guidelines"

# 가이드라인: 30자, 5자 겹침. 계약서: 30자, 겹침 없음.
# 구분자는 요청된 줄바꿈만 사용한다.
RAG_CHUNK_SIZE = 30
RAG_CHUNK_OVERLAP = 5
CONTRACT_CHUNK_SIZE = 30
CONTRACT_CHUNK_OVERLAP = 0
SEPARATORS = ["\n", "\n\n"]

EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"

# 청크가 짧아서 상위 몇 개를 판정 근거로 붙인다.
RETRIEVE_K = 5
EMBED_BATCH = 16

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
