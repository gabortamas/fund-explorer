# fundrag — an agentic RAG research assistant for EU funds and ETFs

A portfolio project: help ordinary investors understand and compare EU retail funds
using the documents every such fund must publish, mainly the PRIIPs Key Information
Document (KID). The assistant informs; it does not recommend what to buy.

## Architecture (target)

- **Ingestion (offline):** collect → parse → extract structured KID fields → chunk → index
  (hybrid vector + BM25) → small knowledge graph (fund, share class, index, issuer).
- **Online (LangGraph):** router → profiler / research agent (agentic retrieval with
  query rewriting) / guardrail → writer (with a calculator tool) → verifier
  (deterministic number check + LLM citation check) → answer.
- **Evaluation:** extraction accuracy, retrieval recall@k, answer accuracy, faithfulness,
  guardrail handling, and an ablation ladder: naive → hybrid → agentic → agentic + graph.

## Build steps

| Step | Component | Status |
|---|---|---|
| 1 | Document collection with provenance manifest | done |
| 2 | KID parsing into prescribed sections | done |
| 3 | Structured KID field extraction + validation | |
| 4 | Chunking and hybrid index; naive RAG baseline | |
| 5 | Golden question set and evaluation harness | |
| 6 | Agentic retrieval loop | |
| 7 | Profiler, screener, writer, verifier | |
| 8 | Graph layer | |
| 9 | UI, tracing, write-up | |
| 10 | Collector agent: ISIN in, verified KID out (evaluated against the hand-collected links) | |

## Setup

```bash
uv venv && uv pip install -e ".[dev]"     # or: python -m venv .venv && pip install -e ".[dev]"
pytest
```

## Step 1: collecting KIDs

The scope is a hand-edited CSV: `data/universe/ishares_v1.csv`. Documents enter the
corpus in one of two ways, and both are checked the same way (is it a PRIIPs KID, which
ISINs does it name, what date is it) before being stored with a manifest record.

```bash
fundrag download   # fetch rows that have a direct kid_url
fundrag inbox      # register PDFs you saved by hand into data/inbox/
fundrag status     # what is present, missing, or needs a look
```

Files are stored as `data/raw/<issuer>/<ISIN>/<type>_<lang>_<date>_<id>.pdf` and every
version is listed in `data/manifest.jsonl` with its source URL, SHA-256 and retrieval time.
A re-issued KID becomes a new version; an identical re-download is ignored.

Note: the UK site of an issuer serves UK-style KIIDs, not EU PRIIPs KIDs. Use an EU
country site. The collector flags a KIID if one slips in. iShares country-site links
(`ishares.com/<country>/.../literature/kiid/...`) are rewritten automatically to
BlackRock's direct file server, which needs no cookie.

## Step 2: parsing KIDs into sections

```bash
fundrag parse                      # every stored KID -> data/processed/kid/<doc_id>.json + .md
fundrag show IE00B4L5Y983          # list the sections of one fund's KID
fundrag show IE00B4L5Y983 costs_over_time   # print one section
```

The PRIIPs regulation prescribes the KID's section titles word for word, so the parser
splits on those headings instead of guessing from fonts or layout. Every KID must yield
the nine required sections in the prescribed order; anything else is reported as a
problem. Sub-parts (objectives, risk indicator, performance scenarios, costs over time,
composition of costs) are kept as a second level, and every section records its pages
for citations. Text comes from pdfplumber, which keeps each table row on one line
(`Total costs 20 USD 180 USD`), the form the extraction step needs.

`tests/test_corpus.py` runs the parser on your real KIDs; a KID with an unusual layout
shows up as a failing test named after its ISIN.

## Data and licensing

Fund documents are the issuers' copyright. They are downloaded for personal,
non-commercial research and are not committed to this repository (`data/raw/` is
git-ignored); the manifest records where each one came from.
