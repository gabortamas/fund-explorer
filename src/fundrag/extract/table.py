"""Run an extractor over every parsed KID and keep the result as a table (one CSV per
method in data/processed/fields/), so the extractors can be compared side by side."""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

from fundrag import config
from fundrag.collect import manifest
from fundrag.extract.rules import extract_rules
from fundrag.extract.schema import COLUMNS, KidFields
from fundrag.parse import pipeline
from fundrag.parse.pipeline import ParsedDocument

FIELDS_DIR = config.PROCESSED_DIR / "fields"
EXTRACTORS: dict[str, Callable[[ParsedDocument], KidFields]] = {"rules": extract_rules}


def extract_all(method: str = "rules") -> dict[str, KidFields]:
    """{ISIN: fields} for the latest parsed KID of every ISIN in the manifest."""
    extractor = EXTRACTORS[method]
    out = {}
    for isin, record in manifest.latest_by_isin(manifest.load()).items():
        try:
            doc = pipeline.load(record.doc_id)
        except FileNotFoundError:
            continue  # not parsed yet
        out[isin] = extractor(doc)
    return out


def save(fields: dict[str, KidFields], method: str, fields_dir: Path | None = None) -> Path:
    path = (fields_dir or FIELDS_DIR) / f"{method}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for isin in sorted(fields):
            writer.writerow(fields[isin].model_dump())
    return path
