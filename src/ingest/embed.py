"""Embed chunk text with MiniLM into artifacts/embed/.

Uses the same embedder as the query path (src/embeddings.py, model from
config.embedding.model), chunk text only, batch 32. Writes job.json
(`model`, `dim`, `chunk_count`, `embedded_at`) plus ids.json + vectors.npy.
Full replace each run so ids, vectors, and chunks.jsonl stay in sync.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config_loader import get_config, resolve_path
from src.embeddings import embed_texts, embedder_dim
from src.models import Chunk

BATCH_SIZE = 32


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_chunks() -> list[Chunk]:
    chunks_dir = resolve_path(str(get_config().get("artifacts", {}).get("chunks_dir", "artifacts/chunks")))
    chunks_path = chunks_dir / "chunks.jsonl"
    if not chunks_path.exists():
        raise FileNotFoundError(
            f"Missing {chunks_path} - run `python -m src.ingest chunk` first."
        )
    chunks: list[Chunk] = []
    with chunks_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            chunks.append(Chunk(**record))
    if not chunks:
        raise ValueError("chunks.jsonl is empty - rerun `python -m src.ingest chunk`.")
    ids = [c.chunk_id for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate chunk_ids found in chunks.jsonl.")
    return chunks


def write_artifacts(
    ids: list[str], vectors, model_name: str, embed_dir: Path, dim: int
) -> None:
    job = {
        "model": model_name,
        "dim": dim,
        "chunk_count": len(ids),
        "embedded_at": now_utc_iso(),
    }
    (embed_dir / "job.json").write_text(
        json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (embed_dir / "ids.json").write_text(
        json.dumps(ids, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    import numpy as np

    np.save(embed_dir / "vectors.npy", np.asarray(vectors, dtype=np.float32))


def embed_chunks() -> None:
    """Embed all chunks; full replace of artifacts/embed/ contents."""
    config = get_config()
    model_name = str(config["embedding"]["model"])
    embed_dir = resolve_path(str(config.get("artifacts", {}).get("embed_dir", "artifacts/embed")))

    chunks = read_chunks()
    texts = [c.text for c in chunks]
    print(f"Embedding {len(chunks)} chunk(s) with {model_name} (batch {BATCH_SIZE})...")
    vectors = embed_texts(texts, model_name=model_name, batch_size=BATCH_SIZE)

    dim = int(vectors.shape[1])
    expected = embedder_dim(model_name)
    if dim != expected:
        raise RuntimeError(f"Embedding dim {dim} does not match model dim {expected}.")

    embed_dir.mkdir(parents=True, exist_ok=True)
    ids = [c.chunk_id for c in chunks]
    write_artifacts(ids, vectors, model_name, embed_dir, dim)
    print(
        f"Embedded {len(ids)} vector(s) of dim {dim} -> {embed_dir} (job.json, ids.json, vectors.npy)"
    )
