"""Ground Groq on retrieved chunks; citation URL is taken from the best hit in code.

Post-processing guarantees: invented URLs are stripped, the body is capped at
3 sentences, `NOT_IN_SOURCES` markers become a deterministic `unknown` answer,
and the citation/source comes only from chunks (never from model text).
Writes artifacts/generate/<request_id>.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.config_loader import get_config, resolve_path
from src.models import Answer, AnswerMode, RetrieveHit
from src.query.prompts import NOT_IN_SOURCES, SYSTEM_PROMPT, UNKNOWN, UNKNOWN_HINT

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


def pick_citation(user_text: str, hits: list[RetrieveHit]) -> RetrieveHit | None:
    """Best citation hit whose source URL is on the config allowlist."""
    from src.ingest.chunk import tag_section

    allowed = _allowlisted_urls()
    preferred = [h for h in hits if h.source_url in allowed]
    if not preferred:
        return None
    want = tag_section(user_text)
    if want != "overview":
        for hit in preferred:
            if hit.section == want:
                return hit
    return preferred[0]


def best_hit(user_text: str, hits: list[RetrieveHit]) -> RetrieveHit:
    """Best hit for citation: prefer a chunk whose section matches the query's
    section tag (computed with the same tagging as ingest), else the top hit."""
    hit = pick_citation(user_text, hits)
    return hit if hit is not None else hits[0]


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
    return "\n\n---\n\n".join(
        f"[excerpt {i + 1}] official page: {h.source_url}\n{h.text}"
        for i, h in enumerate(hits[:MAX_CONTEXT_HITS])
    )


def _write_trace(payload: dict) -> None:
    path = _trace_dir() / f"{payload['request_id']}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _fetched_date(hit: RetrieveHit | None) -> str | None:
    if hit and hit.fetched_at:
        return hit.fetched_at[:10]
    return None


def generate(user_text: str, hits: list[RetrieveHit], request_id: str) -> Answer:
    from src.llm.factory import get_llm

    chosen = pick_citation(user_text, hits)  # allowlisted sources only
    best_chunk_id = chosen.chunk_id if chosen else None
    llm = get_llm()
    system = SYSTEM_PROMPT
    user = f"Question: {user_text}\n\nOfficial source excerpts:\n\n{_context_block(hits)}"
    model = getattr(llm, "_model", "unknown")
    raw = llm.complete(system, user)

    mode: AnswerMode
    if NOT_IN_SOURCES in raw:
        mode = "unknown"
        # The answer card renders the citation link itself; keep the body free
        # of URLs so the final displayed answer contains exactly one link.
        body = UNKNOWN.format(hint="").strip()
        answer = Answer(
            text=body,
            mode=mode,
            request_id=request_id,
            source_url=chosen.source_url if chosen else None,
            last_updated=None,  # not grounded; no "last updated" claim
        )
    else:
        mode = "fact"
        answer = Answer(
            text=_clean_text(raw),
            mode=mode,
            request_id=request_id,
            source_url=chosen.source_url if chosen else None,
            last_updated=_fetched_date(chosen),
        )

    _write_trace(
        {
            "request_id": request_id,
            "mode": mode,
            "model": model,
            "question": user_text,
            "hit_ids": [h.chunk_id for h in hits[:MAX_CONTEXT_HITS]],
            "best_hit": best_chunk_id,
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
