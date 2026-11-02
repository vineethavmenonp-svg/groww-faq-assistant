"""Tiny UI (architecture.md §9): welcome, 3 examples, facts-only disclaimer,
chat input, answer card, and an in-product demo panel.

- Facts-only. No investment advice. (copy from config)
- Chat stays in st.session_state (in-memory); the UI writes no files, has no
  login, and never calls provider SDKs — everything goes through
  src.query.pipeline.run.
- If the vector index is missing, the pipeline returns the fail-closed
  "Run: python -m src.ingest" message; LLM config errors surface via st.error.

Run: streamlit run src/ui/app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src.config_loader import get_config
from src.models import Answer
from src.query import pipeline

MODE_BADGES = {
    "fact": "✅ Fact (documented source below)",
    "unknown": "🤷 Not in loaded sources",
    "refuse_advice": "🚫 No investment advice",
    "refuse_performance": "🚫 No return computations",
    "refuse_pii": "🚫 Personal data refused",
}


def _read_trace(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def render_answer_card(answer: Answer) -> None:
    with st.chat_message("assistant"):
        st.markdown(f"**{MODE_BADGES.get(answer.mode, answer.mode)}** — `{answer.mode}`")
        st.write(answer.text)
        if "Run: python -m src.ingest" in answer.text:
            st.warning("The vector index is missing or empty. Run `python -m src.ingest` first.")
        if answer.source_url:
            st.markdown(f"📄 [Official source]({answer.source_url})")
        if answer.last_updated:
            st.caption(f"Last updated from sources: {answer.last_updated}")


def render_demo_panel(answer: Answer | None) -> None:
    st.sidebar.header("Demo panel")
    st.sidebar.caption("Artifacts for the last request (files under artifacts/).")
    if answer is None:
        st.sidebar.info("Ask a question to populate the panel.")
        return

    st.sidebar.markdown(f"**mode:** `{answer.mode}` · request `{answer.request_id[:8]}…`")

    retrieve_trace = _read_trace(answer.trace_paths.get("retrieve", ""))
    if retrieve_trace:
        st.sidebar.subheader("Retrieval")
        for hit in retrieve_trace.get("hits", []):
            st.sidebar.markdown(
                f"`{hit['score']:.3f}` · `{hit['section']}` · "
                f"[{hit['chunk_id'].split('::')[0]}]({hit['source_url']})"
            )
        st.sidebar.caption(
            f"embed model: {retrieve_trace.get('model', 'n/a')} · "
            f"candidates: {retrieve_trace.get('candidates', 'n/a')}"
        )
    else:
        st.sidebar.caption("No retrieve trace (refusals skip retrieval; PII skips all traces).")

    generate_trace = _read_trace(answer.trace_paths.get("generate", ""))
    st.sidebar.subheader("Generation")
    if generate_trace:
        st.sidebar.markdown(f"**model:** {generate_trace.get('model', 'n/a')}")
        st.sidebar.markdown(f"**citation:** {generate_trace.get('source_url') or 'none'}")
        st.sidebar.caption(f"best hit: {generate_trace.get('best_hit') or 'n/a'}")
    else:
        st.sidebar.caption("No generate trace (PII requests write nothing).")

    st.sidebar.subheader("Artifacts")
    if answer.trace_paths:
        for kind, path in answer.trace_paths.items():
            st.sidebar.code(path, language=None)
    else:
        st.sidebar.caption("None for this request.")


def main() -> None:
    cfg = get_config()["ui"]
    st.set_page_config(page_title="Groww Mutual Fund FAQ Assistant", page_icon="💬")
    st.title("Groww Mutual Fund FAQ Assistant")
    st.write(cfg["welcome"])
    st.caption(cfg["disclaimer"])

    st.session_state.setdefault("answers", [])  # [(Answer, question)] — in-memory only
    last = st.session_state["answers"][-1][0] if st.session_state["answers"] else None
    render_demo_panel(last)

    st.subheader("Example questions")
    cols = st.columns(3)
    example_clicked = None
    for i, example in enumerate(cfg["examples"]):
        if cols[i % 3].button(example, key=f"example_{i}"):
            example_clicked = example

    if question := st.chat_input("Ask a factual scheme question…", key="chat_input"):
        example_clicked = question

    if not example_clicked:
        for answer, prev_question in reversed(st.session_state["answers"][-3:]):
            with st.chat_message("user"):
                st.write(prev_question)
            render_answer_card(answer)
        return  # streamlit reruns; nothing new to process

    with st.chat_message("user"):
        st.write(example_clicked)
    try:
        with st.spinner("Checking safety, retrieving from official sources…"):
            answer = pipeline.run(example_clicked)
    except RuntimeError as exc:
        # Config errors (missing LLM_MODEL/GROQ_API_KEY) or Groq outage surface here.
        st.error(f"Config error: {exc}")
        st.caption("No ungrounded fallback is available by design. Check .env and config.yaml.")
        return
    st.session_state["answers"].append((answer, example_clicked))
    render_answer_card(answer)
    render_demo_panel(answer)


if __name__ == "__main__":
    main()
