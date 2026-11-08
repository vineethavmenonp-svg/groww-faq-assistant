"""Chat-first UI: compact header (with New chat), welcome question +
suggested inquiries before the first message, chronological conversation
(oldest at top, newest at bottom, question directly before its answer),
discreet labeled source attribution under factual answers, facts-only footer.

Styling follows a Groww-inspired community palette (accent mint #00B386,
pale mint #E8F7F1, canvas #F7FAF9, text #202B33) — not claimed as official
brand specifications. The header mark is a custom typographic "G"; an
"Independent learning project" label rides in the pinned footer. No official
logos or certification claims are used.

Conversation handling:
- Full history is kept in st.session_state ("answers", in-memory) and rendered
  chronologically every rerun — nothing disappears mid-session, and the
  (small) model context window never truncates what the user sees.
- "New chat" in the header clears the current conversation and restores the
  welcome screen and the three example questions.
- After a new exchange the view scrolls to the newest message (once, on new
  turns only); older messages stay readable by scrolling up.
- History is per browser session (st.session_state): users never see each
  other's conversation.
- Refused privacy turns are stored with sensitive values masked
  (src.query.safety.mask_values) — sensitive inputs are never persisted or
  echoed in clear text.

The UI writes no files, has no login, and never calls provider SDKs —
everything goes through src.query.pipeline.run (guardrails, retrieval,
citation behaviour unchanged). If the vector index is missing, the pipeline
returns the fail-closed "Run: python -m src.ingest" message.

Run: streamlit run src/ui/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from streamlit.components.v1 import html as _components_html

# Self-locate the repository root and add it to sys.path so the app's
# absolute `src.*` imports resolve on any runner. Streamlit executes this
# file with `src/ui` (not the repo root) on sys.path — the standard Python
# `sys.path[0]`-is-script-dir behavior — which causes
# ModuleNotFoundError: No module named 'src' on hosted runners. Inserting
# the root here makes the entry point independent of the working
# directory and runner environment.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config_loader import get_config
from src.models import Answer
from src.query import pipeline
from src.query import safety

_CSS = """
<style>
  /* --- Palette: Groww-inspired community styling (not official brand specs) ---
     accent mint #00B386 · pale mint #E8F7F1 · canvas #F7FAF9 · text #202B33
     Contrast: dark text (#202B33) on mint = ~5.2:1 (AA); white on mint = ~2.8:1,
     so the send arrow and any text on mint use the dark text colour, and link
     text uses darkened mint #007457 (~5.8:1 on white). */
  [data-testid="stAppViewContainer"] { background: #F7FAF9 !important; }
  #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
  [data-testid="stStatusWidget"], [data-testid="stSidebarCollapsedControl"],
  /* stAppHeader is a fixed transparent bar that otherwise overlays
     (and visually clips) the card title at the new tighter top padding */
  header[data-testid="stHeader"], [data-testid="stAppHeader"] {
      display: none !important;
  }
  .block-container {
      max-width: 860px !important;
      padding-top: 0.75rem !important;
      /* keeps the bottom message clear of the pinned composer (input +
         disclaimer below it), incl. mobile */
      padding-bottom: 8.5rem !important;
  }
  @media (max-width: 640px) {
      .block-container { padding-bottom: 9.5rem !important; }
  }

  /* Shared header: sticky one-row bar (G mark · title · New chat right).
     Streamlit's scroll happens in section.stMain. The bar is wrapped in a
     stLayoutWrapper flex item whose height equals the bar — sticky must be
     on THAT wrapper, whose containing block (stVerticalBlock) spans the
     whole conversation, so it has room to stick while messages scroll
     beneath; the bar occupies layout space, so nothing is hidden at rest. */
  div[data-testid="stLayoutWrapper"]:has(.mfq-header) {
      position: sticky;
      top: 0;
      z-index: 60;
  }
  div[data-testid="stHorizontalBlock"]:has(.mfq-header) {
      background: #FFFFFF;
      padding: 0.6rem 0.9rem 0.55rem;
      margin-bottom: 0.85rem !important;
      border-bottom: 1px solid #DFE9E4;
      box-shadow: 0 2px 10px rgba(32, 43, 51, 0.05);
      align-items: center;
      flex-wrap: nowrap;
  }
  div[data-testid="stHorizontalBlock"]:has(.mfq-header)
      > div[data-testid="stColumn"] {
      width: auto !important; flex: 1 1 auto;
  }
  div[data-testid="stHorizontalBlock"]:has(.mfq-header)
      > div[data-testid="stColumn"]:last-child {
      flex: 0 0 auto;
  }
  div[data-testid="stHorizontalBlock"]:has(.mfq-header) [data-testid="stElementContainer"] {
      width: 100%;
  }
  .mfq-header {
      display: flex; align-items: center; gap: 0.6rem;
      background: transparent; border: none; box-shadow: none; padding: 0;
  }
  .mfq-logo {
      width: 34px; height: 34px; flex: 0 0 34px;
      display: flex; align-items: center; justify-content: center;
      background: #E8F7F1; border-radius: 0.5rem;
      /* Custom typographic mark: serif "G" in darkened mint. Decorative,
         distinct from — not derived from — any official logo. #007457 keeps
         ~4.9:1 contrast on the pale-mint tile. */
      font-family: Georgia, "Times New Roman", serif;
      font-size: 20px; font-weight: 700; line-height: 1;
      color: #007457;
  }
  .mfq-title { font-size: 1.05rem; font-weight: 700; color: #202B33; line-height: 1.2; }
  @media (max-width: 640px) {
      .mfq-title { font-size: 0.95rem; }
      div[data-testid="stHorizontalBlock"]:has(.mfq-header) { padding: 0.5rem 0.6rem 0.45rem; gap: 0.4rem; }
  }

  /* New chat: quiet outline control, mint on hover. Targeted by its
     data-testid — it is the only primary-type button in the app, so this
     never touches the welcome-card buttons. */
  button[data-testid="stBaseButton-primary"] {
      background: #FFFFFF !important; border: 1px solid #DFE9E4 !important;
      color: #202B33 !important; font-weight: 600; font-size: 0.82rem;
      text-align: center; justify-content: center !important;
      border-radius: 0.45rem; padding: 0.34rem 0.75rem !important;
      min-height: 0; box-shadow: 0 1px 2px rgba(32, 43, 51, 0.04);
  }
  button[data-testid="stBaseButton-primary"]:hover {
      background: #E8F7F1 !important; border-color: #00B386 !important;
      color: #007457 !important;
  }
  button[data-testid="stBaseButton-primary"]:focus-visible {
      outline: 2px solid #00B386; outline-offset: 1px;
  }

  /* Chat bubbles — compact: tighter card padding, 28px avatars, small gaps. */
  /* Cards keep their inner padding: the whole source row always sits inside
     the content-driven card (heights are content-driven, never fixed). */
  div[data-testid="stChatMessage"] {
      background: #FFFFFF; border: 1px solid #DFE9E4; border-radius: 0.5rem;
      box-shadow: 0 1px 3px rgba(32, 43, 51, 0.05);
      padding: 0.55rem 0.8rem 0.6rem;
      margin-bottom: 0.45rem;
  }
  div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
      background: #E8F7F1;
  }
  div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
      border-left: 3px solid #00B386;
  }
  /* Smaller avatars (oversized presets reduced) */
  [data-testid="stChatMessageAvatarUser"],
  [data-testid="stChatMessageAvatarAssistant"] {
      width: 24px !important; height: 24px !important;
      max-width: 24px !important; max-height: 24px !important;
      font-size: 11px !important; line-height: 24px !important;
  }
  [data-testid="stChatMessageAvatarUser"] {
      background: #202B33 !important; color: #FFFFFF !important;
  }
  [data-testid="stChatMessageAvatarAssistant"] {
      background: #00B386 !important; color: #202B33 !important;
  }
  div[data-testid="stChatMessageContent"] p, div[data-testid="stChatMessageContent"] li {
      color: #202B33; font-size: 0.95rem; margin-bottom: 0.25rem;
  }

  /* Discreet labeled source attribution under factual answers: content-driven
     card, comfortable inner padding, link left / date right on desktop,
     stacked neatly on mobile — never touching the card's bottom edge. */
  div[data-testid="stChatMessageContent"] .stMarkdown,
  div[data-testid="stChatMessageContent"] div[data-testid="stMarkdownContainer"] {
      margin-bottom: 0.15rem !important;
  }
  .mfq-src {
      margin-top: 0.45rem;
      padding: 0.5rem 0.1rem 0.35rem;
      border-top: 1px solid #E8F7F1;
      font-size: 0.76rem; color: #5C6B75; line-height: 1.45;
      display: flex; justify-content: space-between; align-items: baseline;
      gap: 0.4rem 1rem; flex-wrap: wrap; font-variant-numeric: tabular-nums;
  }
  .mfq-src a { color: #007457; font-weight: 600; text-decoration: none; overflow-wrap: anywhere; }
  .mfq-src a:hover { text-decoration: underline; }
  .mfq-date { color: #5C6B75; font-variant-numeric: tabular-nums; white-space: nowrap; }
  @media (max-width: 640px) {
      .mfq-src { flex-direction: column; align-items: flex-start; gap: 0.2rem; }
      .mfq-date { margin-left: 0; }
  }

  /* Welcome heading + suggested inquiries (no duplicate icon/title) */
  .mfq-hero { text-align: center; padding: 1rem 0 0.2rem; }
  .mfq-hero h1 { font-size: 1.45rem; font-weight: 700; color: #202B33; margin: 0 0 0.3rem; }
  .mfq-hero p { color: #5C6B75; font-size: 0.95rem; margin: 0; }
  .mfq-kicker {
      text-align: center; font-size: 0.72rem; font-weight: 600;
      letter-spacing: 0.05em; color: #5C6B75; text-transform: uppercase;
      margin: 1.1rem 0 0.6rem;
  }

  /* Suggested-inquiry buttons: equal-width cards, wrapping labels */
  .stButton > button {
      background: #FFFFFF; border: 1px solid #DFE9E4; color: #202B33;
      font-weight: 600; font-size: 0.9rem;
      text-align: left; justify-content: flex-start !important;
      border-radius: 0.25rem; min-height: 40px; padding: 0.5rem 0.9rem;
      box-shadow: 0 1px 3px rgba(32, 43, 51, 0.05);
      width: 100%;
      white-space: normal; word-break: break-word; line-height: 1.35;
  }
  .stButton > button > div, .stButton > button p { width: 100%; text-align: left; }
  .stButton > button:hover {
      background: #E8F7F1; border-color: #00B386; color: #202B33;
  }
  .stButton > button:focus-visible {
      outline: 2px solid #00B386; outline-offset: 1px;
  }

  /* Chat input well: mint focus ring; accent send button with dark arrow
     (white-on-mint fails AA, dark-on-mint passes) */
  [data-testid="stChatInput"] { border-color: #B9CFC6; }
  [data-testid="stChatInput"]:focus-within { border-color: #00B386 !important; }
  [data-testid="stChatInputSubmitButton"] button,
  button[data-testid="stChatInputSubmitButton"] {
      background-color: #00B386 !important; color: #202B33 !important;
  }
  [data-testid="stChatInputSubmitButton"] svg,
  button[data-testid="stChatInputSubmitButton"] svg { color: #202B33; }

  /* Composer: input + disclaimer as one properly sized bottom area. The
     disclaimer sits BELOW the chat input (small muted text, wraps on
     mobile), and the whole composer reserves its own space so it never
     covers questions, answers or source rows. */
  [data-testid="stBottom"] {
      background: #F7FAF9 !important;
      border-top: 1px solid #DFE9E4;
      box-shadow: none !important;
  }
  [data-testid="stBottom"]::before { content: none !important; }
  [data-testid="stBottom"]::after {
      content: "Independent learning project · Facts only · No investment advice · Don’t enter personal details.";
      display: block; text-align: center; color: #5C6B75;
      font-size: 0.72rem; line-height: 1.4;
      padding: 0.35rem 0.75rem 0.25rem;
      white-space: normal; overflow-wrap: break-word;
  }
  [data-testid="stBottomBlockContainer"] { padding-bottom: 0.1rem; }

  /* Welcome screen: non-interactive coverage chips */
  .mfq-chips {
      display: flex; flex-wrap: wrap; gap: 0.4rem;
      justify-content: center; margin: 0.5rem 0 0.2rem;
  }
  .mfq-chip {
      background: #FFFFFF; border: 1px solid #DFE9E4;
      color: #202B33; border-radius: 999px;
      padding: 0.26rem 0.72rem; font-size: 0.78rem; font-weight: 600;
  }
  .mfq-note {
      text-align: center; color: #5C6B75;
      font-size: 0.8rem; margin: 0.55rem 0 0;
  }
</style>
"""

_SCROLL_JS = (
    "<script>(() => {"
    "const p=window.parent;"
    "setTimeout(function(){"
    # Streamlit scrolls inside its own main section, not the window
    "const sc=p.document.querySelector('section.stMain');"
    "if(sc){sc.scrollTo({top:sc.scrollHeight,behavior:'smooth'});}"
    "p.scrollTo({top:p.document.body.scrollHeight,behavior:'smooth'});"
    "},60);})();</script>"
)


def _source_label(url: str) -> str:
    """Human label for a corpus URL (link labels, never raw URLs)."""
    config = get_config()
    for entry in config.get("sources", {}).get("allowlist", []):
        if str(entry.get("url", "")) == url:
            title = str(entry.get("scheme_or_amc", "")) or url
            if str(entry.get("page_id", "")) == "hdfc-mutual-funds":
                return f"{title} — fund house overview"
            return title
    return "Official source"


def _fmt_date(iso: str) -> str:
    """'2026-11-01' -> '1 Nov 2026'; passes through unparseable strings."""
    if not iso:
        return ""
    try:
        from datetime import datetime
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%-d %b %Y")
    except (ValueError, TypeError):
        return iso


def _attribution_html(answer: Answer) -> str:
    """Discreet source row: named page label (left) + collection date (right)
    for grounded facts; a financial value's own effective date (NAV as of …)
    is kept distinct from the page-collection date. Skipped when the copy
    already embeds the link inline, so each answer keeps one URL position.
    Refusals/clarifications without a grounded source render no attribution
    at all (never a fabricated citation)."""
    inline = bool(answer.source_url and answer.source_url in (answer.text or ""))
    parts = []
    collected = _fmt_date(answer.last_updated) if answer.last_updated else ""
    if answer.source_url and not inline:
        label = _source_label(answer.source_url)
        parts.append(
            f'<a href="{answer.source_url}" target="_blank" rel="noopener">{label} ↗</a>'
        )
    if answer.nav_date:
        date_bits = [f'NAV as of {answer.nav_date}']
        if collected:
            date_bits.append(f'Collected {collected}')
        parts.append(f'<span class="mfq-date">{" · ".join(date_bits)}</span>')
    elif collected:
        parts.append(f'<span class="mfq-date">Collected {collected}</span>')
    if not parts:
        return ""
    return f'<div class="mfq-src">{"".join(parts)}</div>'


def _render_history() -> None:
    """All turns, chronologically: oldest first, each question immediately
    before its answer. Nothing is sliced — the whole session stays visible."""
    for stored_question, answer in st.session_state["answers"]:
        with st.chat_message("user", avatar="user"):
            st.write(stored_question)
        with st.chat_message("assistant", avatar="assistant"):
            st.write(answer.text)
            if "Run: python -m src.ingest" in answer.text:
                st.warning("The vector index is missing or empty. Run `python -m src.ingest` first.")
            attribution = _attribution_html(answer)
            if attribution:
                st.markdown(attribution, unsafe_allow_html=True)


def main() -> None:
    cfg = get_config()["ui"]
    st.set_page_config(page_title="Groww FAQ Assistant", page_icon="💬")
    st.markdown(_CSS, unsafe_allow_html=True)

    st.session_state.setdefault("answers", [])  # [(masked question, Answer)]
    st.session_state.setdefault("last_scheme", None)
    st.session_state.setdefault("pending_question", None)  # clarify awaiting scheme
    st.session_state.setdefault("last_question", None)     # last resolved fact question
    st.session_state.setdefault("scroll_pending", False)

    # Header row: title card + New chat (visible in the header on every screen).
    header_col, newchat_col = st.columns([6, 1], gap="small", vertical_alignment="center")
    with header_col:
        st.markdown(
            """
            <div class="mfq-header">
              <div class="mfq-logo">G</div>
              <div class="mfq-title">Groww FAQ Assistant</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with newchat_col:
        # Only primary-type button in the app: the explicit detail-testid lets
        # the compact header styling target exactly this control.
        new_chat = st.button("New chat", key="new_chat", type="primary", width="stretch")
    if new_chat:
        # Clear the current conversation and ALL carried context; the welcome
        # screen and examples return on this same rerun.
        st.session_state["answers"] = []
        st.session_state["last_scheme"] = None
        st.session_state["pending_question"] = None
        st.session_state["last_question"] = None
        st.session_state["scroll_pending"] = False

    has_chat = bool(st.session_state["answers"])

    # Suggested inquiries: shown only before the first message (and again
    # after New chat restores the welcome screen).
    example_clicked = None
    if not has_chat:
        chips = ""
        for name in ("HDFC Large Cap", "HDFC Flexi Cap",
                     "HDFC ELSS Tax Saver", "HDFC Mid Cap"):
            chips += f'<span class="mfq-chip">{name}</span>'
        st.markdown(
            f"""
            <div class="mfq-hero">
              <h1>What would you like to know?</h1>
              <p>Ask about HDFC mutual funds. Get answers with sources.</p>
            </div>
            <div class="mfq-kicker">Covered schemes</div>
            <div class="mfq-chips">{chips}</div>
            <p class="mfq-note">Plus general information about HDFC Mutual Fund.</p>
            <div class="mfq-kicker">Suggested inquiries</div>
            """,
            unsafe_allow_html=True,
        )
        _, col, _ = st.columns([1.4, 5, 1.4])
        with col:
            for i, example in enumerate(cfg["examples"]):
                # width="stretch": equal-width cards; label wraps, full question kept
                if st.button(f"{example}  →", key=f"example_{i}", width="stretch"):
                    example_clicked = example

    # Conversation area: full history, oldest at the top.
    if has_chat:
        _render_history()

    question = st.chat_input(
        "Ask about an HDFC scheme…", key="chat_input",
    ) or example_clicked

    if question:
        try:
            with st.spinner("Checking documented scheme pages..."):
                answer = pipeline.run(
                    question,
                    prior_scheme=st.session_state.get("last_scheme"),
                    pending_question=st.session_state.get("pending_question"),
                    last_question=st.session_state.get("last_question"),
                )
        except RuntimeError as exc:
            # Config errors (missing LLM_MODEL/GROQ_API_KEY) or Groq outage surface here.
            with st.chat_message("assistant", avatar="assistant"):
                st.error("A temporary connection error occurred while querying the index or the model.")
                st.caption(f"Details: {exc} — no ungrounded fallback is available by design.")
            return
        # Privacy turns are stored with values masked: sensitive inputs are
        # never persisted (history) or echoed (bubble).
        stored_question = (
            safety.mask_values(question) if answer.mode == "refuse_pii" else question
        )
        if answer.mode == "clarify":
            # Single-use pending slot: the next reply that names a scheme
            # completes THIS question (clarification replies). The previous
            # fact thread stays intact behind it — the pipeline prefers the
            # pending question whenever a reply resolves it.
            st.session_state["pending_question"] = question
        else:
            # Nothing is carried across unrelated turns: refusals and unknowns
            # end the thread; new context only starts from a resolved fact.
            st.session_state["pending_question"] = None
            if answer.mode == "fact" and answer.source_url:
                # Carry the resolved scheme + the effective question (raw text
                # combined with carried phrasing) so follow-ups like "…and its
                # benchmark?" or "the same for Large Cap?" reuse the thread.
                st.session_state["last_scheme"] = answer.source_url.rstrip("/").split("/")[-1]
                st.session_state["last_question"] = answer.effective_question or question
            else:
                st.session_state["last_scheme"] = None
                st.session_state["last_question"] = None
        st.session_state["answers"].append((stored_question, answer))
        st.session_state["scroll_pending"] = True  # scroll once, on new turns only
        st.rerun()

    # Scroll to the newest exchange after submission; older messages remain
    # reachable by scrolling up (this fires once per new turn).
    if st.session_state.get("scroll_pending"):
        st.session_state["scroll_pending"] = False
        _components_html(_SCROLL_JS, height=0)


if __name__ == "__main__":
    main()
