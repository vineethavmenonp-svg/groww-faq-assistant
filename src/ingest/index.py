"""Write embeddings into Chroma at data/chroma/ (replace collection on re-ingest).

Reads artifacts/chunks/chunks.jsonl + artifacts/embed/{ids.json,vectors.npy},
then replaces the configured collection with those points:
id = chunk_id, document = chunk text, metadata = source_url / scheme_or_amc /
section / fetched_at, embedding = MiniLM vector from the embed step.
"""

from __future__ import annotations

import json

from src.chroma_store import (
    add_chunks,
    get_client,
    get_or_replace_collection,
    query,
)
from src.config_loader import get_config, resolve_path
from src.embeddings import embed_texts
from src.models import Chunk

ADD_BATCH = 64


def read_embed_artifacts() -> tuple[list[Chunk], list[str], "np.ndarray"]:
    """chunks.jsonl rows and vectors.npy, in the same id order."""
    import numpy as np

    config = get_config()
    chunks_dir = resolve_path(str(config.get("artifacts", {}).get("chunks_dir", "artifacts/chunks")))
    embed_dir = resolve_path(str(config.get("artifacts", {}).get("embed_dir", "artifacts/embed")))
    chunks_path = chunks_dir / "chunks.jsonl"
    if not chunks_path.exists():
        raise FileNotFoundError(f"Missing {chunks_path} - run `python -m src.ingest chunk` first.")
    ids_path = embed_dir / "ids.json"
    vectors_path = embed_dir / "vectors.npy"
    if not ids_path.exists() or not vectors_path.exists():
        raise FileNotFoundError(
            f"Missing embeddings in {embed_dir} - run `python -m src.ingest embed` first."
        )

    with ids_path.open(encoding="utf-8") as fh:
        embedding_ids: list[str] = json.load(fh)
    vectors = np.load(vectors_path)
    with chunks_path.open(encoding="utf-8") as fh:
        chunks = [Chunk(**json.loads(line)) for line in fh if line.strip()]
    if len(chunks) != len(embedding_ids) or vectors.shape[0] != len(embedding_ids):
        raise ValueError(
            f"Embedding artifacts out of sync: {len(chunks)} chunks, "
            f"{len(embedding_ids)} ids, {vectors.shape[0]} vectors. "
            "Re-run `python -m src.ingest chunk` then `python -m src.ingest embed`."
        )
    if [c.chunk_id for c in chunks] != embedding_ids:
        raise ValueError("chunks.jsonl order does not match ids.json. Re-run the full ingest.")
    return chunks, embedding_ids, vectors


def smoke_query(collection) -> None:
    """Doc smoke check: print top hits + URLs for a fact query."""
    hits = query(
        collection,
        embed_texts(["expense ratio HDFC Large Cap Fund Direct Growth"])[0],
        top_k=3,
    )
    print("Smoke query 'expense ratio HDFC Large Cap Fund Direct Growth':")
    for hit in hits:
        print(f"  {hit.score:.4f} [{hit.section}] {hit.source_url}")


def index_chunks() -> None:
    config = get_config()
    chroma_cfg = config.get("chroma", {})
    collection_name = str(chroma_cfg.get("collection", "groww_mf_faq"))
    distance = str(chroma_cfg.get("distance", "cosine"))

    chunks, _ids, vectors = read_embed_artifacts()

    client = get_client()  # persist dir from config; created if needed
    collection = get_or_replace_collection(client, collection_name, distance)
    for start in range(0, len(chunks), ADD_BATCH):
        batch_chunks = chunks[start : start + ADD_BATCH]
        batch_vectors = vectors[start : start + ADD_BATCH]
        add_chunks(collection, batch_chunks, batch_vectors)

    count = collection.count()
    if count != len(chunks):
        raise RuntimeError(f"Expected {len(chunks)} points, collection has {count}.")
    print(
        f"Indexed {count} point(s) into collection '{collection_name}' "
        f"({distance} distance; replaced the previous version, no duplicates)."
    )
    smoke_query(collection)
