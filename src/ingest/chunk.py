"""Turn artifacts/raw into artifacts/chunks/chunks.jsonl.

Section-aware strategy (config: `chunk.strategy`): build line blocks from the
scraped text, keeping a known label and its value in the same block. A block
that exceeds `chunk.size` is recursively split with `chunk.overlap` overlap.
Every chunk is tagged with a section (`expense_ratio`, `exit_load`, `min_sip`,
`lock_in`, `riskometer`, `benchmark`, `statements`, else `overview`) and gets
chunk_id `{page_id}::{section}::{n}`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config_loader import get_config, resolve_path
from src.models import Chunk, RawPage, make_chunk_id

SECTION_OVERVIEW = "overview"

# Priority-ordered section tags (first match wins, else overview).
SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("expense_ratio", re.compile(r"total\s+expense\s+ratio|\bter\b|expense\s+ratio", re.IGNORECASE)),
    ("exit_load", re.compile(r"exit\s+load", re.IGNORECASE)),
    ("min_sip", re.compile(r"min\.?\s*for\s+sip|min(?:imum)?\s+(?:sip|monthly\s+investment)|sip\s+amount", re.IGNORECASE)),
    ("lock_in", re.compile(r"lock[-_ ]?in", re.IGNORECASE)),
    ("riskometer", re.compile(r"riskometer", re.IGNORECASE)),
    ("benchmark", re.compile(r"benchmark", re.IGNORECASE)),
    ("statements", re.compile(
        r"capital[- ]gains\s+statement|statement\s+of\s+account"
        r"|consolidated\s+account\s+statement|\bcas\b|\bsoa\b",
        re.IGNORECASE,
    )),
]

# Bare-label lines whose value is (almost always) the next line; pairing keeps
# label + value inside one block, so they can never be split across chunks.
LABEL_RE = re.compile(
    r"^(?:"
    r"total\s+expense\s+ratio|expense\s+ratio|\bter\b|"
    r"exit\s+load|"
    r"lock[-_ ]?in(?:\s+period)?|"
    r"min\.?\s*for\s+(?:sip|investment)|min(?:imum)?\s+(?:sip|investment)|"
    r"riskometer|"
    r"(?:fund\s+)?benchmark|"
    r"investment\s+objective|"
    r"fund\s+(?:house|manager|size)|aum|"
    r"launch\s+date|"
    r"nav\s*:|category|rating"
    r")",
    re.IGNORECASE,
)
_LABEL_LINE_MAX = 60
_MIN_CHARS = 40  # drop blocks that are too thin to chunk meaningfully


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def tag_section(text: str) -> str:
    for section, pattern in SECTION_PATTERNS:
        if pattern.search(text):
            return section
    return SECTION_OVERVIEW


def extract_raw_pages() -> list[RawPage]:
    """Read scrape records from artifacts/raw (skips failures.json)."""
    config = get_config()
    raw_dir = resolve_path(str(config.get("artifacts", {}).get("raw_dir", "artifacts/raw")))
    pages: list[RawPage] = []
    for record_path in sorted(raw_dir.glob("*.json")):
        if record_path.name == "failures.json":
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        page = RawPage(
            page_id=str(record["page_id"]),
            url=str(record["url"]),
            fetched_at=str(record.get("fetched_at", "")),
            http_status=int(record.get("http_status", 0)),
            scheme_or_amc=str(record.get("scheme_or_amc", "")),
            text_path=str(record.get("text_path", "")),
            html_path=record.get("html_path"),
        )
        if page.http_status == 200 and page.text_path:
            pages.append(page)
        else:
            print(f"[skip] {record_path.name}: not a successful scrape record")
    if not pages:
        raise FileNotFoundError(
            "No scraped pages in artifacts/raw - run `python -m src.ingest load` first."
        )
    return pages


def _is_bare_label(line: str) -> bool:
    """True for short lines that look like a label whose value is the next line."""
    match = LABEL_RE.match(line)
    if not match or len(line) > _LABEL_LINE_MAX:
        return False
    return not line[match.end():].strip()


def build_units(text: str) -> list[str]:
    """Merge a bare label line with the following line; everything else stays put."""
    lines = [line.strip() for line in text.splitlines()]
    units: list[str] = []
    i = 0
    while i < len(lines):
        if _is_bare_label(lines[i]) and i + 1 < len(lines) and lines[i + 1].strip():
            units.append(f"{lines[i]}\n{lines[i + 1]}")
            i += 2
        else:
            if lines[i]:
                units.append(lines[i])
            i += 1
    return units


def _split_with_overlap(text: str, size: int, overlap: int) -> list[str]:
    """Split an oversized block by words, carrying ~`overlap` chars over."""
    if len(text) <= size:
        return [text]
    words = text.split()
    if len(words) <= 1:  # single giant token: character split stays safe
        step = max(size - overlap, 1)
        return [text[i:i + size] for i in range(0, len(text), step)]

    pieces: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for word in words:
        add = len(word) if not cur else len(word) + 1
        if cur and cur_len + add > size:
            pieces.append(" ".join(cur))
            tail: list[str] = []
            tail_len = 0
            for token in reversed(cur):
                token_len = len(token) + (1 if tail else 0)
                if tail_len + token_len > overlap and tail:
                    break
                tail.insert(0, token)
                tail_len += token_len
            cur = tail
            cur_len = sum(len(t) for t in cur) + max(len(cur) - 1, 0)
            add = len(word) if not cur else len(word) + 1
        cur.append(word)
        cur_len += add
    if cur:
        pieces.append(" ".join(cur))
    return [piece for piece in pieces if piece.strip()]


def build_blocks(units: list[str], size: int) -> list[str]:
    """Greedy blocks up to `size` chars; break early on a section change once
    the current block is at least half size so chunks are section-clean."""
    blocks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    cur_section: str | None = None

    for unit in units:
        unit_section = tag_section(unit)
        add = len(unit) + (1 if cur else 0)
        boundary = cur and (
            cur_len + add > size
            or (
                cur_section is not None
                and unit_section != cur_section
                and cur_len >= size // 2
            )
        )
        if boundary:
            blocks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(unit)
        cur_len += add
        if cur_section is None:
            cur_section = unit_section

    if cur:
        blocks.append("\n".join(cur))

    # Merge stray tiny blocks into their neighbour instead of emitting noise.
    merged: list[str] = []
    for block in blocks:
        if (
            merged
            and len(block) < _MIN_CHARS
            and len(merged[-1]) + len(block) + 1 <= size + size // 2
        ):
            merged[-1] = f"{merged[-1]}\n{block}"
        else:
            merged.append(block)
    return [block for block in merged if len(block.strip()) >= _MIN_CHARS]


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Section-aware block building; oversized pieces get recursive split."""
    return [
        piece
        for block in build_blocks(build_units(text), size)
        for piece in _split_with_overlap(block, size, overlap)
    ]


def chunk_pages() -> list[Chunk]:
    """Chunk all scraped pages; writes chunks.jsonl + summary.json (full replace)."""
    config = get_config()
    chunk_cfg = config.get("chunk", {})
    strategy = chunk_cfg.get("strategy", "section_aware")
    if strategy != "section_aware":
        raise ValueError(f"Unsupported chunk strategy: {strategy!r}")
    size = int(chunk_cfg.get("size", 800))
    overlap = int(chunk_cfg.get("overlap", 120))
    chunks_dir = resolve_path(str(config.get("artifacts", {}).get("chunks_dir", "artifacts/chunks")))
    chunks_dir.mkdir(parents=True, exist_ok=True)

    chunks: list[Chunk] = []
    page_stats: dict[str, dict[str, Any]] = {}
    for page in extract_raw_pages():
        text = (resolve_path(page.text_path)).read_text(encoding="utf-8")
        pieces = chunk_text(text, size=size, overlap=overlap)
        section_seq: dict[str, int] = {}
        page_by_section: dict[str, int] = {}
        for piece in pieces:
            section = tag_section(piece)
            n = section_seq.get(section, 0)
            section_seq[section] = n + 1
            page_by_section[section] = page_by_section.get(section, 0) + 1
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(page.page_id, section, n),
                    source_url=page.url,
                    scheme_or_amc=page.scheme_or_amc,
                    section=section,
                    text=piece,
                    fetched_at=page.fetched_at,
                )
            )
        page_stats[page.page_id] = {"total": len(pieces), "by_section": dict(sorted(page_by_section.items()))}
        print(f"[chunked] {page.page_id}: {len(pieces)} chunk(s)")

    chunks_path = chunks_dir / "chunks.jsonl"
    with chunks_path.open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk.__dict__, ensure_ascii=False) + "\n")

    by_section: dict[str, int] = {}
    for chunk in chunks:
        by_section[chunk.section] = by_section.get(chunk.section, 0) + 1
    summary = {
        "created_at": now_utc_iso(),
        "strategy": strategy,
        "size": size,
        "overlap": overlap,
        "total_pages": len(page_stats),
        "total_chunks": len(chunks),
        "by_section": dict(sorted(by_section.items())),
        "pages": page_stats,
    }
    (chunks_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(chunks)} chunk(s) -> {chunks_path} (+ summary.json)")
    return chunks
