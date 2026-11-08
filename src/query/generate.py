"""Ground Groq on retrieved chunks; the citation URL comes only from the
excerpt the answer actually cites (first line `SOURCES: n,n`), never from
"whatever was retrieved first".

Post-processing guarantees: invented URLs are stripped, the body is capped at
3 sentences (SOURCES line excluded), the cited hit must belong to the
answer's evidence, `NOT_IN_SOURCES` markers become a deterministic `unknown`
answer with no attached link, and URLs come only from code, never model text.
Writes artifacts/generate/<request_id>.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.config_loader import get_config, resolve_path
from src.models import Answer, AnswerMode, RetrieveHit
from src.query.prompts import NOT_IN_SOURCES, SYSTEM_PROMPT, UNKNOWN

MAX_CONTEXT_HITS = 4
MAX_SENTENCES = 3

_URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)


def now_utc_iso() -> str:
    from src.query.retrieve import now_utc_iso  # shared, keeps one implementation

    return now_utc_iso()


def _trace_dir() -> Path:
    path = resolve_path(str(get_config().get("artifacts", {}).get("generate_dir", "artifacts/generate")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _allowlisted_urls() -> set[str]:
    config = get_config()
    return {
        str(entry.get("url", ""))
        for entry in config.get("sources", {}).get("allowlist", [])
        if entry.get("url")
    }


def _cap_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z₹0-9])", text.strip())
    if len(parts) <= MAX_SENTENCES:
        return text.strip()
    return " ".join(parts[:MAX_SENTENCES]).strip()


def _clean_text(text: str) -> str:
    cleaned = _URL_RE.sub(" ", text)
    cleaned = cleaned.replace(NOT_IN_SOURCES, "").strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return _cap_sentences(cleaned)


def _context_block(hits: list[RetrieveHit]) -> str:
    """Excerpts labeled by their SAVED PAGE TITLE (scheme_or_amc), not the raw
    URL: URL slugs can differ from registered titles (e.g. the Flexi Cap page
    is saved as hdfc-equity-fund-direct-growth), so the model matches funds by
    title while the URL stays a code-side detail."""
    return "\n\n---\n\n".join(
        f'[excerpt {i + 1}] saved page title: "{h.scheme_or_amc}"\n{h.text}'
        for i, h in enumerate(hits[:MAX_CONTEXT_HITS])
    )


_SOURCES_LINE_RE = re.compile(r"\s*sources\s*:\s*([0-9,\s-]+)\s*$", re.IGNORECASE)


def _parse_sources_marker(raw: str, hits: list[RetrieveHit]) -> tuple[str, list[RetrieveHit]]:
    """Split a leading `SOURCES: 1,3` line off the model reply; returns
    (cleaned raw, cited hits). Cited indexes are validated against the
    context; anything else yields an empty citation list."""
    lines = raw.strip().splitlines()
    cited: list[RetrieveHit] = []
    if lines:
        match = _SOURCES_LINE_RE.match(lines[0].strip())
        if match:
            indexes = []
            for piece in match.group(1).replace("-", ",").split(","):
                piece = piece.strip()
                if piece.isdigit():
                    idx = int(piece) - 1
                    if 0 <= idx < len(hits[:MAX_CONTEXT_HITS]):
                        indexes.append(idx)
            cited = [hits[i] for i in indexes]
            lines = lines[1:]
    raw_rest = "\n".join(lines).strip()
    raw_rest = _SOURCES_LINE_RE.sub("", raw_rest)  # strip duplicated markers
    return raw_rest, cited


def _citation_for(hits: list[RetrieveHit], cited: list[RetrieveHit]) -> RetrieveHit | None:
    """Citation rule: a factual answer's URL must belong to the evidence the
    answer cites. Approved-but-irrelevant URLs are never attached.

    1. cited hits (marker) -> preferred allowlisted one.
    2. fallback: ALL context excerpts come from a single page -> that page is
       unambiguously the evidence.
    3. otherwise -> None (caller degrades to an unattributed limitation).
    """
    allowed = _allowlisted_urls()
    for hit in cited:
        if hit.source_url in allowed:
            return hit
    pages = {h.source_url for h in hits}
    if len(pages) == 1:
        only = hits[0]
        return only if only.source_url in allowed else None
    return None


def _write_trace(payload: dict) -> None:
    path = _trace_dir() / f"{payload['request_id']}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _fetched_date(hit: RetrieveHit | None) -> str | None:
    if hit and hit.fetched_at:
        return hit.fetched_at[:10]
    return None


def generate(user_text: str, hits: list[RetrieveHit], request_id: str) -> Answer:
    from src.llm.factory import get_llm
    from src.query.prompts import UNKNOWN_ATTRIBUTE

    llm = get_llm()
    system = SYSTEM_PROMPT
    user = f"Question: {user_text}\n\nOfficial source excerpts:\n\n{_context_block(hits)}"
    model = getattr(llm, "_model", "unknown")
    raw = llm.complete(system, user)

    raw_body, cited = _parse_sources_marker(raw, hits)
    chosen = _citation_for(hits, cited)

    mode: AnswerMode
    if NOT_IN_SOURCES in raw_body:
        mode = "unknown"
        # The fact is not in the matching excerpt(s): deterministic unknown,
        # NO attached link (nothing the model produced supports a citation).
        # `needs_lexical_retry`: this verdict means "not in what retrieval
        # returned", which may be a retrieval miss (e.g. a short literal token
        # like a lock-in phrase in a furniture-heavy chunk) — the pipeline may
        # retry once with a lexical second-chance retrieval on the same page.
        answer = Answer(
            text=UNKNOWN.format(hint="").strip(),
            mode=mode,
            request_id=request_id,
            source_url=None,
            last_updated=None,  # not grounded; no "last updated" claim
            needs_lexical_retry=True,
        )
    elif chosen is None:
        # A fact without attributable evidence is not publishable: refuse the
        # figures rather than attaching the first retrieved URL.
        mode = "unknown"
        answer = Answer(
            text=UNKNOWN_ATTRIBUTE,
            mode=mode,
            request_id=request_id,
            source_url=None,
            last_updated=None,
        )
    else:
        mode = "fact"
        answer = Answer(
            text=_clean_text(raw_body),
            mode=mode,
            request_id=request_id,
            source_url=chosen.source_url,
            last_updated=_fetched_date(chosen),
        )

    _write_trace(
        {
            "request_id": request_id,
            "mode": mode,
            "model": model,
            "question": user_text,
            "hit_ids": [h.chunk_id for h in hits[:MAX_CONTEXT_HITS]],
            "cited_hits": [h.chunk_id for h in cited],
            "cited_source_url": answer.source_url,
            "raw_response": raw,
            "final_text": answer.text,
            "source_url": answer.source_url,
            "created_at": now_utc_iso(),
        }
    )
    return answer


def write_score_gate_trace(
    question: str, request_id: str, hits: list[RetrieveHit], final_text: str, source_url: str | None
) -> None:
    """Trace for the `unknown` path decided before any LLM call (below min_score)."""
    _write_trace(
        {
            "request_id": request_id,
            "mode": "unknown",
            "model": "none (below min_score)",
            "question": question,
            "hit_ids": [h.chunk_id for h in hits],
            "best_hit": None,
            "raw_response": final_text,
            "final_text": final_text,
            "source_url": source_url,
            "created_at": now_utc_iso(),
        }
    )


def generate_refusal(
    mode: AnswerMode, text: str, request_id: str, source_url: str | None = None, question: str = ""
) -> Answer:
    """Trace-only path for refusals: no retrieval file, no LLM call."""
    if mode != "refuse_pii":  # PII never touches artifacts
        _write_trace(
            {
                "request_id": request_id,
                "mode": mode,
                "model": "none (refusal)",
                "question": question,
                "hit_ids": [],
                "best_hit": None,
                "raw_response": text,
                "final_text": text,
                "source_url": source_url,
                "created_at": now_utc_iso(),
            }
        )
    return Answer(text=text, mode=mode, request_id=request_id, source_url=source_url)
