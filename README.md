# Groww Mutual Fund FAQ Assistant

Facts-only RAG prototype for HDFC Mutual Fund scheme questions (expense ratio, exit load, min SIP, ELSS lock-in, riskometer, benchmark, statements). Every answer includes one official source link.

**Facts-only. No investment advice.** Do not share PAN, Aadhaar, account numbers, OTPs, email, or phone.

## Setup

Python 3.9+ (3.11+ preferred)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Working model: set `LLM_MODEL` in `.env` (Groq slug; verified live: `qwen/qwen3.8-27b` — the `qwen/qwen3-32b` slug does not exist on Groq; `openai/gpt-oss-120b`/`20b` also available on this key). `GroqClient` caps `max_tokens=256` to stay within free-tier output-token limits. Key, chunking, and the source allowlist live in `config/config.yaml` / `.env`.

## Run

```bash
# Scrape the allowlisted pages into artifacts/raw/ (implemented)
python -m src.ingest load
# Full ingest once later steps are wired:
python -m src.ingest
streamlit run src/ui/app.py
```

Scraping, chunking, embedding, indexing, and the query pipeline are wired: scraping fetches only URLs from `config.sources.allowlist` and saves each page as `<page_id>.html`, `.txt`, `.json` in `artifacts/raw/` (non-200/error pages are recorded in `artifacts/raw/failures.json` and skipped, without crashing the batch). Chunking builds section-aware chunks (config: `chunk.strategy/size/overlap`) into `artifacts/chunks/chunks.jsonl` + `summary.json`. Embedding encodes chunk text with `sentence-transformers/all-MiniLM-L6-v2` (batch 32) into `artifacts/embed/job.json`, `ids.json`, `vectors.npy`. Indexing replaces the configured Chroma collection (config: `chroma.persist_dir/collection/distance`) at `data/chroma/` with `chunk_id` + metadata points. The query pipeline (`python -m src.query.pipeline --q "..."`) runs the safety gate (PII / advice / performance / fact), embeds the query, retrieves from the same Chroma collection with section- and scheme-aware reranking, refuses below `retrieval.min_score`, and answers facts with Groq grounded on retrieved chunks — one official URL and `Last updated from sources:` come from code, never from model output. Traces land in `artifacts/retrieve/` and `artifacts/generate/`; PII requests are refused without touching disk.

## Run the application

```bash
# One-time corpus build (scrape → chunk → embed → index; re-run to refresh)
python -m src.ingest

# Streamlit chat UI (in-memory session, demo panel on the sidebar)
streamlit run src/ui/app.py

# Or CLI equivalence for a single question
python -m src.query.pipeline --q "Expense ratio of HDFC Large Cap Fund Direct Growth?"
```

The UI fails closed with `Run: python -m src.ingest` when the vector index is missing or empty, and surfaces a config error for a missing `GROQ_API_KEY`/`LLM_MODEL` or a Groq outage — no ungrounded fallback by design. Refusal modes, the `unknown` path, retrieval, traces, and the UI work without the Groq key; live fact answers start once `GROQ_API_KEY` is filled in `.env` (kept private; `.env` is gitignored).

## Known limits

- **Corpus = the 5 approved Groww pages only** (AMC overview + Large Cap + Flexi Cap + ELSS Tax Saver + Mid Cap). Facts outside these pages (e.g. *"How do I download a capital-gains statement?"*) answer `unknown` with the closest official page as the single citation — by design, never invented. Broader coverage (15–25 official URLs) was scoped out for this version; the allowlist format in `config/config.yaml` supports adding pages later (`python -m src.ingest` refreshes everything).
- **Statement / tax-filing questions are not answerable** from the 5 corpus pages and are intentionally converted to the `unknown` behavior above.
- **Cross-fund comparisons** ("compare TER of Large Cap vs Flexi Cap") also return `unknown`: the generator enforces a single-fund, single-excerpt grounding rule to prevent mixing values across schemes.
- **Site chrome noise**: Groww pages embed navigation and peer-fund tables, which dilute retrieval ranking; this is mitigated by section- and scheme-aware reranking (`src/query/retrieve.py`), but occasional citation of a neighbouring scheme's page can still occur when facts are identical across pages (e.g. benchmark names).
- **Layout-change risk**: UX/structure changes on groww.in can break the section tagger (`src/ingest/chunk.py`). Raw HTML snapshots (`artifacts/raw/*.html`) are preserved for re-chunking without re-scraping.
- **Free-tier LLM limits**: Groq free tier caps output tokens (~1k TPM OTPM); `GroqClient` sets `max_tokens=256` and answers are capped at 3 sentences, so bursts of rapid live queries can still hit 429 rate limits.
- **PEP 668/py3.9 note**: the venv targets Python 3.9 (matches `sentence-transformers` CPU wheels in use); requirements list pins no versions — for a pinned production lock, export with `pip freeze`.

## Deployment (prepared, not deployed)

The app is a standard Streamlit service and is deploy-ready:

1. **Host requirements**: any Python 3.9+ host (Streamlit Community Cloud / Render / Docker). `requirements.txt` lists all runtime deps; `streamlit` is the process entrypoint.
2. **Env vars (host)**: `GROQ_API_KEY` and `LLM_MODEL=qwen/qwen3.8-27b` must be set in the host environment — never commit `.env` (it is gitignored).
3. **One-time corpus build on the host**:
   ```bash
   python -m src.ingest        # load → chunk → embed → index
   ```
4. **Start command**:
   ```bash
   streamlit run src/ui/app.py --server.port $PORT --server.address 0.0.0.0
   ```
5. **Data folders**: `data/chroma/` (index) and `artifacts/` (raw pages, chunks, vectors, traces) are build outputs; `artifacts/retrieve|generate` traces are runtime-created per question.
