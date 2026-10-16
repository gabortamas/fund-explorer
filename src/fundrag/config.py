"""Project paths and settings.

Everything lives under DATA_DIR so the whole corpus can be deleted and rebuilt.
Override the location with the FUNDRAG_DATA_DIR environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FUNDRAG_DATA_DIR", PROJECT_ROOT / "data"))

UNIVERSE_DIR = DATA_DIR / "universe"
RAW_DIR = DATA_DIR / "raw"  # downloaded PDFs, one folder per issuer/ISIN
INBOX_DIR = DATA_DIR / "inbox"  # drop manually downloaded PDFs here
MANIFEST_PATH = DATA_DIR / "manifest.jsonl"  # one line per stored document version

USER_AGENT = (
    "fundrag-research/0.1 (personal non-commercial research project; "
    "contact: github.com/gabortamas)"
)
REQUEST_DELAY_SECONDS = 2.0  # be polite to issuer websites
