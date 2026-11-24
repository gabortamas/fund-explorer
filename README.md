# fundrag — an agentic RAG research assistant for EU funds and ETFs

A portfolio project: help ordinary investors understand and compare EU retail funds
using the documents every such fund must publish, mainly the PRIIPs Key Information
Document (KID). The assistant informs; it does not recommend what to buy.

## Current state and next steps (as of 24 Nov 2026)

**Where things stand**

- **Corpus:** 24 iShares funds in `data/universe/ishares_v1.csv`, all 24 with a clean
  English PRIIPs KID, parsed into 17 sections each. 3 were collected by hand, 21 by the
  collector agent. KID dates range from 2026-04-09 to 2026-09-21.
- **Collector agent (step 10, moved to the front):** works. On 22-24 Nov it found all
  21 missing KIDs, nearly always on its first or second candidate link, with 14 web
  searches in total. A dry run on the 3 hand-collected funds found the same files, but
  2 of those 3 were named in the prompt's examples at the time (since removed), so
  only one of them counts as a fair test.
- **RAG track:** steps 1 (collection) and 2 (parsing) done.
- **Infrastructure track:** stages A (housekeeping) and B (read-only REST API) done;
  stage C has the `Dockerfile` and `docker-compose.yml` (API + Postgres). The manifest
  can now live in a Postgres table (`fundrag db init`, `fundrag db import`).
  **Parked on purpose** until there is something that needs it (parallel runs in
  stage D). Without `FUNDRAG_DATABASE_URL` everything works without Docker.
- **Tests:** 78 (`uv run pytest`; some run once per real KID); 4 of them need a running
  Postgres and are skipped unless `FUNDRAG_TEST_DATABASE_URL` is set (see "Running with Docker Compose").
- **Environment:** Windows laptop, project in `C:\Projects\fund_explorer`, Python 3.14,
  uv, Docker Desktop on WSL 2. Code on GitHub: `gabortamas/fund-explorer` (public;
  the KID files stay local).

**Known limits of the current design**

- Without `FUNDRAG_DATABASE_URL` the manifest is still the JSONL file, which two
  processes must not write at once. Anything that runs in parallel (Airflow, several
  containers) must use Postgres.
- PDFs and parsed files live in local folders, which containers in Kubernetes do not
  share by default.
- The agent's search knowledge is iShares-specific (the system prompt and the list of
  issuer websites in `collect/verify.py`). A new issuer needs both extended.

**Next steps, in order**

1. **RAG step 3 onwards:** structured field extraction from the KIDs, then chunking,
   index and a naive RAG baseline.
2. **Collector, later:** a fair re-run of the 3-fund evaluation; a "newest version"
   check (the agent accepts the first valid KID it finds, not necessarily the newest);
   a cheap rule before the agent once the corpus covers hundreds of funds.
3. **Infrastructure, when it is needed:** MinIO (stage C part 3); Airflow (stage D), a
   DAG that collects, identifies and parses, one task per fund.

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
| 10 | Collector agent: ISIN in, verified KID out (evaluated against the hand-collected links) | done (moved forward) |

## Infrastructure track

Alongside the RAG steps, the project is a hands-on tutorial in serving and running it:

| Stage | Component | Status |
|---|---|---|
| A | Housekeeping: locked dependencies, data path that works in containers, GitHub remote | done |
| B | Read-only REST API (FastAPI) over the corpus | done |
| C | Docker image, then Docker Compose with Postgres and MinIO | image, Postgres done; parked |
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
country site; the Luxembourg site in English (`blackrock.com/lu/individual/en/...`) has
English PRIIPs KIDs. The collector flags a KIID if one slips in. Country-site links
(`ishares.com/<country>/.../literature/kiid/...` and the same on `blackrock.com`) are
rewritten automatically to BlackRock's direct file server, which needs no cookie.

## Step 1b: the collector agent finds KIDs for you

```bash
fundrag find IE00B0M62Q58            # one fund (or several ISINs)
fundrag find --missing               # every fund in the universe without a KID
fundrag find --dry-run IE00B4L5Y983  # check what it finds, but store nothing
```

The agent gets a goal ("the English PRIIPs KID of this ISIN"), three tools and a
budget, and decides step by step what to do:

| Tool | Runs where | What it does |
|---|---|---|
| `web_search` | at Anthropic | searches the web ($10 per 1000 searches; at most 5 per fund) |
| `web_fetch` | at Anthropic | reads a page on the issuer's own website (at most 5 per fund) |
| `try_kid_url` | on your laptop | downloads a candidate PDF and runs the checks of step 1 |

Only `try_kid_url` can store a document, and only if the file is on the issuer's own
website, is an English PRIIPs KID and names the right ISIN (`collect/verify.py`).
A rejection tells the agent why, so it can try again (at most 6 candidates per fund).
The terminal shows each step; the full record of every run, with token counts, is
saved in `data/agent_runs/`. The code is in `collect/agent.py`; `tests/test_agent.py`
tests the loop with a fake web and a scripted fake Claude, so the tests cost nothing.

The model is `claude-sonnet-5-5` unless `--model` or `FUNDRAG_AGENT_MODEL` says
otherwise (for example `claude-haiku-5-5`, cheaper).

**Getting an API key** (once):

1. Go to https://platform.claude.com and sign in or create an account. This is the
   Claude Console, an account separate from a claude.ai subscription.
2. Settings → Billing → "Buy credits". The API is prepaid. Start small (say $5) and
   look at the token counts of the first runs before running all 21 funds.
3. Settings → API keys → "Create key". Copy it right away; it is shown only once.
4. Store it as an environment variable for your Windows user, in PowerShell:

   ```powershell
   setx ANTHROPIC_API_KEY "sk-ant-..."
   ```

   then close and reopen the terminal (setx only affects new windows). Check with
   `echo $env:ANTHROPIC_API_KEY`. Never put the key into a file in this repository.

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

## Running with Docker Compose (API + Postgres)

```powershell
copy .env.example .env               # once; then put your own password into .env
docker compose up --build -d         # build the image, start Postgres and the API
docker compose exec api fundrag db import    # once: copy manifest.jsonl into Postgres
docker compose exec api fundrag status       # first line: "Manifest: Postgres ..."
```

Then open http://127.0.0.1:8000/docs as before. `docker compose ps` shows both
containers and whether they are healthy; `docker compose logs -f api` shows the API's
output; `docker compose down` stops everything. The database lives in the Docker volume
`pgdata` and survives `down`; `docker compose down -v` deletes it (then run the import
again).

Where the manifest lives depends on one environment variable:

| `FUNDRAG_DATABASE_URL` | Manifest | Typical use |
|---|---|---|
| set | the `manifest` table in that Postgres database | inside Compose (set for you) |
| not set | `data/manifest.jsonl` | quick work on the laptop, the tests |

So after the switch, run corpus commands *inside* the API container, where the
variable is set: `docker compose exec api fundrag download` (or `inbox`, `parse`).
A plain `uv run fundrag download` on the laptop would write to the JSONL file instead;
`fundrag db import` picks such records up later (it skips the ones already imported).
To point laptop commands at the Compose database instead (PowerShell, this window only):

```powershell
$env:FUNDRAG_DATABASE_URL = "postgresql://fundrag:<password>@localhost:5432/fundrag"
uv run fundrag status
$env:FUNDRAG_TEST_DATABASE_URL = $env:FUNDRAG_DATABASE_URL
uv run pytest                        # now also runs tests/test_db.py against Postgres
```

`tests/test_db.py` works in a throw-away schema and never touches the real table.
If port 5432 is taken (a Postgres installed directly on Windows), change the left
number in `"127.0.0.1:5432:5432"` and in the URL.

## Data and licensing

Fund documents are the issuers' copyright. They are downloaded for personal,
non-commercial research and are not committed to this repository (`data/raw/` is
git-ignored); the manifest records where each one came from.
