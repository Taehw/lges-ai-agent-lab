from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import (
    CHROMA_DIR,
    CHUNK_OVERLAP,
    CHUNK_SEPARATORS,
    CHUNK_SIZE,
    COLLECTION_NAME,
    HISTORY_TURN_LIMIT,
    MAX_DISTANCE,
    NO_INFO_MESSAGE,
    RETRIEVE_K,
    SYSTEM_PROMPT,
)
from .embeddings import LocalBgeM3Embeddings
from .llm import LocalExaoneLLM


class RAGPipeline:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._embeddings: LocalBgeM3Embeddings | None = None
        self._llm: LocalExaoneLLM | None = None
        self._vectorstore: Chroma | None = None
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=CHUNK_SEPARATORS,
            length_function=len,
        )
        self._histories: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.uploaded_files: list[dict[str, object]] = []
        self.status = {
            "embeddings_ready": False,
            "llm_ready": False,
            "message": "대기 중",
        }

    def ensure_embeddings(self) -> LocalBgeM3Embeddings:
        with self._lock:
            if self._embeddings is None:
                self.status["message"] = "임베딩 모델을 불러오는 중입니다"
                self._embeddings = LocalBgeM3Embeddings()
                chroma_kwargs = {
                    "collection_name": COLLECTION_NAME,
                    "embedding_function": self._embeddings,
                    "persist_directory": str(CHROMA_DIR),
                }
                try:
                    self._vectorstore = Chroma(
                        **chroma_kwargs,
                        collection_metadata={"hnsw:space": "cosine"},
                    )
                except Exception:
                    self._vectorstore = Chroma(**chroma_kwargs)
                self.status["embeddings_ready"] = True
                self.status["message"] = "임베딩 모델 준비 완료"
                self._hydrate_files()
            return self._embeddings

    def ensure_llm(self) -> LocalExaoneLLM:
        with self._lock:
            if self._llm is None:
                self.status["message"] = "LLM을 불러오는 중입니다"
                self._llm = LocalExaoneLLM()
                self.status["llm_ready"] = True
                self.status["message"] = "준비 완료"
            return self._llm

    def _store(self) -> Chroma:
        self.ensure_embeddings()
        assert self._vectorstore is not None
        return self._vectorstore

    def ingest_pdfs(self, saved_paths: list[Path]) -> dict[str, object]:
        store = self._store()
        added_files: list[dict[str, object]] = []
        total_chunks = 0

        for path in saved_paths:
            loader = PyPDFLoader(str(path), mode="page")
            pages = loader.load()
            chunks = self._splitter.split_documents(pages)
            if not chunks:
                added_files.append(
                    {
                        "name": path.name,
                        "chunks": 0,
                        "pages": len(pages),
                        "warning": "추출된 텍스트가 없습니다",
                    }
                )
                continue

            documents: list[Document] = []
            ids: list[str] = []
            for index, chunk in enumerate(chunks):
                source_name = path.name
                page = chunk.metadata.get("page", 0)
                page_number = int(page) + 1 if isinstance(page, int) else page
                chunk.metadata["source"] = source_name
                chunk.metadata["page"] = page_number
                documents.append(chunk)
                ids.append(f"{source_name}::{uuid.uuid4().hex}::{index}")

            self._delete_by_source(store, path.name)
            store.add_documents(documents=documents, ids=ids)
            total_chunks += len(documents)
            record = {"name": path.name, "chunks": len(documents), "pages": len(pages)}
            added_files.append(record)
            self._upsert_file_record(record)

        return {"files": added_files, "chunk_count": total_chunks}

    def chat(self, question: str, session_id: str) -> dict[str, object]:
        question = (question or "").strip()
        if not question:
            return {"answer": "질문을 입력해 주세요.", "sources": []}

        store = self._store()
        history = self._histories[session_id]
        search_query = self._build_search_query(question, history)

        if store._collection.count() == 0:
            answer = NO_INFO_MESSAGE
            self._remember(session_id, question, answer)
            return {"answer": answer, "sources": []}

        pairs = store.similarity_search_with_score(search_query, k=RETRIEVE_K)
        relevant = [(doc, score) for doc, score in pairs if score <= MAX_DISTANCE]

        if not relevant:
            answer = NO_INFO_MESSAGE
            self._remember(session_id, question, answer)
            return {"answer": answer, "sources": []}

        sources = [self._source_payload(doc, score) for doc, score in relevant]
        context = self._format_context(relevant)
        user_content = (
            f"[문서]\n{context}\n\n"
            f"[질문]\n{question}\n\n"
            "문서 근거로만 간결하게 답하세요."
        )

        llm = self.ensure_llm()
        answer = llm.generate(
            user_content=user_content,
            history=history[-HISTORY_TURN_LIMIT:],
            system_prompt=SYSTEM_PROMPT,
        ).strip()

        if not answer:
            answer = NO_INFO_MESSAGE

        if self._is_no_info(answer):
            answer = NO_INFO_MESSAGE
            self._remember(session_id, question, answer)
            return {"answer": answer, "sources": []}

        self._remember(session_id, question, answer)
        return {"answer": answer, "sources": sources}

    def clear_history(self, session_id: str) -> None:
        self._histories.pop(session_id, None)

    def list_files(self) -> list[dict[str, object]]:
        return list(self.uploaded_files)

    def _hydrate_files(self) -> None:
        store = self._vectorstore
        if store is None:
            return
        try:
            data = store._collection.get(include=["metadatas"])
        except Exception:
            return
        counts: dict[str, int] = {}
        pages: dict[str, int] = {}
        for meta in data.get("metadatas") or []:
            name = (meta or {}).get("source")
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
            page = (meta or {}).get("page")
            if isinstance(page, int):
                pages[name] = max(pages.get(name, 0), page)
        self.uploaded_files = [
            {"name": name, "chunks": count, "pages": pages.get(name, 0)}
            for name, count in counts.items()
        ]

    @staticmethod
    def _delete_by_source(store: Chroma, source_name: str) -> None:
        try:
            existing = store._collection.get(where={"source": source_name})
            ids = existing.get("ids") or []
            if ids:
                store.delete(ids=ids)
        except Exception:
            pass

    def _upsert_file_record(self, record: dict[str, object]) -> None:
        self.uploaded_files = [
            item for item in self.uploaded_files if item.get("name") != record["name"]
        ]
        self.uploaded_files.append(record)

    @staticmethod
    def _build_search_query(question: str, history: list[dict[str, str]]) -> str:
        recent_users = [
            message["content"]
            for message in history[-HISTORY_TURN_LIMIT:]
            if message.get("role") == "user"
        ]
        if not recent_users:
            return question
        return " ".join(recent_users[-2:] + [question])

    @staticmethod
    def _format_context(pairs: list[tuple[Document, float]]) -> str:
        blocks = []
        for index, (doc, _score) in enumerate(pairs, start=1):
            source = doc.metadata.get("source", "unknown")
            page = doc.metadata.get("page", "?")
            blocks.append(f"[자료 {index}] ({source}, p.{page})\n{doc.page_content.strip()}")
        return "\n\n".join(blocks)

    @staticmethod
    def _source_payload(doc: Document, score: float) -> dict[str, object]:
        text = " ".join(doc.page_content.split())
        summary = text[:180] + ("…" if len(text) > 180 else "")
        return {
            "source": doc.metadata.get("source", "unknown"),
            "page": doc.metadata.get("page", "?"),
            "summary": summary,
            "score": round(float(score), 4),
        }

    @staticmethod
    def _is_no_info(text: str) -> bool:
        normalized = text.strip().strip("\"'").rstrip(".!。")
        return normalized == NO_INFO_MESSAGE or text.strip().startswith(NO_INFO_MESSAGE)

    def _remember(self, session_id: str, question: str, answer: str) -> None:
        history = self._histories[session_id]
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        if len(history) > HISTORY_TURN_LIMIT * 2:
            self._histories[session_id] = history[-HISTORY_TURN_LIMIT * 2 :]
