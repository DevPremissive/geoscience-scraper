from __future__ import annotations

import json
import logging
import os
from typing import Optional

from config import CHROMA_DB

logger = logging.getLogger(__name__)


class OllamaEmbeddingModel:
    _instance: Optional[OllamaEmbeddingModel] = None
    _model_name: str

    @classmethod
    def get(cls, model_name: str = "nomic-embed-text") -> OllamaEmbeddingModel:
        if cls._instance is None or cls._model_name != model_name:
            cls._instance = cls(model_name)
        return cls._instance

    def __init__(self, model_name: str = "nomic-embed-text"):
        self.model_name = model_name
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        batch_size = 32
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            raw_batch = texts[i : i + batch_size]
            embedded = None
            for max_chars in (1400, 1200, 1000, 800, 600, 400):
                batch = [t[:max_chars] for t in raw_batch]
                resp = httpx.post(
                    f"{self.host}/api/embed",
                    json={"model": self.model_name, "input": batch},
                    timeout=60,
                )
                if resp.status_code == 200:
                    embedded = resp.json()["embeddings"]
                    break
                if "context length" not in resp.text:
                    resp.raise_for_status()
            if embedded is None:
                raise RuntimeError("embed: text too dense for model even at 400 chars")
            all_embeddings.extend(embedded)
        return all_embeddings


class EmbeddingModel:
    _instance: Optional[EmbeddingModel] = None
    _model_name: str

    @classmethod
    def get(cls, model_name: str = "all-MiniLM-L6-v2") -> EmbeddingModel:
        if cls._instance is None or cls._model_name != model_name:
            cls._instance = cls(model_name)
        return cls._instance

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def _ensure_loaded(self):
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(self.model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        max_chars = 1400
        texts_to_embed = [t[:max_chars] for t in texts]
        try:
            embeddings = self._model.encode(texts_to_embed, normalize_embeddings=True, show_progress_bar=False)
            if embeddings.ndim == 1:
                embeddings = embeddings.reshape(1, -1)
            return embeddings.tolist()
        except RuntimeError:
            pass
        lo, hi = 100, 1400
        while lo < hi:
            mid = (lo + hi) // 2
            truncated = [t[:mid] for t in texts_to_embed]
            try:
                embeddings = self._model.encode(truncated, normalize_embeddings=True, show_progress_bar=False)
                if embeddings.ndim == 1:
                    embeddings = embeddings.reshape(1, -1)
                return embeddings.tolist()
            except RuntimeError:
                hi = mid
        dim = self._model.get_sentence_embedding_dimension()
        results = []
        for t in texts:
            try:
                emb = self._model.encode([t[:100]], normalize_embeddings=True, show_progress_bar=False)
                if emb.ndim == 1:
                    emb = emb.reshape(1, -1)
                results.append(emb.tolist()[0])
            except RuntimeError:
                results.append([0.0] * dim)
        return results


def _get_embedder(backend: str, model_name: str):
    effective_backend = os.environ.get("EMBEDDING_BACKEND", backend)
    if effective_backend == "ollama":
        return OllamaEmbeddingModel.get(model_name)
    return EmbeddingModel.get(model_name)


class VectorStore:
    def __init__(
        self,
        collection_name: str = "geo_canada",
        embedding_model: str = "nomic-embed-text",
        embedding_backend: str = "ollama",
    ):
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        self._setup_chroma()
        self._embedding_model = _get_embedder(embedding_backend, embedding_model)
        self._model_initialized = False

    def _init_model(self, chunks: list):
        if not self._model_initialized:
            sample = [c["text"][:500] for c in chunks[:3]]
            self._embedding_model.embed(sample)
            self._model_initialized = True

    def _setup_chroma(self):
        import chromadb
        CHROMA_DB.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(CHROMA_DB))
        try:
            self._collection = self._client.get_collection(name=self.collection_name)
        except Exception:
            self._collection = self._client.create_collection(name=self.collection_name)

    def upsert(self, chunks: list) -> int:
        if not chunks:
            return 0
        self._init_model(chunks)
        texts = [c["text"] for c in chunks]
        vectors = self._embedding_model.embed(texts)

        seen_ids: set = set()
        chunks = [c for c in chunks if not (c["id"] in seen_ids or seen_ids.add(c["id"]))]
        texts = [c["text"] for c in chunks]
        vectors = self._embedding_model.embed(texts)

        ids = [c["id"] for c in chunks]
        metas = [c["metadata"] for c in chunks]
        json_metas = []
        for m in metas:
            serialized = {}
            for k, v in m.items():
                if v is None:
                    pass
                elif isinstance(v, (list, dict, set)):
                    serialized[k] = json.dumps(v)
                elif isinstance(v, (str, int, float, bool)):
                    serialized[k] = v
                else:
                    serialized[k] = str(v)
            json_metas.append(serialized)

        CHROMA_PAGE = 2000
        try:
            for page_start in range(0, len(ids), CHROMA_PAGE):
                sl = slice(page_start, page_start + CHROMA_PAGE)
                count = sl.stop - sl.start if sl.start < len(ids) else 0
                if count <= 0:
                    continue
                self._collection.upsert(
                    ids=ids[sl],
                    documents=texts[sl],
                    metadatas=json_metas[sl],
                    embeddings=vectors[sl],
                )
        except ValueError as e:
            if "batch" in str(e).lower():
                logger.warning(f"Batch upsert failed ({e}): splitting into smaller pages of 1000")
                for page_start in range(0, len(ids), 1000):
                    sl = slice(page_start, page_start + 1000)
                    count = sl.stop - sl.start if sl.start < len(ids) else 0
                    if count <= 0:
                        continue
                    self._collection.upsert(
                        ids=ids[sl],
                        documents=texts[sl],
                        metadatas=json_metas[sl],
                        embeddings=vectors[sl],
                    )
            elif "dimension" in str(e).lower():
                logger.warning(f"Embedding dimension mismatch in collection '{self.collection_name}'")
                self._client.delete_collection(self.collection_name)
                self._collection = self._client.create_collection(name=self.collection_name)
                for page_start in range(0, len(ids), CHROMA_PAGE):
                    sl = slice(page_start, page_start + CHROMA_PAGE)
                    count = sl.stop - sl.start if sl.start < len(ids) else 0
                    if count <= 0:
                        continue
                    self._collection.upsert(
                        ids=ids[sl],
                        documents=texts[sl],
                        metadatas=json_metas[sl],
                        embeddings=vectors[sl],
                    )
            else:
                raise
        logger.info(f"Chroma: upserted {len(chunks)} chunks ({len(vectors[0])} dims)")
        return len(chunks)

    def search(self, query, filters=None, limit=10):
        q_vec = self._embedding_model.embed([query])[0]
        result = self._collection.query(query_embeddings=[q_vec], where=filters or None, n_results=limit)
        return [
            {"id": rid, "text": doc, "score": dist}
            for rid, doc, dist in zip(result["ids"][0], result["documents"][0], result["distances"][0])
        ]
