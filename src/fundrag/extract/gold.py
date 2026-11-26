"""Read the hand-made answer key (data/gold/kid_fields_gold.csv).

The file may come back from Excel in a different shape than it was written: with a
German or Hungarian regional setting, Excel saves semicolons between the columns and
decimal commas ("0,20"). Both shapes are accepted here.
"""

from __future__ import annotations

import csv
from pathlib import Path

from fundrag import config
from fundrag.extract.schema import COLUMNS, KidFields

GOLD_PATH = config.DATA_DIR / "gold" / "kid_fields_gold.csv"
_TEXT_FIELDS = {"isin", "share_class", "distribution", "currency", "benchmark_index",
                "management_style"}  # fmt: skip


def load_gold(path: Path | None = None) -> dict[str, KidFields]:
    """{ISIN: correct fields} for every filled-in row of the answer key."""
    path = path or GOLD_PATH
    text = path.read_text(encoding="utf-8-sig")  # -sig: Excel sometimes adds a BOM
    delimiter = ";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","
    rows = csv.DictReader(text.splitlines(), delimiter=delimiter)
    gold = {}
    for row in rows:
        values = {}
        for col in COLUMNS:
            raw = (row.get(col) or "").strip()
            if raw == "":
                continue
            if col not in _TEXT_FIELDS and delimiter == ";":
                raw = raw.replace(",", ".")  # decimal comma
            values[col] = raw
        if len(values) > 1:  # more than just the ISIN: the row has been filled in
            gold[values["isin"]] = KidFields(**values)
    return gold
