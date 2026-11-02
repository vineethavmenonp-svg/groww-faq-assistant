from __future__ import annotations

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.api.types import EmbeddingFunction

from src.config_loader import get_config, resolve_path
from src.models import Chunk, RetrieveHit


class _DisabledEmbeddingFunction(EmbeddingFunction):
    """Chroma must not embed on its own; we pass MiniLM vectors in."""

    def __call__(self, input):
        raise RuntimeError("Pass embeddings explicitly; do not use Chroma's default embedder.")


def get_client(persist_dir: str | None = None):
    path = persist_dir or get_config()["chroma"]["persist_dir"]
    resolved = resolve_path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(resolved))


def get_or_replace_collection(client, name: str | None = None, distance: str | None = None) -> Collection:
    cfg = get_config()["chroma"]
    coll_name = name or cfg["collection"]
    space = distance or cfg["distance"]
    try:
        client.delete_collection(coll_name)
    except Exception:
        pass
    return client.create_collection(
        name=coll_name,
        metadata={"hnsw:space": space},
        embedding_function=_DisabledEmbeddingFunction(),
    )


def get_collection(client=None, name: str | None = None) -> Collection:
    cfg = get_config()["chroma"]
    chroma = client or get_client()
    coll_name = name or cfg["collection"]
    return chroma.get_collection(
        name=coll_name,
        embedding_function=_DisabledEmbeddingFunction(),
    )


def add_chunks(collection: Collection, chunks: list[Chunk], embeddings) -> None:
    if not chunks:
        return
    collection.add(
        ids=[c.chunk_id for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[
            {
                "source_url": c.source_url,
                "scheme_or_amc": c.scheme_or_amc,
                "section": c.section,
                "fetched_at": c.fetched_at,
            }
            for c in chunks
        ],
        embeddings=embeddings.tolist() if hasattr(embeddings, "tolist") else embeddings,
    )


def query(collection: Collection, query_embedding, top_k: int) -> list[RetrieveHit]:
    result = collection.query(
        query_embeddings=[query_embedding.tolist() if hasattr(query_embedding, "tolist") else query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    hits: list[RetrieveHit] = []
    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    for chunk_id, text, meta, distance in zip(ids, docs, metas, distances):
        meta = meta or {}
        score = 1.0 - float(distance)
        hits.append(
            RetrieveHit(
                chunk_id=chunk_id,
                score=score,
                source_url=str(meta.get("source_url", "")),
                section=str(meta.get("section", "")),
                text=text or "",
                fetched_at=str(meta.get("fetched_at", "")),
                scheme_or_amc=str(meta.get("scheme_or_amc", "")),
            )
        )
    return hits
