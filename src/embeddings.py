from __future__ import annotations

from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config_loader import get_config


@lru_cache(maxsize=2)
def get_embedder(model_name: str) -> SentenceTransformer:
    return SentenceTransformer(model_name)


def embed_texts(texts: list[str], model_name: str | None = None, batch_size: int = 32) -> np.ndarray:
    if not texts:
        dim = embedder_dim(model_name)
        return np.zeros((0, dim), dtype=np.float32)
    name = model_name or get_config()["embedding"]["model"]
    model = get_embedder(name)
    vectors = model.encode(
        texts, batch_size=batch_size, convert_to_numpy=True, show_progress_bar=False
    )
    return np.asarray(vectors, dtype=np.float32)


def embedder_dim(model_name: str | None = None) -> int:
    name = model_name or get_config()["embedding"]["model"]
    return int(get_embedder(name).get_sentence_embedding_dimension())
