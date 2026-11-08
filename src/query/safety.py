"""Refuse PII, advice, performance, and prediction questions before retrieve.

Gate families, in inspection order:
1. PII *values* present in the message (PAN-shaped identifiers, Aadhaar,
   email, phone, account numbers, OTPs) — refused as `refuse_pii`.
2. *Requests for* sensitive personal information, even without a value
   present (e.g. "What is the fund manager's PAN?"), including requests to
   find/share another person's details — refused as `refuse_pii`. This
   assistant only answers documented public scheme facts, so such requests
   get a privacy refusal instead of the missing-source fallback.
3. Advice (buy/sell/recommendation) and instruction-override attempts
   ("ignore previous instructions…") — refused as `refuse_advice`.
4. Performance comparisons and future-return prediction ("predicted
   returns", "in 5 years will it grow to…") — refused as
   `refuse_performance` with the explicit no-prediction template.

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

_SEPARATORS = r"[\s\-_.]*(?:number|no\.?|n0|id|card|detail(?:s)?|info(?:rmation)?)?s?"

# Requests *for* sensitive personal information (no value present yet).
# Conservative by design: this assistant never handles personal data, so any
# question asking for such a field — about anyone, including fund managers —
# is treated as a privacy request, not a fact lookup.
_PII_REQUEST_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pan_request", re.compile(r"\bpans?\b", re.IGNORECASE)),
    ("aadhaar_request", re.compile(r"\baadha(ar|ri)\b", re.IGNORECASE)),
    ("contact_request", re.compile(
        r"\b(?:e-?mail|phone|mobile|whatsapp|telegram)\b" + _SEPARATORS,
        re.IGNORECASE,
    )),
    ("dob_request", re.compile(r"\b(?:date of birth|\bdob\b)\b", re.IGNORECASE)),
    ("financial_request", re.compile(
        r"\b(?:salary|income|net worth|bank (?:statement|balance))\b",
        re.IGNORECASE,
    )),
    ("credential_request", re.compile(r"\b(?:password|passcode|pins?)\b", re.IGNORECASE)),
]

_ADVICE_PATTERNS: list[re.Pattern[str]] = [
    # Instruction-override attempts are treated as advice-refusal bait: they
    # try to make the app abandon its facts-only rules.
    re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:your\s+|the\s+|my\s+)?(?:previous|prior|earlier|preceding|above)\s+(?:instructions?|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(?:all\s+|your\s+|the\s+)?(?:previous|prior|earlier|above)\s+(?:instructions?|prompts?|rules?|guidelines?)", re.IGNORECASE),
    re.compile(r"\byou are no longer\b|\bforget (?:your|all) (?:rules|instructions)\b|\bdan mode\b", re.IGNORECASE),
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

# Prediction/forward-looking questions: this assistant only documents what the
# saved pages state, so it never predicts or calculates future returns.
_PREDICTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:future|expected|projected|estimated|forecast(?:ed)?)\s+(?:return|r?earnings?|growth|price|value|nav|gains?)\b", re.IGNORECASE),
    re.compile(r"\b(?:predict|forecast|projection)\b", re.IGNORECASE),
    re.compile(r"\bin\s+(?:the\s+)?next\s+\d+\s*(?:year|yr|month)s?\b", re.IGNORECASE),
    re.compile(r"\bwill\b[^\n?]{0,40}\b(?:become|be worth|give|yield|earn|grow|double|reach)\b", re.IGNORECASE),
    re.compile(r"\bhow much\b[^\n?]{0,30}\b(?:earn|gain|make)\b", re.IGNORECASE),
    re.compile(r"\b(?:double|tripple|triple)\b", re.IGNORECASE),
]

_PERFORMANCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bcagr\b|\bxirr\b|outperform|performance", re.IGNORECASE),
    re.compile(r"\breturns?\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*(?:year|yr|month)s?\b[^\n?]{0,30}\b(?:nav|price|value|grown)\b", re.IGNORECASE),
    re.compile(r"\bvs\b[^\n?]{0,30}\b(?:nifty|sensex|benchmark|index)\b", re.IGNORECASE),
]


def _match(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(text))


# Value-bearing detectors reused for masking: sensitive values must never be
# persisted in history or echoed back. Keyword-only detectors (otp, "account
# number" words) are deliberately excluded so the echoed question stays
# readable while any actual value is masked.
_MASKING_KEYS = ("pan", "aadhaar", "email", "phone", "account_number")
_MASKING_PATTERNS = [p for name, p in _PII_PATTERNS if name in _MASKING_KEYS]


def mask_values(user_text: str) -> str:
    """Replace sensitive values with '••••' — used before a refused turn is
    written to conversation history or shown back to the user."""
    masked = user_text or ""
    for pattern in _MASKING_PATTERNS:
        masked = pattern.sub("••••", masked)
    return masked


def inspect(user_text: str) -> AnswerMode:
    """Gate verdict for a user message:
    PII values > PII requests > advice (incl. overrides) > performance
    (incl. future-return prediction) > fact."""
    user_text = user_text or ""
    for _name, pattern in _PII_PATTERNS:
        if _match(pattern, user_text):
            return "refuse_pii"
    for _name, pattern in _PII_REQUEST_PATTERNS:
        if _match(pattern, user_text):
            return "refuse_pii"
    for pattern in _ADVICE_PATTERNS:
        if _match(pattern, user_text):
            return "refuse_advice"
    for pattern in _PREDICTION_PATTERNS:
        if _match(pattern, user_text):
            return "refuse_performance"
    for pattern in _PERFORMANCE_PATTERNS:
        if _match(pattern, user_text):
            return "refuse_performance"
    return "fact"
