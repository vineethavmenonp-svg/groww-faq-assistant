"""Question-type gates that run after the safety refusals, before retrieval.

General rules derived from the saved sources' titles and URL slugs (no
per-question routing, no hardcoded financial values):

- resolve_scheme: title tokens + URL-slug tokens (aliases like the Flexi Cap
  page saved as hdfc-equity-fund-direct-growth) are matched against the
  question. A token that appears in exactly one page is "distinctive"; the
  page that collects the most distinctive hits is the resolved scheme. No
  distinctive hit means the question does not name a scheme — the carried
  scheme applies only to follow-up phrased messages ("its benchmark?"),
  never to bare or unrelated turns -> clarify.
- unsupported AMC cue -> scope refusal (the five saved pages are HDFC only).
- Regular-plan mention -> plan limitation (saved pages document Direct only).
- latest/today NAV wording -> freshness template from the saved NAV line.

Conversation carry (single-use, supplied by the caller — the UI stores it
in st.session_state and New chat clears it):
- pending question: a clarify turn waits for a scheme; the reply that names
  one resolves the ORIGINAL fact from that pending question.
- last fact question + carried scheme: follow-up replies reuse the carried
  fact phrasing (scheme-only or repeat-only changes) by combining the texts;
  every resolved turn still performs fresh retrieval — history supplies the
  question's wording, never the answer.
"""

from __future__ import annotations

import re

from src.config_loader import get_config

_TOKEN_RE = re.compile(r"[a-z0-9&]+")

_FACT_CONCEPT_RE = re.compile(
    r"expense\s*ratio|\bter\b|exit\s*load|lock-?\s*-?in|"
    r"min(?:imum)?\s*(?:sip|investment)|sip\s*amount|\bsip\b|"
    r"\bnav\b|riskometer|benchmark|\bmanagers?\b|\baum\b|"
    r"launch\s*date|\brating\b|objective|statement|\bexpense\b|\bload\b|"
    r"\btell\s+me\b|\bdescribe\b|\boverview\b",
    re.IGNORECASE,
)

# Scheme-bound attributes: a question about one of these IS ambiguous without
# a scheme (which fund's NAV? whose exit load?) and must clarify. General /
# process questions (statement downloads, "best pizza on Mars", "tell me
# everything") are NOT scheme-bound — they run the generic evidence-only fact
# path instead of an incorrect "which scheme?" prompt.
_SCHEME_BOUND_RE = re.compile(
    r"expense\s*ratio|\bter\b|exit\s*load|lock-?\s*-?in|"
    r"min(?:imum)?\s*(?:sip|investment)|sip\s*amount|\bsip\b|"
    r"\bnav\b|riskometer|benchmark|\bmanagers?\b|\baum\b|"
    r"launch\s*date|\brating\b|objective|\bexpense\b|\bload\b",
    re.IGNORECASE,
)

# Follow-up phrasing: pronouns/continuations that refer back to the scheme
# (or question) of the previous contextual turn. A bare fact fragment with no
# such signal is treated as ambiguous — the carried scheme is never applied
# just because history mentions one (no cross-topic carry).
_FOLLOWUP_RE = re.compile(
    r"\b(?:its|it's)\b"
    r"|\bwhat\s+about\b"
    r"|\bhow\s+about\b"
    r"|^\s*and\b"
    r"|\b(?:also|again)\b"
    r"|\b(?:same|same one|same question)\b"
    r"|\b(?:this|that)\s+(?:fund|scheme|plan|one|investment)\b"
    r"|\bfor\s+(?:it|this|that)\b"
    r"|\babove\s+(?:scheme|fund)\b|\bfund\s+mentioned\s+above\b",
    re.IGNORECASE,
)

_REGULAR_PLAN_RE = re.compile(r"\bregular\b(?:\s+(?:plan|growth|dividend|payout))?", re.IGNORECASE)

_FRESHNESS_RE = re.compile(
    r"\b(?:today|todays|today's|now|live|latest|current)\b.{0,24}\bnav\b|"
    r"\bnav\b.{0,24}\b(?:today|todays|today's|now|live|latest|current)\b",
    re.IGNORECASE,
)

# Other houses/AMCs and platform funds that are outside the saved corpus.
_UNSUPPORTED_AMC_RE = re.compile(
    r"\b(?:sbi|icici|axis|kotak|nippon|uti|dsp|franklin|quant|mirae|parag|"
    r"idfc|invesco|edelweiss|canara|baroda|bandhan|motilal|samco|taurus|"
    r"aditya\s+birla|bajaj\s+finserv|reliance|hdfc\s+housing|groww)\b"
    r"[^\n]{0,12}\b(?:fund|bluechip|sip|nav|scheme|mutual)?\b",
    re.IGNORECASE,
)

# Falls back to False when the config index is unavailable (e.g. tests).
_SKIPWATCH = {"sip", "nav", "s ip"}


def _page_tokens(title: str, url: str) -> set[str]:
    slug = url.rstrip("/").split("/")[-1]
    toks = {t for t in _TOKEN_RE.findall(title.lower()) if len(t) > 1}
    toks |= {t for t in _TOKEN_RE.findall(slug.lower()) if len(t) > 1}
    return toks


def _build_index() -> list[dict]:
    """[{page_id, url, title, tokens, distinctive_items}] from the allowlist."""
    entries = get_config().get("sources", {}).get("allowlist", [])
    index = []
    for entry in entries:
        url = str(entry.get("url", ""))
        title = str(entry.get("scheme_or_amc", ""))
        index.append({
            "page_id": str(entry.get("page_id", "")),
            "url": url,
            "title": title,
            "tokens": _page_tokens(title, url),
        })
    token_pages: dict[str, set[str]] = {}
    for page in index:
        for token in page["tokens"]:
            token_pages.setdefault(token, set()).add(page["page_id"])
    for page in index:
        page["distinctive"] = {
            t for t in page["tokens"] if len(token_pages.get(t, set())) == 1
        }
    return index


def title_for_url(url: str) -> str | None:
    for page in _build_index():
        if page["url"] == url:
            return page["title"]
    return None


def resolve_scheme(text: str, prior: str | None = None) -> str | None:
    """Return the resolved page_id, or None when ambiguous.

    An explicitly named scheme always wins (distinctive-token scoring; a tie
    across schemes is ambiguous). The carried `prior` page applies ONLY when
    the message uses follow-up phrasing ("its benchmark?", "what about the
    exit load?", "…and the same for Large Cap?") — history alone never makes
    a scheme target, so a bare fact fragment after an unrelated turn asks for
    clarification instead of guessing.
    """
    tokens = {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 1}
    index = _build_index()
    scores: dict[str, int] = {}
    for page in index:
        hits = sum(1 for t in page["distinctive"] if t in tokens)
        if hits:
            scores[page["page_id"]] = hits
    if not scores:
        known = {p["page_id"] for p in index}
        if prior in known and is_followup(text):
            return prior
        return None
    best = max(scores.values())
    winners = [pid for pid, sc in scores.items() if sc == best]
    if len(winners) == 1:
        return winners[0]
    return None  # tie across schemes -> ambiguous


def is_followup(text: str) -> bool:
    """True when the message phrases itself as a continuation of the previous
    exchange (pronoun / "what about" / "and …" / "same … for …")."""
    return bool(_FOLLOWUP_RE.search(text or ""))


def supported_names_for_prompt() -> str:
    titles = sorted(p["title"] for p in _build_index())
    return ", ".join(titles)


def has_unsupported_amc(text: str) -> str | None:
    """Return the matched unsupported-name fragment, if any."""
    match = _UNSUPPORTED_AMC_RE.search(text or "")
    return match.group(0) if match else None


def wants_fact_concept(text: str) -> bool:
    return bool(_FACT_CONCEPT_RE.search(text or ""))


def wants_scheme_bound_fact(text: str) -> bool:
    """Scheme-bound attribute without a named scheme is ambiguous — "which
    fund's NAV / exit lock-in / expense ratio?" — and must be clarified."""
    return bool(_SCHEME_BOUND_RE.search(text or ""))


def is_ambiguously_scoped(text: str, scheme: str | None, prior: str | None) -> bool:
    """No scheme named, none carried over, but the question asks for a
    scheme-bound fact -> ask which scheme instead of guessing."""
    return scheme is None and prior is None and wants_scheme_bound_fact(text)


def is_regular_plan(text: str) -> bool:
    return bool(_REGULAR_PLAN_RE.search(text or ""))


def is_freshness_query(text: str) -> bool:
    return bool(_FRESHNESS_RE.search(text or ""))


_NAV_PATTERNS = [
    # "NAV: 30 Oct '26\n₹2,187.57" (header widget)
    re.compile(
        r"NAV\s*:?\s*(\d{1,2}\s+[A-Za-z]{3}[a-z]*\s*['’]?\d{2,4})\s*₹\s*([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    # "the Latest NAV as of 30 Oct 2026 is ₹215.99." (About sentence)
    re.compile(
        r"NAV as of (\d{1,2}\s+[A-Za-z]{3}[a-z]*\s+\d{2,4}) is ₹([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
]


def extract_nav(text: str) -> tuple[str, str] | None:
    """(nav_value, nav_date) from a saved excerpt; None when absent."""
    for pattern in _NAV_PATTERNS:
        match = pattern.search(text or "")
        if match:
            date, value = match.group(1), match.group(2)
            # normalize "30 Oct '26" -> "30 Oct 2026"
            normalized = re.sub(r"['’](\d{2})\b", r" \g<1>", date)
            normalized = re.sub(
                r"\b(\d{1,2}\s+[A-Za-z]{3}[a-z]*)\s+(\d{2})\b",
                r"\1 20\2",
                normalized,
            )
            return value, normalized.strip()
    return None
