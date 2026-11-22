# fundrag — an agentic RAG research assistant for EU funds and ETFs

A portfolio project: help ordinary investors understand and compare EU retail funds
using the documents every such fund must publish, mainly the PRIIPs Key Information
Document (KID). The assistant informs; it does not recommend what to buy.

## Current state and next steps (as of 22 Nov 2026)

**Where things stand**

- **Corpus:** 24 iShares funds in `data/universe/ishares_v1.csv`; 3 of them have a KID
  (IE00B4L5Y983 MSCI World, IE00B4WXJJ64 EUR Govt Bond, IE00B1XNHC34 Global Clean
  Energy Transition), all dated 2026-09-03 and parsed into sections. Their links were
  found by hand. The other 21 have no `kid_url` yet: iShares links contain the fund
  name, so they cannot be built from the ISIN alone.
- **RAG track:** steps 1 (collection) and 2 (parsing) done. No LLM calls yet.
- **Infrastructure track:** stages A (housekeeping) and B (read-only REST API) done;
  stage C has its first part, the `Dockerfile`.
- **Tests:** 36, all passing (`uv run pytest`).
- **Environment:** Windows laptop, project in `C:\Projects\fund_explorer`, Python 3.14,
  uv, Docker Desktop on WSL 2. Code on GitHub: `gabortamas/fund-explorer` (public;
  the KID files stay local).

**Known limits of the current design**

- The manifest is a JSONL file appended to by whichever process runs. Two processes
  writing at once (parallel Airflow tasks, several containers) could corrupt it.
- PDFs and parsed files live in local folders, which containers in Kubernetes do not
  share by default.
- Finding KID links is manual, so the corpus cannot grow without someone searching.

**Next steps, in order**

1. **Stage C, part 2: Docker Compose with Postgres.** One `docker-compose.yml` that
   starts the API and a Postgres database together. Move the manifest from
   `manifest.jsonl` into a Postgres table, with a one-off script that imports the
   existing records. In the API only `src/fundrag/api/store.py` should need to change.
   File storage (MinIO) comes after that.
2. **Collector (step 10, moved forward).** Input: an ISIN; output: a verified KID.
   First a plain rule that reads the issuer's own fund listings, then an agent (Claude
   API with web search) for funds the rule cannot handle. Every candidate PDF must pass
   the existing checks in `collect/identify.py` before it is stored. Evaluated against
   the three hand-collected links.
3. **Stage D: Airflow.** A DAG that collects, identifies and parses, one mapped task
   per fund, running in Docker Compose.
4. **RAG steps 3 onwards**, each adding a DAG task and an API endpoint as it lands.

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
| C | Docker image, then Docker Compose with Postgres and MinIO | image done |
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

## Running the API in Docker

```bash
docker build -t fund-explorer-api .
docker run --rm -p 8000:8000 -v "${PWD}/data:/data" fund-explorer-api
```

The image holds the code and its exact dependencies but no data. `-v` attaches the
local `data/` folder at `/data` inside the container (where `FUNDRAG_DATA_DIR` points),
and `-p 8000:8000` connects port 8000 on the host to the API in the container. The
container runs as a non-root user and reports its health through `GET /health`.

## Data and licensing

Fund documents are the issuers' copyright. They are downloaded for personal,
non-commercial research and are not committed to this repository (`data/raw/` is
git-ignored); the manifest records where each one came from.
