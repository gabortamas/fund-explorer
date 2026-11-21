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

## Infrastructure track

Alongside the RAG steps, the project is a hands-on tutorial in serving and running it:

| Stage | Component | Status |
|---|---|---|
| A | Housekeeping: locked dependencies, data path that works in containers, GitHub remote | done |
| B | Read-only REST API (FastAPI) over the corpus | done |
| C | Docker image, then Docker Compose with Postgres and MinIO | |
| D | Airflow DAG: collect, identify, parse, one task per fund | |
| E | Kubernetes, Helm, Airflow on Kubernetes, Argo Workflows | |

## Setup

```bash
uv sync --extra dev     # creates .venv with the exact versions in uv.lock
uv run pytest
```

`uv.lock` pins every package to an exact version. After changing the dependencies in
`pyproject.toml`, run `uv lock` and commit both files.

The data folder is `FUNDRAG_DATA_DIR` if that environment variable is set, otherwise
`data/` in this checkout.

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

## The REST API

```bash
uv run fundrag serve --reload     # then open http://127.0.0.1:8000/docs
```

| Method and path | What it returns |
|---|---|
| `GET /health` | `{"status": "ok"}`; used by Docker and Kubernetes health checks later |
| `GET /v1/funds?asset_class=&kid_available=&limit=&offset=` | Page of funds with KID status |
| `GET /v1/funds/{isin}` | One fund and the provenance of its latest KID |
| `GET /v1/funds/{isin}/kid` | The KID's sections, without text |
| `GET /v1/funds/{isin}/kid/sections/{key}` | One section with text and pages |

An ISIN with a wrong check digit gets `422`; a fund or KID that does not exist gets
`404`. The code is in `src/fundrag/api/`; `tests/test_api.py` runs against a small
temporary data folder.

## Data and licensing

Fund documents are the issuers' copyright. They are downloaded for personal,
non-commercial research and are not committed to this repository (`data/raw/` is
git-ignored); the manifest records where each one came from.
