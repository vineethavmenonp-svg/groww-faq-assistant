"""Entry point: safety gates, intent gates, then evidence-only generation.

Order for every question (general rules, not per-question routes):
1. safety: privacy (values or requests), advice incl. override attempts,
   performance incl. future-return prediction — all refused before retrieval.
2. intent: unsupported AMC/scheme -> scope refusal; scheme resolution from
   page titles + URL-slug aliases in the allowlist config; no scheme named
   in a fresh chat -> clarify; Regular-plan question -> plan limitation;
   today's/latest NAV -> freshness template with the saved NAV date.
3. retrieval restricted to the resolved scheme's page only (evidence for the
   correct scheme; other pages can neither be cited nor confuse the model).
4. generation: the model marks its supporting excerpt(s) (`SOURCES: n`); the
   citation URL comes only from cited evidence — never "first retrieved".
   Unattributable facts degrade to an explicit limitation, no figures.
5. Refusals/clarifications never fabricate citations.

CLI: python -m src.query.pipeline --q "Expense ratio of HDFC Large Cap Fund Direct Growth?"
"""

from __future__ import annotations

import argparse
import json
import sys

from src.config_loader import get_config
from src.models import Answer, AnswerMode, new_request_id
from src.query import generate as generate_mod
from src.query import intent
from src.query import retrieve as retrieve_mod
from src.query import safety
from src.query.prompts import (
    ASK_TOPIC,
    CLARIFY,
    PLAN_LIMITATION,
    REFUSE_ADVICE,
    REFUSE_PII,
    REFUSE_PERFORMANCE,
    REFUSE_SCOPE,
)


def _educational_link() -> str:
    return str(get_config().get("safety", {}).get("educational_link", ""))


def _refusal(
    mode: AnswerMode, text: str, request_id: str, source_url: str | None, question: str
) -> Answer:
    return generate_mod.generate_refusal(
        mode, text, request_id, source_url=source_url, question=question
    )


def _template_answer(
    mode: AnswerMode, text: str, request_id: str, question: str,
    source_url: str | None = None, last_updated: str | None = None,
    nav_date: str | None = None,
    note: str = "none (template answer)",
) -> Answer:
    """Deterministic answers (clarify/scope/plan/freshness): written to the
    generate trace with the question, exactly like other non-PII paths —
    none of these messages contain sensitive values (privacy check ran first)."""
    from src.config_loader import resolve_path

    path = resolve_path(str(
        get_config().get("artifacts", {}).get("generate_dir", "artifacts/generate")
    )) / f"{request_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "request_id": request_id, "mode": mode, "model": note,
            "question": question, "hit_ids": [], "cited_hits": [],
            "cited_source_url": source_url, "raw_response": text,
            "final_text": text, "source_url": source_url,
            "created_at": generate_mod.now_utc_iso(),
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return Answer(
        text=text, mode=mode, request_id=request_id,
        source_url=source_url, last_updated=last_updated, nav_date=nav_date,
    )


def _freshness_answer(
    user_text: str, scheme_page: str, request_id: str, hits: list
) -> Answer:
    """Today's/latest NAV: the saved dataset can only show what its own page
    states, with the NAV date it carries — never as 'today's' value."""
    title = _title_of_page(scheme_page)
    for hit in hits:
        nav = intent.extract_nav(hit.text)
        if nav:
            value, nav_date = nav
            collected = (hit.fetched_at or "")[:10]
            text = (
                f"The saved page for {title or scheme_page} shows a latest NAV "
                f"of ₹{value} as of {nav_date}. That page was collected on "
                f"{collected}, so I cannot verify today's NAV."
            )
            return _template_answer(
                "fact", text, request_id, user_text,
                source_url=hit.source_url, last_updated=collected,
                nav_date=nav_date,
                note="none (saved-page freshness template)",
            )
    return _template_answer(
        "unknown",
        "The saved page does not carry an NAV value with its date, so I "
        "cannot answer today's NAV question from documented sources.",
        request_id, user_text,
    )


def run(
    user_text: str,
    prior_scheme: str | None = None,
    pending_question: str | None = None,
    last_question: str | None = None,
) -> Answer:
    """Run one turn.

    Conversation carry (single-use, caller-supplied from session state):
    - `pending_question`: the unresolved ambiguous question from the last
      `clarify` turn — a reply naming a scheme completes that question.
    - `last_question` + `prior_scheme`: the previous resolved fact exchange —
      follow-up phrased replies ("its benchmark?", "the same for Large Cap?")
      reuse the carried fact phrasing; the carrier scheme applies only with
      follow-up signals, so unrelated questions never inherit it.
    Context never substitutes for evidence: every resolved question runs a
    fresh page-restricted retrieval; history supplies wording only.
    """
    request_id = new_request_id()
    educational_link = _educational_link()
    mode: AnswerMode = safety.inspect(user_text)
    answer: Answer

    if mode == "refuse_pii":
        # Nothing about this request is written to artifacts, Chroma, or logs.
        answer = generate_mod.generate_refusal("refuse_pii", REFUSE_PII, request_id)
    elif mode == "refuse_advice":
        # Pointer to the fund-house overview (real saved page); not a citation
        # of this refusal's content — see DOC conflict note.
        answer = _refusal(
            mode, REFUSE_ADVICE, request_id,
            source_url=educational_link or None, question=user_text,
        )
    elif mode == "refuse_performance":
        answer = _refusal(
            mode, REFUSE_PERFORMANCE, request_id,
            source_url=educational_link or None, question=user_text,
        )
    else:
        answer = _answering_flow(
            user_text, request_id, prior_scheme,
            pending_question, last_question, educational_link,
        )
    if answer.effective_question is None:
        answer.effective_question = user_text
    return attach_trace_paths(answer)


def _answering_flow(
    user_text: str, request_id: str, prior_scheme: str | None,
    pending_question: str | None, last_question: str | None,
    educational_link: str,
) -> Answer:
    scheme = intent.resolve_scheme(user_text, prior_scheme)
    own_fact = intent.wants_fact_concept(user_text)

    # Unsupported house/scheme: explain scope, no scheme citation attached.
    unsupported = intent.has_unsupported_amc(user_text)
    if unsupported:
        other = ".".join(w.capitalize() for w in unsupported.split())
        return _template_answer(
            "refuse_scope",
            REFUSE_SCOPE.format(other=unsupported),
            request_id, user_text, note=f"none (scope gate: {unsupported})",
        )

    # --- context resolution (general cases, not question-specific routes) ---
    # Case A — reply to a clarify: the pending fact question completes with
    # the scheme named in this reply.
    pending_applies = bool(
        pending_question and scheme is not None and not own_fact
    )
    # Case B — follow-up that continues the previous fact thread: a scheme
    # swap ("the same for Large Cap?") repeats the carried fact phrasing; a
    # follow-up naming its own fact ("What about its benchmark?") keeps its
    # own wording but anchors the pronoun to the carried scheme. Requires
    # follow-up phrasing + a previous resolved fact turn — never applied to
    # bare or unrelated turns.
    carry_applies = bool(
        last_question and prior_scheme and scheme is not None
        and pending_question is None
        and intent.is_followup(user_text)
    )
    if pending_applies:
        effective = f"{pending_question} {user_text}".strip()
    elif carry_applies and own_fact:
        effective = (
            f"{user_text} ({_title_of_page(scheme) or prior_scheme})"
        )
    else:
        effective = f"{last_question} {user_text}".strip() if carry_applies else user_text

    # A bare scheme mention with nothing carried and no fact requested gets a
    # topic prompt (name a fact) instead of an unscoped retrieval.
    if scheme is not None and not own_fact and not pending_applies and not carry_applies:
        return _template_answer(
            "clarify",
            ASK_TOPIC.format(scheme=_title_of_page(scheme) or scheme),
            request_id, user_text, note="none (ask-topic gate)",
        )

    # Ambiguity: a fresh chat never gets a guessed scheme — ask which one.
    if intent.is_ambiguously_scoped(user_text, scheme, prior_scheme):
        return _template_answer(
            "clarify",
            CLARIFY.format(schemes=intent.supported_names_for_prompt()),
            request_id, user_text, note="none (ambiguity gate)",
        )
    if scheme is None:
        # A scheme-bound attribute without a scheme ("What is the lock-in?")
        # is ambiguous all by itself — featured fact of WHICH fund? Clarify,
        # whether the chat is fresh (nothing carried) or mid-thread (a bare
        # fragment must never silently inherit the carried scheme).
        if intent.wants_scheme_bound_fact(user_text):
            return _template_answer(
                "clarify",
                CLARIFY.format(schemes=intent.supported_names_for_prompt()),
                request_id, user_text, note="none (scheme-bound, unnamed)",
            )
        # General (non scheme-bound) question — statement downloads, "best
        # pizza on Mars": run the evidence-only fact path corpus-wide; the
        # score gate / NOT_IN_SOURCES degrade it to the unlinked unknown.
        return _run_fact(user_text, request_id, restrict_page=None)

    question_for_gates = effective  # plan/freshness signals may live in the
    # carried phrasing (e.g. a clarify reply to "What is the NAV today …?")

    # Plan identity: saved pages document Direct plans only.
    if intent.is_regular_plan(question_for_gates):
        answer = _template_answer(
            "plan_limitation",
            PLAN_LIMITATION.format(scheme=_title_of_page(scheme) or scheme),
            request_id, user_text, note=f"none (plan gate: {scheme})",
        )
        answer.effective_question = effective
        return answer

    # Freshness: today's/latest NAV answered from the saved page, with its
    # own NAV date kept distinct from the collection date.
    if intent.is_freshness_query(question_for_gates):
        try:
            hits = retrieve_mod.retrieve(effective, request_id, restrict_page=scheme)
        except RuntimeError as exc:
            return generate_mod.generate_refusal(
                "unknown", str(exc), request_id, question=user_text
            )
        answer = _freshness_answer(user_text, scheme, request_id, hits)
        answer.effective_question = effective
        return answer

    answer = _run_fact(effective, request_id, restrict_page=scheme)
    answer.effective_question = effective
    return answer


def _title_of_page(page_id: str) -> str | None:
    for entry in get_config().get("sources", {}).get("allowlist", []):
        if str(entry.get("page_id", "")) == page_id:
            return str(entry.get("scheme_or_amc", "")) or None
    return None


def _run_fact(user_text: str, request_id: str, restrict_page: str | None) -> Answer:
    try:
        hits = retrieve_mod.retrieve(user_text, request_id, restrict_page=restrict_page)
    except RuntimeError as exc:
        # Fail closed: missing/empty index is operational -> no LLM answer.
        return generate_mod.generate_refusal("unknown", str(exc), request_id, question=user_text)

    retrieval_cfg = get_config().get("retrieval", {})
    min_score = float(retrieval_cfg.get("min_score", 0.25))
    if hits[0].score < min_score:
        # Missing-information fallback: no invented numbers, and NO citation
        # attached (nothing supports a claim; never the first retrieved URL).
        return _unknown_from_score_gate(user_text, request_id, hits)

    answer = generate_mod.generate(user_text, hits, request_id)

    # Second chance: a NOT_IN_SOURCES verdict means "not in the excerpts
    # retrieval returned", which can be a retrieval miss for short literal
    # tokens (e.g. lock-in phrasing inside a furniture-heavy chunk). Retry
    # once with a lexical pass over the SAME restricted page — not a new
    # source, not a route specific to any question.
    if answer.needs_lexical_retry and restrict_page:
        retry_id = new_request_id()
        retry = None
        try:
            retry_hits = retrieve_mod.lexical_retrieve(user_text, restrict_page, retry_id)
            if retry_hits and [h.chunk_id for h in retry_hits] != [h.chunk_id for h in hits]:
                retry = generate_mod.generate(user_text, retry_hits, retry_id)
        except RuntimeError:
            retry = None
        if retry is not None and retry.mode == "fact":
            return retry
        # The retry produced no grounded fact: keep the first deterministic
        # unknown (never fall back to closer-looking pages or general knowledge).
    return answer


def attach_trace_paths(answer: Answer) -> Answer:
    """Data contract: trace_paths for both stages, whatever exists."""
    from src.config_loader import resolve_path

    for key, default in (("retrieve", "artifacts/retrieve"), ("generate", "artifacts/generate")):
        artifact = get_config().get("artifacts", {}).get(f"{key}_dir", default)
        path = resolve_path(str(artifact)) / f"{answer.request_id}.json"
        if path.exists():
            answer.trace_paths[key] = str(path)
    return answer


def _unknown_from_score_gate(user_text, request_id, hits) -> Answer:
    from src.models import Answer
    from src.query.prompts import UNKNOWN

    answer = Answer(
        text=UNKNOWN.format(hint="").strip(),
        mode="unknown",
        request_id=request_id,
        source_url=None,  # no claim -> no citation, never the first URL
        last_updated=None,
    )
    generate_mod.write_score_gate_trace(
        question=user_text,
        request_id=request_id,
        hits=hits,
        final_text=answer.text,
        source_url=answer.source_url,
    )
    return answer


def format_answer(answer: Answer) -> str:
    """Rendered answer: mode, text, exactly one official link when a fact has
    supporting evidence, last-updated date."""
    lines = [answer.text]
    if answer.source_url:
        lines.append(f"Source: {answer.source_url}")
        if answer.last_updated:
            lines.append(f"Last updated from sources: {answer.last_updated}")
    return f"[{answer.mode}]\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Groww FAQ fact-only query pipeline")
    parser.add_argument(
        "--q",
        default="Expense ratio of HDFC Large Cap Fund Direct Growth?",
        help="Question to ask (default: doc sample question)",
    )
    args = parser.parse_args(argv)
    try:
        answer = run(args.q)
    except RuntimeError as exc:
        # Missing/bad env config (LLM_MODEL / GROQ_API_KEY) or Groq outage:
        # config error, no ungrounded local fallback.
        print(f"[config error] {exc}", file=sys.stderr)
        return 2
    print(format_answer(answer))
    for kind, path in answer.trace_paths.items():
        print(f"trace ({kind}): {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
