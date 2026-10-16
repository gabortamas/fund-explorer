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
| 1 | Document collection with provenance manifest | in progress |
| 2 | PDF parsing (layout-aware) | |
| 3 | Structured KID field extraction + validation | |
| 4 | Chunking and hybrid index; naive RAG baseline | |
| 5 | Golden question set and evaluation harness | |
| 6 | Agentic retrieval loop | |
| 7 | Profiler, screener, writer, verifier | |
| 8 | Graph layer | |
| 9 | UI, tracing, write-up | |

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
country site. The collector flags a KIID if one slips in.

## Data and licensing

Fund documents are the issuers' copyright. They are downloaded for personal,
non-commercial research and are not committed to this repository (`data/raw/` is
git-ignored); the manifest records where each one came from.
