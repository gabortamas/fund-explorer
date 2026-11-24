"""Project paths and settings.

Everything lives under DATA_DIR so the whole corpus can be deleted and rebuilt.

Where is DATA_DIR? Checked in this order:
  1. the FUNDRAG_DATA_DIR environment variable, if set (Docker and Kubernetes use this)
  2. the `data/` folder of the source checkout, if the code runs from one
     (your laptop: C:\\Projects\\fund_explorer\\data)
  3. `data/` in the current working directory, as a last resort

Why not just use the folder next to the code? Because once the package is installed
normally (as it will be inside a Docker image), the code lives in site-packages, and
"next to the code" would point somewhere that has nothing to do with the data.
"""

from __future__ import annotations

import os
from pathlib import Path


def _default_data_dir() -> Path:
    if env := os.environ.get("FUNDRAG_DATA_DIR"):
        return Path(env)
    checkout = Path(__file__).resolve().parents[2]  # src/fundrag/config.py -> project root
    if (checkout / "pyproject.toml").exists():
        return checkout / "data"
    return Path.cwd() / "data"


DATA_DIR = _default_data_dir()

UNIVERSE_DIR = DATA_DIR / "universe"
RAW_DIR = DATA_DIR / "raw"  # downloaded PDFs, one folder per issuer/ISIN
INBOX_DIR = DATA_DIR / "inbox"  # drop manually downloaded PDFs here
MANIFEST_PATH = DATA_DIR / "manifest.jsonl"  # one line per stored document version
PROCESSED_DIR = DATA_DIR / "processed"  # parsed text and, later, extracted fields
AGENT_RUNS_DIR = DATA_DIR / "agent_runs"  # one JSON record per collector-agent run
DEFAULT_UNIVERSE = UNIVERSE_DIR / "ishares_v1.csv"

# Where the manifest lives. If FUNDRAG_DATABASE_URL is set, in that Postgres database,
# e.g. postgresql://fundrag:<password>@localhost:5432/fundrag (Docker Compose sets it for
# the API container). If it is not set, in MANIFEST_PATH above, as before: handy for
# quick work on the laptop without starting a database, and for the tests.
DATABASE_URL = os.environ.get("FUNDRAG_DATABASE_URL") or None

USER_AGENT = (
    "fundrag-research/0.1 (personal non-commercial research project; "
    "contact: github.com/gabortamas)"
)
REQUEST_DELAY_SECONDS = 2.0  # be polite to issuer websites
