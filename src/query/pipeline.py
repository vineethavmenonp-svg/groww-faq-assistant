"""Entry point: safety first, then retrieve, then generate.

user text -> safety gate -> (fact?) embed query -> Chroma top_k
  -> max score < min_score: unknown (no LLM, no invented numbers)
  -> else Groq (model from .env), grounded on chunks
Answer: <=3 sentences + one allowlisted URL + "Last updated from sources:".
Citation URL and last-updated always come from the retrieved chunk (never from
model output). Refusals never hit retrieval; PII never touches artifacts.

CLI: python -m src.query.pipeline --q "Expense ratio of HDFC Large Cap Fund Direct Growth?"
"""

from __future__ import annotations

import argparse
import sys

from src.config_loader import get_config
from src.models import Answer, AnswerMode, new_request_id
from src.query import generate as generate_mod
from src.query import retrieve as retrieve_mod
from src.query import safety
from src.query.prompts import REFUSE_ADVICE, REFUSE_PERFORMANCE, REFUSE_PII


def _safety_links() -> tuple[str, str]:
    links = get_config().get("safety", {})
    return str(links.get("educational_link", "")), str(links.get("factsheet_fallback_url", ""))


def _refusal(
    mode: AnswerMode, text: str, request_id: str, source_url: str | None, question: str
) -> Answer:
    return generate_mod.generate_refusal(
        mode, text, request_id, source_url=source_url, question=question
    )


def run(user_text: str) -> Answer:
    request_id = new_request_id()
    mode: AnswerMode = safety.inspect(user_text)
    educational_link, factsheet_link = _safety_links()
    answer: Answer

    if mode == "refuse_pii":
        # Nothing about this request is written to artifacts, Chroma, or logs.
        answer = generate_mod.generate_refusal("refuse_pii", REFUSE_PII, request_id)
    elif mode == "refuse_advice":
        answer = _refusal(
            mode,
            REFUSE_ADVICE.format(educational_link=educational_link),
            request_id,
            source_url=educational_link or None,
            question=user_text,
        )
    elif mode == "refuse_performance":
        answer = _refusal(
            mode,
            REFUSE_PERFORMANCE.format(factsheet_fallback_url=factsheet_link),
            request_id,
            source_url=factsheet_link or None,
            question=user_text,
        )
    else:
        answer = _run_fact(user_text, request_id)
    return attach_trace_paths(answer)


def _run_fact(user_text: str, request_id: str) -> Answer:
    try:
        hits = retrieve_mod.retrieve(user_text, request_id)
    except RuntimeError as exc:
        # Fail closed: missing/empty index is operational -> no LLM answer.
        return generate_mod.generate_refusal("unknown", str(exc), request_id, question=user_text)

    retrieval_cfg = get_config().get("retrieval", {})
    min_score = float(retrieval_cfg.get("min_score", 0.25))
    if hits[0].score < min_score:
        # No invented numbers; the closest official page is attached as the
        # single citation link (the card renders it as `Source:`).
        return _unknown_from_score_gate(user_text, request_id, hits)

    return generate_mod.generate(user_text, hits, request_id)


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
        source_url=hits[0].source_url if hits else None,
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
    """Rendered answer: mode, text, exactly one official link, last-updated."""
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
