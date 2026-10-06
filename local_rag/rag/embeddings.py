from __future__ import annotations

from typing import List

from langchain_core.embeddings import Embeddings

from .config import EMBEDDING_MODEL_PATH


class LocalBgeM3Embeddings(Embeddings):
    """Load the local BGE-M3 checkpoint (Hugging Face / sentence-transformers format).

    The provided ``./models/bge-m3`` directory is a sentence-transformers model
    (pytorch weights), not a GGUF file, so llama-cpp-python cannot load it.
    """

    def __init__(self, model_path: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer

        path = str(model_path or EMBEDDING_MODEL_PATH)
        self.model = SentenceTransformer(path, local_files_only=True, device="cpu")
        self.model.max_seq_length = 512

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        cleaned = [text.replace("\n", " ") for text in texts]
        vectors = self.model.encode(
            cleaned,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> List[float]:
        vector = self.model.encode(
            text.replace("\n", " "),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vector.tolist()
