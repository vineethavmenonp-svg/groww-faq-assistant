# PRD: Groww Mutual Fund FAQ Assistant

| Field | Value |
| --- | --- |
| Product | Groww Mutual Fund FAQ Assistant (facts-only RAG prototype) |
| Status | Draft |
| Owner | Product |
| Source | `DOC/Problemstatement.txt` |
| Last updated | 2026-11-01 |

---

## 1. Problem

Retail users and support/content teams need fast, trustworthy answers to repetitive mutual-fund scheme facts (expense ratio, exit load, min SIP, ELSS lock-in, riskometer, benchmark, how to download statements).

Today those facts live on public Groww pages and official AMC/SEBI/AMFI materials. Users either hunt across pages or get opinionated answers they should not treat as advice.

**This product answers facts only, with a source link on every response. It does not give investment advice.**

---

## 2. Goals

1. Ship a working FAQ prototype that answers factual questions about **HDFC Mutual Fund AMC + 4 schemes** from **official public pages only**.
2. Make the RAG pipeline **inspectable by stage** (load → chunk → embed → retrieve → generate) so the demo proves how the answer was produced.
3. Keep answers short, cited, and safe: refuse advice, skip PII, skip performance claims.
4. Keep model, vector DB, and key settings in **config / `.env`** so they can be swapped without code changes.

### Non-goals (v1)

- Advice, recommendations, or “should I buy/sell?”
- Returns calculation or performance comparison
- Login, holdings, KYC, or any personal account data
- Coverage beyond the listed HDFC AMC + 4 schemes (except educational links used in refusals)
- Production-grade crawling, auth, or multi-tenant support

---

## 3. Users

| Persona | Need |
| --- | --- |
| Retail user comparing schemes | Fast facts with a link to verify |
| Support / content teammate | Same answers to repetitive MF questions without drafting from scratch |

---

## 4. Scope

### 4.1 Corpus (must scrape and store)

| # | Entity | URL |
| --- | --- | --- |
| 1 | AMC overview — HDFC Mutual Fund | https://groww.in/mutual-funds/amc/hdfc-mutual-funds |
| 2 | Large Cap — HDFC Large Cap Fund Direct Growth | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| 3 | Flexi Cap — HDFC Flexi Cap Direct Plan Growth | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| 4 | ELSS (tax saver) — HDFC ELSS Tax Saver Fund Direct Plan Growth | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| 5 | Mid Cap — HDFC Mid Cap Fund Direct Growth | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |

Supplemental official pages (Groww factsheets, AMFI, SEBI investor education, AMC factsheets) may be added until the source list reaches **15–25 URLs**. Third-party blogs are out of scope as sources.

### 4.2 Question types (in scope)

- Expense ratio
- Exit load
- Minimum SIP
- ELSS lock-in
- Riskometer
- Benchmark
- How to download capital-gains / statements

### 4.3 Out of scope questions (must refuse)

- Should I buy / sell / switch?
- Which fund is better?
- Portfolio construction or allocation
- Predicted or computed returns
- Anything requiring PAN, Aadhaar, account number, OTP, email, or phone

---

## 5. Product principles

1. **Facts only.** Identify the exact fact asked; answer or refuse.
2. **One citation per answer.** Every answer includes one clear source link.
3. **Public sources only.** No app-backend screenshots; no unofficial blogs as sources.
4. **No PII.** Do not accept or store PAN, Aadhaar, account numbers, OTPs, emails, or phones.
5. **No performance claims.** Do not compute or compare returns; if asked, link the official factsheet.
6. **Short and dated.** Answers ≤ 3 sentences. Include `Last updated from sources: <date>`.
7. **Inspectable RAG.** Persist artifacts for each pipeline stage so they can be shown in the demo.

---

## 6. User experience

### 6.1 Tiny UI (v1)

- Welcome line
- 3 example questions
- Persistent note: **“Facts-only. No investment advice.”**
- Single chat input
- Answer card: 1–3 sentences + one source link + last-updated line
- Optional demo panel (strongly recommended for this exercise): show the active RAG stage and stored artifacts (chunks retrieved, scores, source URL)

### 6.2 Example questions (seed)

1. What is the expense ratio of HDFC Large Cap Fund Direct Growth?
2. What is the lock-in for HDFC ELSS Tax Saver?
3. How do I download a capital-gains statement?

### 6.3 Answer patterns

**Fact hit**

- Direct answer in ≤ 3 sentences
- One source URL
- `Last updated from sources: YYYY-MM-DD`

**Refuse advice**

- Polite, facts-only refusal
- No buy/sell language
- One relevant educational link (AMFI/SEBI/Groww explainer)

**Unknown / out of corpus**

- Say the fact is not in the loaded sources
- Do not invent numbers
- Offer the closest official page if available

**PII detected**

- Do not store the payload
- Ask the user to remove personal identifiers and re-ask the factual question

---

## 7. Functional requirements

### FR-1 Data loading

- Fetch the 5 seed pages plus approved official URLs (15–25 total).
- Store raw HTML/text as an inspectable artifact (`raw/` or equivalent).
- Record URL, fetch timestamp, and HTTP status.

### FR-2 Data chunking

- Choose a strategy that fits this corpus (scheme pages are semi-structured: labels + values + short prose).
- **Recommended default:** section-aware chunks (heading / fact-row / FAQ block) with a small overlap; fall back to recursive character splitting for long unstructured blocks.
- Persist chunks with `chunk_id`, `source_url`, `scheme_or_amc`, `section`, `text`.

### FR-3 Embeddings

- Model: `sentence-transformers/all-MiniLM-L6-v2`
- Model name lives in config (not hardcoded).
- Persist embeddings and the mapping to chunk IDs.

### FR-4 Vector store

- ChromaDB
- Collection name, persist directory, and distance metric live in config.

### FR-5 Retrieval

- Embed the query with the **same** embedding model used at ingest.
- Query the **same** Chroma collection.
- Return top-k chunks with scores and source URLs; persist the retrieval trace.

### FR-6 Generation

- LLM via Groq (primary). Model id from `.env` (problem statement names `qwen/qwen3.8-27b`; implement as an env-driven id so it can be corrected to the live Groq model slug).
- Config must allow swapping provider later: Gemini, OpenAI, Anthropic, Mistral (stubs or adapters; Groq is v1).
- Prompt must: extract the asked fact, cite one URL, cap at 3 sentences, refuse advice, never invent numbers.

### FR-7 Safety

- Classifier or prompt rules for advice vs fact vs PII vs performance.
- Performance questions: no computed returns; link official factsheet.
- PII: reject and do not persist user message contents that contain identifiers.

### FR-8 Demo artifacts

For each stage, write files (or a local viewer) that can be shown:

| Stage | Artifact |
| --- | --- |
| Load | Raw page text + metadata |
| Chunk | Chunk table / JSON |
| Embed | Embedding job log (model, dim, count) |
| Retrieve | Query, top-k chunks, scores, URLs |
| Generate | Prompt, model, final answer, citation |

---

## 8. Phased delivery

Build in this order. Each phase has a stored artifact and a demo checkpoint.

| Phase | Name | Done when |
| --- | --- | --- |
| 0 | Foundations | Repo, README skeleton, `config` + `.env.example`, disclaimer copy |
| 1 | Data loading | 5 seed URLs fetched and stored with timestamps |
| 2 | Chunking | Chunk files exist; strategy documented in README |
| 3 | Embedding | Vectors written with `all-MiniLM-L6-v2` |
| 4 | Vector DB | Chroma collection queryable locally |
| 5 | Retrieval | Sample queries return cited chunks |
| 6 | LLM + safety | Groq answers / refusals with one link and last-updated line |
| 7 | Tiny UI | Welcome, 3 examples, facts-only note, chat |
| 8 | Deliverables pack | Source list, sample Q&A, demo video or hosted link |

---

## 9. Configuration

All of the following must be changeable without editing application logic.

**Config file (e.g. `config.yaml` or `config.py`)**

- Embedding model name
- Chroma persist path and collection name
- Chunk size / overlap / strategy key
- Retrieval `top_k`
- Allowed source URL allowlist
- UI copy: welcome, examples, disclaimer
- LLM provider key (`groq` default)

**`.env` (secrets and model ids)**

- `GROQ_API_KEY`
- `LLM_MODEL` (e.g. the Groq Qwen model slug)
- Optional later: `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`

Do not commit secrets. Do not store user PII in logs.

---

## 10. Skills the prototype must demonstrate

| Skill | Product interpretation |
| --- | --- |
| W1 — Thinking like a model | Detect the exact fact; answer vs refuse |
| W2 — LLMs & prompting | Concise instruction style, polite refusals, citation wording |
| W3 — RAG only | Small-corpus retrieval with accurate citations from AMC / SEBI / AMFI / Groww public pages |

This is a **RAG demo**, not a fine-tuned advisor.

---

## 11. Success metrics (prototype)

Qualitative, not production analytics:

| Check | Target |
| --- | --- |
| Sample Q&A (5–10) | Correct fact + valid official link |
| Advice prompts | 100% refuse with educational link |
| PII prompts | No storage; safe refusal |
| Returns prompts | No computed/compared returns |
| Answer length | ≤ 3 sentences |
| Citation | Exactly one visible source link |
| Stage demo | Load/chunk/embed/retrieve/generate artifacts visible |

---

## 12. Deliverables

1. Working prototype link, or a ≤ 3-minute demo video if hosting is not possible
2. Source list (CSV or Markdown) of the 15–25 URLs used
3. README: setup, scope (AMC + schemes), known limits
4. Sample Q&A file (5–10 queries with answers + links)
5. Disclaimer snippet used in the UI (facts-only, no advice)

---

## 13. Risks and known limits

| Risk | Mitigation |
| --- | --- |
| Groww pages change layout | Store raw snapshots; chunk by labels not CSS |
| Scraping blocked or JS-rendered | Prefer static text extract; document fallback |
| Model slug in the brief may not match Groq catalog | Read model from `.env`; document the working slug |
| Hallucinated numbers | Ground strictly on retrieved chunks; refuse if chunks lack the fact |
| Users ask for advice anyway | Hard refusal template + educational link |
| Scope creep to all AMCs | Freeze corpus to HDFC AMC + 4 schemes for v1 |

---

## 14. Open decisions

1. Hosted Streamlit/Gradio app vs local notebook + demo video
2. Exact Groq model slug to put in `.env` after checking current Groq catalog
3. Whether the UI shows RAG-stage artifacts in-product or as files in the repo
4. Final 15–25 URL list (seed 5 + official factsheets / AMFI / SEBI pages)

---

## 15. Disclaimer (UI snippet)

> Facts-only. No investment advice. Answers are taken from official public pages and always include a source link. This assistant cannot recommend buying, selling, or holding any scheme. Do not share PAN, Aadhaar, account numbers, OTPs, email, or phone number.

---

## 16. Appendix — suggested sample evaluation queries

1. Expense ratio of HDFC Large Cap Fund Direct Growth?
2. Exit load of HDFC Flexi Cap Direct Plan Growth?
3. Minimum SIP for HDFC Mid Cap Fund Direct Growth?
4. ELSS lock-in for HDFC ELSS Tax Saver?
5. Riskometer of HDFC Large Cap Fund Direct Growth?
6. Benchmark of HDFC Flexi Cap Direct Plan Growth?
7. How to download a capital-gains statement?
8. Should I buy HDFC Mid Cap? *(must refuse)*
9. Which of these is best for me? *(must refuse)*
10. What was the 5-year return vs Nifty? *(no computed comparison; link factsheet)*
