from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

def page_id_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] or "page"


def make_chunk_id(page_id: str, section: str, n: int) -> str:
    return f"{page_id}::{section}::{n}"


def new_request_id() -> str:
    return uuid.uuid4().hex


AnswerMode = Literal[
    "fact",
    "refuse_advice",
    "refuse_pii",
    "refuse_performance",
    "refuse_scope",
    "plan_limitation",
    "clarify",
    "unknown",
]


@dataclass(frozen=True)
class RawPage:
    page_id: str
    url: str
    fetched_at: str
    http_status: int
    scheme_or_amc: str
    text_path: str
    html_path: str | None = None


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source_url: str
    scheme_or_amc: str
    section: str
    text: str
    fetched_at: str


@dataclass(frozen=True)
class RetrieveHit:
    chunk_id: str
    score: float
    source_url: str
    section: str
    text: str
    fetched_at: str = ""
    scheme_or_amc: str = ""


@dataclass
class Answer:
    text: str
    mode: AnswerMode
    request_id: str
    source_url: str | None = None
    last_updated: str | None = None
    # NAV-date of the saved value itself, when the answer quotes one (freshness
    # template). Kept distinct from `last_updated` (the page-collection date).
    nav_date: str | None = None
    # True when the model said the fact is NOT in the excerpts it saw — this is
    # retrieval sparsity, not proof of absence, so the pipeline may retry once
    # with a lexical second-chance retrieval on the same restricted page.
    needs_lexical_retry: bool = False
    # The context-resolved question this turn was actually answered against —
    # the raw user text plus any carried fact phrasing (clarify-reply or
    # follow-up). The UI carries this forward as the next turn's context.
    effective_question: str | None = None
    trace_paths: dict[str, str] = field(default_factory=dict)
