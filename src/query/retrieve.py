"""Embed the query with MiniLM and search the same Chroma collection.

Fails closed if the index is missing or empty (the UI shows
`Run: python -m src.ingest`). Writes artifacts/retrieve/<request_id>.json.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import re

from src.config_loader import get_config, resolve_path
from src.embeddings import embed_texts
from src.models import RetrieveHit

INDEX_HINT = "Run: python -m src.ingest"
MAX_CANDIDATES = 200  # tiny corpus: rank over everything, then rerank

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = {
    "the", "and", "for", "what", "which", "who", "how", "much", "does", "did",
    "his", "her", "their", "its", "this", "that", "are", "was", "were", "you",
    "your", "can", "will", "with", "from", "have", "has", "hdfc", "fund",
    "direct", "growth", "plan", "scheme", "tell", "give", "get", "under",
    "about", "into", "off", "out", "not", "all", "any", "per", "faq",
}


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


def retrieve(
    query_text: str, request_id: str, restrict_page: str | None = None
) -> list[RetrieveHit]:
    """Embed + search Chroma, then rerank.

    `restrict_page` limits context to the resolved scheme's page (evidence
    must belong to the correct scheme; other schemes' chunks are excluded so
    they can neither be cited nor confuse the model). Lexical overlap is part
    of the rerank because stored chunk *sections* can be mis-tagged (a chunk
    may lead with an unrelated label while carrying the needed field).
    """
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

    if restrict_page:
        candidates = [c for c in candidates if c.chunk_id.split("::")[0] == restrict_page]
        if not candidates:
            raise RuntimeError(
                f"No saved excerpts for the requested scheme page. {INDEX_HINT}"
            )

    max_score = max(h.score for h in candidates)
    query_section = tag_section(query_text)
    query_tokens = set(_TOKEN_RE.findall(query_text.lower()))
    content_tokens = {
        t for t in query_tokens
        if len(t) > 2 and t not in _STOPWORDS
    }

    def scheme_tokens(hit: RetrieveHit) -> set[str]:
        tokens = _TOKEN_RE.findall(hit.chunk_id.split("::")[0].replace("-", " "))
        tokens += _TOKEN_RE.findall(hit.scheme_or_amc.lower())
        return set(tokens)

    def rank_key(hit: RetrieveHit) -> tuple[int, int, int, float]:
        section_match = int(hit.section == query_section) if query_section != "overview" else 0
        hit_text_tokens = set(_TOKEN_RE.findall(hit.text.lower()))
        lexical = len(content_tokens & hit_text_tokens)
        tokens = scheme_tokens(hit)
        scheme_match = sum(1 for token in tokens if token in query_tokens)
        return (section_match, lexical, scheme_match, hit.score)

    ranked = sorted(candidates, key=rank_key, reverse=True)
    hits = ranked[:top_k]

    trace = {
        "request_id": request_id,
        "query": query_text,
        "model": str(config["embedding"]["model"]),
        "top_k": top_k,
        "restrict_page": restrict_page,
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


_RARITY_FRACTION = 0.15  # a "rare" token appears in < 15% of a page's chunks
_RARE_FLOOR = 2          # ...but never above 2 chunks on very small pages


def _stem(token: str) -> str:
    """Light suffix-stem so 'locked' matches a chunk's 'lock-in' → 'lock'."""
    for suffix in ("ing", "ed", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def lexical_retrieve(
    query_text: str, restrict_page: str, request_id: str
) -> list[RetrieveHit]:
    """Second-chance retrieval after a NOT_IN_SOURCES verdict.

    Dense retrieval can miss a short literal token (e.g. a lock-in phrase
    buried in a furniture-heavy chunk). This pass reads every saved chunk of
    ONE page (no fetch, no index rebuild) and reorders by rare-keyword
    coverage:

    - query content tokens are kept only when "rare" on that page (document
      frequency <= max(_RARE_FLOOR, _RARITY_FRACTION * N)) — scheme/page words
      like "elss"/"tax" occur in 40%+ of chunks and never drive the retry;
    - each chunk is scored by idf-weighted coverage of those rare tokens
      (tokens light-stemmed so 'locked' matches 'lock');
    - only chunks matching at least one rare token are returned, capped at
      top_k — the rest stay unseen, so a retry can only reorder evidence on
      the SAME resolved page, never widen the corpus.

    If no rare token matches anything, the retry is aborted upstream and the
    deterministic unknown stands. Writes artifacts/retrieve/<request_id>.json
    with method="lexical" for tracing.
    """
    config = get_config()
    top_k = int(config.get("retrieval", {}).get("top_k", 4))

    collection = _open_collection()
    result = collection.get(include=["documents", "metadatas"])
    ids = result.get("ids") or []
    docs = result.get("documents") or []
    metas = result.get("metadatas") or []
    page_rows = [
        (chunk_id, text, meta)
        for chunk_id, text, meta in zip(ids, docs, metas)
        if chunk_id.split("::")[0] == restrict_page
    ]
    if not page_rows:
        raise RuntimeError(
            f"No saved excerpts for the requested scheme page. {INDEX_HINT}"
        )

    content_tokens = {
        _stem(t) for t in _TOKEN_RE.findall(query_text.lower())
        if len(t) > 2 and t not in _STOPWORDS
    }
    chunk_tokens = [set(_TOKEN_RE.findall(text.lower())) for _, text, _ in page_rows]
    n_chunks = len(page_rows)
    doc_freq = {
        token: sum(1 for toks in chunk_tokens if token in toks)
        for token in content_tokens
    }
    rare_tokens = [
        token for token, df in doc_freq.items()
        if df and df <= max(_RARE_FLOOR, math.ceil(_RARITY_FRACTION * n_chunks))
    ]
    if not rare_tokens:
        raise RuntimeError("No rare query keyword matches this saved page.")

    idf = {
        token: math.log(1.0 + n_chunks / (doc_freq[token] or 1))
        for token in rare_tokens
    }

    scored: list[tuple[float, int, str]] = []
    for (chunk_id, _, _), toks in zip(page_rows, chunk_tokens):
        matched = [token for token in rare_tokens if token in toks]
        if not matched:
            continue  # no rare-keyword touch: stays unseen in this pass
        score = sum(idf[token] for token in matched)
        # rare tokens matched first, coverage next, stable order last
        scored.append((-len(matched), -score, chunk_id))
    if not scored:
        raise RuntimeError("No rare query keyword matches this saved page.")
    scored.sort()
    top = scored[:top_k]

    allowlist = get_config().get("sources", {}).get("allowlist", [])
    rows_by_id = {cid: (text, meta) for cid, text, meta in page_rows}
    hits: list[RetrieveHit] = []
    for _neg_len, neg_score, chunk_id in top:
        url = ""
        scheme = ""
        for entry in allowlist:
            if chunk_id.startswith(f"{entry.get('page_id', '')}::"):
                url = str(entry.get("url", ""))
                scheme = str(entry.get("scheme_or_amc", ""))
                break
        text, meta = rows_by_id[chunk_id]
        hits.append(RetrieveHit(
            chunk_id=chunk_id,
            score=round(-neg_score, 4),
            source_url=url,
            section=chunk_id.split("::")[1],
            text=text,
            fetched_at=str(meta.get("fetched_at", "")),
            scheme_or_amc=scheme,
        ))
    trace = {
        "request_id": request_id,
        "query": query_text,
        "method": "lexical",
        "top_k": top_k,
        "restrict_page": restrict_page,
        "candidates": n_chunks,
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
