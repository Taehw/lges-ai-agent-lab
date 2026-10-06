import shutil
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from recommender import config
from recommender.repository import RecipeRepository


def _embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=config.EMBEDDING_MODEL)


def build_index(repository: RecipeRepository, persist_dir: Path = config.CHROMA_DIR) -> int:
    """레시피 전체를 임베딩해 Chroma 인덱스를 새로 만든다."""
    if persist_dir.exists():
        shutil.rmtree(persist_dir)
    recipes = repository.all()
    docs = [
        Document(page_content=r.to_search_text(), metadata={"id": r.id, "name": r.name})
        for r in recipes
    ]
    Chroma.from_documents(
        docs,
        embedding=_embeddings(),
        ids=[r.id for r in recipes],
        collection_name=config.CHROMA_COLLECTION,
        persist_directory=str(persist_dir),
    )
    return len(docs)


def index_exists(persist_dir: Path = config.CHROMA_DIR) -> bool:
    return persist_dir.exists() and any(persist_dir.iterdir())


class ChromaRetriever:
    def __init__(self, persist_dir: Path = config.CHROMA_DIR):
        self._store = Chroma(
            collection_name=config.CHROMA_COLLECTION,
            embedding_function=_embeddings(),
            persist_directory=str(persist_dir),
        )

    def search(self, query: str, k: int) -> list[str]:
        return [doc.metadata["id"] for doc in self._store.similarity_search(query, k=k)]
