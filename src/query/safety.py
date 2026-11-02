"""Refuse PII, advice, and performance questions before retrieve.

Order matters: a message containing personal data is refused as `refuse_pii`
even if it also asks a fact (doc test #11), then advice, then performance.
All rules are regex-based so the gate stays explainable in the demo.
"""

from __future__ import annotations

import re

from src.models import AnswerMode

_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("aadhaar", re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b|\b\d{12}\b")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone", re.compile(r"\b(?:\+?91[-\s]?)?[6-9]\d{9}\b")),
    ("account_number", re.compile(r"\b(?:\d[-\s]?){9,17}\d\b")),
    ("otp", re.compile(r"\b\w*otp\b", re.IGNORECASE)),
    ("keyword", re.compile(
        r"\baccount (?:number|no)|\ba/?c\b|\bdebit card\b|\bcredit card\b|\bcvv\b|\bifsc\b",
        re.IGNORECASE,
    )),
]

_ADVICE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bshould i\b", re.IGNORECASE),
    re.compile(r"\brecommend(?:ation)?s?\b", re.IGNORECASE),
    re.compile(r"\bsuggest(?:s|ed)?\b", re.IGNORECASE),
    re.compile(r"\bbest\b[^\n?]{0,40}\b(?:for me|for my|my)\b", re.IGNORECASE),
    re.compile(r"\b(?:which|what)\b[^\n?]{0,40}\bbest\b", re.IGNORECASE),
    re.compile(r"\bbest (?:fund|scheme|option|plan)\b", re.IGNORECASE),
    re.compile(r"\b(?:good|better|worth)\s+to\s+(?:invest|buy|sell|switch)\b", re.IGNORECASE),
    re.compile(r"\bworth (?:investing|buying|switching)\b", re.IGNORECASE),
    re.compile(r"\b(?:ideal|suitable(?:\s+for me)?)\b", re.IGNORECASE),
]

_PERFORMANCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bcagr\b|\bxirr\b|outperform|performance", re.IGNORECASE),
    re.compile(r"\breturns?\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*(?:year|yr|month)s?\b[^\n?]{0,30}\b(?:nav|price|value|grown)\b", re.IGNORECASE),
    re.compile(r"\bvs\b[^\n?]{0,30}\b(?:nifty|sensex|benchmark|index)\b", re.IGNORECASE),
]


def _match(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(text))


def inspect(user_text: str) -> AnswerMode:
    """Gate verdict for a user message: PII > advice > performance > fact."""
    user_text = user_text or ""
    for _name, pattern in _PII_PATTERNS:
        if _match(pattern, user_text):
            return "refuse_pii"
    for pattern in _ADVICE_PATTERNS:
        if _match(pattern, user_text):
            return "refuse_advice"
    for pattern in _PERFORMANCE_PATTERNS:
        if _match(pattern, user_text):
            return "refuse_performance"
    return "fact"
