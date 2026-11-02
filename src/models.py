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
    trace_paths: dict[str, str] = field(default_factory=dict)
