"""가이드라인 저장, 계약서 분할, 문장 검토, 일반 질문을 처리한다.

문장마다 유사 가이드라인을 찾고 수정 여부를 한 번 판정한다.
판정이 끝나면 바로 다음 문장으로 넘어가므로 화면에는 결과가 하나씩 쌓인다.
검색 1회와 모델 호출 1회로 끝나서 에이전트 반복 호출은 넣지 않았다.
"""

from __future__ import annotations

import os
import uuid
from collections import defaultdict
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field
from pypdf import PdfReader

from review.config import (
    CHAT_MODEL,
    CHROMA_DIR,
    COLLECTION_NAME,
    CONTRACT_CHUNK_OVERLAP,
    CONTRACT_CHUNK_SIZE,
    EMBED_BATCH,
    EMBEDDING_MODEL,
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
    RETRIEVE_K,
    SEPARATORS,
)
from review.progress import Progress

REVIEW_SYSTEM = """너는 계약서 검토자다.
가이드라인 발췌와 계약서 문장을 비교해 위배나 보완이 필요한지 판단한다.
규칙:
- 위배 또는 보완이 필요하면 needs_fix는 true, revised에는 가이드라인에 맞게 고친 문장만 적는다.
- 이상이 없으면 needs_fix는 false, revised는 빈 문자열로 둔다.
- 가이드라인에 없는 내용을 새로 만들지 않는다.
- 수정은 해당 문장 안에서만 하고, 문장 번호와 나머지 표현은 유지한다.
- 문장이 중간에서 잘렸고 위배가 분명하지 않으면 needs_fix는 false로 둔다.
"""

CHAT_SYSTEM = """너는 친절한 도우미다.
일반 질문에는 알고 있는 내용으로 간결하게 한국어로 답한다.
"""


class ClauseReview(BaseModel):
    """계약 문장 하나에 대한 판정."""

    needs_fix: bool = Field(description="위배 또는 보완이 필요하면 true")
    revised: str = Field(description="고친 문장. 이상이 없으면 빈 문자열")


def build_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    """글자 수 기준으로 나눈다. 줄바꿈 다음으로 글자 단위까지 자른다."""
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=SEPARATORS,
        length_function=len,
    )


def pick_revision(original: str, needs_fix: bool, revised: str) -> str | None:
    """고칠 필요가 있고 원문과 다를 때만 수정 문장을 돌려준다."""
    if not needs_fix:
        return None
    cleaned = (revised or "").strip()
    if not cleaned or cleaned == original.strip():
        return None
    return cleaned


def format_context(docs: list[Document]) -> str:
    """검색된 가이드라인 조각을 프롬프트에 붙일 문자열로 만든다."""
    if not docs:
        return "(검색된 가이드라인이 없습니다)"
    blocks = []
    for index, doc in enumerate(docs, start=1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page", "?")
        blocks.append(f"[{index}] {source} p.{page}\n{doc.page_content.strip()}")
    return "\n\n".join(blocks)


class ContractService:
    """업로드된 가이드라인과 계약서 상태를 메모리와 Chroma에 보관한다."""

    def __init__(self) -> None:
        self._store: Chroma | None = None
        self._review_chain = None
        self.rag_files: list[dict[str, object]] = []
        self.contract_name: str | None = None
        self.contract_chunks: list[str] | None = None

    def snapshot(self) -> dict[str, object]:
        """화면이 새로고침돼도 버튼과 파일 목록을 복구할 때 쓴다."""
        if not self.rag_files and self._store is None and (CHROMA_DIR / "chroma.sqlite3").exists():
            try:
                self._open_store()
            except Exception:
                pass
        return {
            "rag_files": self.rag_files,
            "contract_name": self.contract_name,
            "contract_ready": self.contract_chunks is not None,
            "api_key_set": bool(os.environ.get("OPENAI_API_KEY")),
        }

    def ingest_rag(self, paths: list[Path]):
        """PDF 여러 개를 읽어 30자 청크로 나누고 임베딩해 저장한다."""
        yield {
            "type": "progress",
            "bar": "PDF 페이지를 확인하고 있습니다.",
            "current": 0,
            "total": len(paths),
            "percent": 0,
        }
        loaded: list[tuple[Path, list[Document], int]] = []
        page_counts = [_page_count(path) for path in paths]
        reading = Progress(total=max(sum(page_counts), 1), desc="PDF 읽는 중")
        try:
            yield reading.event()
            for path, page_count in zip(paths, page_counts):
                docs: list[Document] = []
                for doc in PyPDFLoader(str(path)).lazy_load():
                    page = doc.metadata.get("page", 0)
                    doc.metadata["source"] = path.name
                    doc.metadata["page"] = int(page) + 1 if isinstance(page, int) else page
                    docs.append(doc)
                    reading.advance()
                    yield reading.event()
                loaded.append((path, docs, page_count))
        finally:
            reading.close()

        splitter = build_splitter(RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP)
        prepared: list[tuple[Path, int, list[Document]]] = []
        total_chunks = 0
        for path, docs, page_count in loaded:
            chunks = [chunk for chunk in splitter.split_documents(docs) if chunk.page_content.strip()]
            prepared.append((path, page_count, chunks))
            total_chunks += len(chunks)

        if total_chunks == 0:
            yield {
                "type": "done",
                "message": "추출된 텍스트가 없습니다. 스캔본 PDF인지 확인해 주세요.",
                "files": self.rag_files,
                "contract_ready": self.contract_chunks is not None,
            }
            return

        store = self._open_store()
        embedding = Progress(total=total_chunks, desc="가이드라인 임베딩")
        added: list[dict[str, object]] = []
        try:
            yield embedding.event()
            for path, page_count, chunks in prepared:
                self._delete_source(path.name)
                try:
                    for start in range(0, len(chunks), EMBED_BATCH):
                        batch = chunks[start : start + EMBED_BATCH]
                        ids = [f"{path.name}::{uuid.uuid4().hex}" for _ in batch]
                        store.add_documents(documents=batch, ids=ids)
                        embedding.advance(len(batch))
                        yield embedding.event()
                except Exception:
                    self._delete_source(path.name)
                    raise
                record = {"name": path.name, "pages": page_count, "chunks": len(chunks)}
                added.append(record)
                self._upsert_file(record)
        finally:
            embedding.close()

        lines = ["RAG 파일 업로드가 완료되었습니다."]
        for record in added:
            lines.append(f"- {record['name']}: {record['pages']}페이지, {record['chunks']}청크")
        yield {
            "type": "done",
            "message": "\n".join(lines),
            "files": self.rag_files,
            "contract_ready": self.contract_chunks is not None,
        }

    def ingest_contract(self, path: Path):
        """계약서 PDF를 읽어 겹침 없이 나누고, 검토 버튼에 쓸 문장을 기억한다."""
        yield {
            "type": "progress",
            "bar": "계약서 페이지를 확인하고 있습니다.",
            "current": 0,
            "total": 1,
            "percent": 0,
        }
        page_count = _page_count(path)
        reading = Progress(total=max(page_count, 1), desc="계약서 읽는 중")
        docs: list[Document] = []
        try:
            yield reading.event()
            for doc in PyPDFLoader(str(path)).lazy_load():
                docs.append(doc)
                reading.advance()
                yield reading.event()
        finally:
            reading.close()

        splitter = build_splitter(CONTRACT_CHUNK_SIZE, CONTRACT_CHUNK_OVERLAP)
        splitting = Progress(total=max(len(docs), 1), desc="계약서 문장 분할")
        sentences: list[str] = []
        try:
            yield splitting.event()
            for doc in docs:
                parts = splitter.split_text(doc.page_content or "")
                sentences.extend(part.strip() for part in parts if part.strip())
                splitting.advance()
                yield splitting.event()
        finally:
            splitting.close()

        if not sentences:
            yield {"type": "error", "error": "계약서에서 텍스트를 찾지 못했습니다."}
            return

        # 분할이 끝난 뒤에만 교체해서, 실패해도 이전 계약서가 남는다.
        self.contract_name = path.name
        self.contract_chunks = sentences
        message = (
            "계약서 업로드가 완료되었습니다.\n"
            f"- {path.name}: {page_count}페이지, {len(sentences)}문장\n"
            "왼쪽의 계약서 검토 버튼으로 검토를 시작할 수 있습니다."
        )
        yield {
            "type": "done",
            "message": message,
            "contract_name": path.name,
            "contract_ready": True,
            "sentences": len(sentences),
            "files": self.rag_files,
        }

    def review_contract(self):
        """계약 문장을 순서대로 가이드라인과 비교하고, 문장마다 결과를 보낸다."""
        if not self.contract_chunks:
            yield {"type": "error", "error": "계약서를 먼저 업로드해 주세요."}
            return

        store = self._open_store()
        if store._collection.count() == 0:
            yield {"type": "error", "error": "RAG 파일을 먼저 업로드해 주세요."}
            return

        chunks = list(self.contract_chunks)
        progress = Progress(total=len(chunks), desc="계약서 검토")
        fixed = 0
        chain = self._build_review_chain()
        try:
            yield progress.event()
            for index, sentence in enumerate(chunks, start=1):
                docs = store.similarity_search(sentence, k=RETRIEVE_K)
                result: ClauseReview = chain.invoke(
                    {"context": format_context(docs), "sentence": sentence}
                )
                revised = pick_revision(sentence, result.needs_fix, result.revised)
                if revised:
                    fixed += 1
                yield {
                    "type": "sentence",
                    "index": index,
                    "original": sentence,
                    "revised": revised,
                }
                progress.advance()
                yield progress.event()
        finally:
            progress.close()

        yield {
            "type": "done",
            "message": f"계약서 검토가 완료되었습니다. 검토 {len(chunks)}문장, 수정 {fixed}문장.",
            "contract_ready": True,
            "files": self.rag_files,
        }

    def ask(self, question: str) -> str:
        """일반 질문은 저장된 문서를 검색하지 않고 모델에 바로 보낸다."""
        llm = ChatOpenAI(model=CHAT_MODEL, temperature=0.4, timeout=60, max_retries=2)
        prompt = ChatPromptTemplate.from_messages(
            [("system", CHAT_SYSTEM), ("human", "{question}")]
        )
        message = (prompt | llm).invoke({"question": question})
        content = message.content
        if isinstance(content, str):
            return content.strip()
        return str(content).strip()

    def _build_review_chain(self):
        if self._review_chain is None:
            llm = ChatOpenAI(model=CHAT_MODEL, temperature=0, timeout=90, max_retries=2)
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", REVIEW_SYSTEM),
                    ("human", "[가이드라인 발췌]\n{context}\n\n[계약서 문장]\n{sentence}"),
                ]
            )
            self._review_chain = prompt | llm.with_structured_output(ClauseReview)
        return self._review_chain

    def _open_store(self) -> Chroma:
        if self._store is None:
            CHROMA_DIR.mkdir(parents=True, exist_ok=True)
            embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
            kwargs = {
                "collection_name": COLLECTION_NAME,
                "embedding_function": embeddings,
                "persist_directory": str(CHROMA_DIR),
            }
            try:
                self._store = Chroma(**kwargs, collection_metadata={"hnsw:space": "cosine"})
            except Exception:
                self._store = Chroma(**kwargs)
            self._hydrate_files()
        return self._store

    def _hydrate_files(self) -> None:
        if self.rag_files or self._store is None:
            return
        try:
            data = self._store._collection.get(include=["metadatas"])
        except Exception:
            return
        counts: dict[str, int] = {}
        pages: dict[str, set[int]] = defaultdict(set)
        for meta in data.get("metadatas") or []:
            name = (meta or {}).get("source")
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
            page = (meta or {}).get("page")
            if isinstance(page, int):
                pages[name].add(page)
        self.rag_files = [
            {"name": name, "chunks": count, "pages": len(pages.get(name, ()))}
            for name, count in counts.items()
        ]

    def _delete_source(self, source_name: str) -> None:
        store = self._store
        if store is None:
            return
        try:
            existing = store._collection.get(where={"source": source_name})
            ids = existing.get("ids") or []
            if ids:
                store.delete(ids=ids)
        except Exception:
            return

    def _upsert_file(self, record: dict[str, object]) -> None:
        self.rag_files = [item for item in self.rag_files if item.get("name") != record["name"]]
        self.rag_files.append(record)


def _page_count(path: Path) -> int:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ValueError("암호화된 PDF는 열 수 없습니다.")
        return len(reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"{path.name}을 열 수 없습니다.") from exc
