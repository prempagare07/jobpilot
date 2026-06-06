from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import chromadb

from backend.config import settings


def hash_embedding(text: str, dimensions: int = 384) -> list[float]:
    vector = [0.0] * dimensions
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


@dataclass
class VectorStore:
    collection_name: str = "jobpilot_memory"

    def client(self) -> Any:
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(path=str(settings.chroma_path))

    def collection(self) -> Any:
        return self.client().get_or_create_collection(name=self.collection_name, embedding_function=None)

    def upsert_text(self, item_id: str, text: str, metadata: dict[str, str | int | float | bool] | None = None) -> None:
        self.collection().upsert(
            ids=[item_id],
            documents=[text],
            embeddings=[hash_embedding(text)],
            metadatas=[metadata or {}],
        )

    def query(self, text: str, limit: int = 5) -> list[dict[str, object]]:
        results = self.collection().query(query_embeddings=[hash_embedding(text)], n_results=limit)
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        distances = results.get("distances", [[]])[0]
        return [
            {"id": item_id, "document": document, "distance": distance}
            for item_id, document, distance in zip(ids, documents, distances, strict=False)
        ]


vector_store = VectorStore()
