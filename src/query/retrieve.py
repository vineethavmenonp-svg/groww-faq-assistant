"""Embed the query with MiniLM and search the same Chroma collection.

Fails closed if the index is missing or empty (the UI shows
`Run: python -m src.ingest`). Writes artifacts/retrieve/<request_id>.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import re

from src.config_loader import get_config, resolve_path
from src.embeddings import embed_texts
from src.models import RetrieveHit

INDEX_HINT = "Run: python -m src.ingest"
MAX_CANDIDATES = 200  # tiny corpus: rank over everything, then rerank

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _trace_dir() -> Path:
    config = get_config()
    path = resolve_path(str(config.get("artifacts", {}).get("retrieve_dir", "artifacts/retrieve")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _open_collection():
    from src.chroma_store import get_client, get_collection

    config = get_config()
    chroma_cfg = config.get("chroma", {})
    try:
        collection = get_collection(get_client())
    except Exception as exc:  # collection missing / malformed / empty
        raise RuntimeError(f"Vector index is not available. {INDEX_HINT} ({exc})") from exc
    if collection.count() == 0:
        raise RuntimeError(f"Vector index exists but is empty. {INDEX_HINT}")
    if str(chroma_cfg.get("collection")) != collection.name:
        raise RuntimeError(
            f"Chroma collection mismatch (found '{collection.name}'). {INDEX_HINT}"
        )
    return collection


def retrieve(query_text: str, request_id: str) -> list[RetrieveHit]:
    config = get_config()
    retrieval_cfg = config.get("retrieval", {})
    top_k = int(retrieval_cfg.get("top_k", 4))

    from src.chroma_store import query as chroma_query
    from src.ingest.chunk import tag_section

    collection = _open_collection()
    query_vector = embed_texts([query_text])[0]
    candidates = chroma_query(collection, query_vector, top_k=min(collection.count(), MAX_CANDIDATES))
    if not candidates:
        raise RuntimeError(f"Vector index returned no results. {INDEX_HINT}")

    max_score = max(h.score for h in candidates)
    query_section = tag_section(query_text)
    query_tokens = set(_TOKEN_RE.findall(query_text.lower()))

    def scheme_tokens(hit: RetrieveHit) -> set[str]:
        tokens = _TOKEN_RE.findall(hit.chunk_id.split("::")[0].replace("-", " "))
        tokens += _TOKEN_RE.findall(hit.scheme_or_amc.lower())
        return set(tokens)

    def rank_key(hit: RetrieveHit) -> tuple[int, int, float]:
        section_match = int(hit.section == query_section) if query_section != "overview" else 0
        tokens = scheme_tokens(hit)
        scheme_match = sum(1 for token in tokens if token in query_tokens)
        return (section_match, scheme_match, hit.score)

    ranked = sorted(candidates, key=rank_key, reverse=True)
    hits = ranked[:top_k]

    trace = {
        "request_id": request_id,
        "query": query_text,
        "model": str(config["embedding"]["model"]),
        "top_k": top_k,
        "candidates": len(candidates),
        "query_section": query_section,
        "max_candidate_score": round(max_score, 4),
        "created_at": now_utc_iso(),
        "hits": [
            {
                "chunk_id": h.chunk_id,
                "score": h.score,
                "section": h.section,
                "source_url": h.source_url,
                "fetched_at": h.fetched_at,
                "text": h.text,
            }
            for h in hits
        ],
    }
    trace_path = _trace_dir() / f"{request_id}.json"
    trace_path.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return hits
