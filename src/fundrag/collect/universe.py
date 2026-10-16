"""The universe: which products are in scope.

A plain CSV you edit by hand, so the scope of the corpus is explicit and reviewable.
Columns:
    isin         required
    name         human-readable name (only for display; the KID text is authoritative)
    issuer       e.g. iShares
    asset_class  equity | bond | property | mixed ...
    language     language of the KID you want, e.g. en
    kid_url      optional direct PDF link; leave empty to add the file by hand via the inbox
    notes        free text
"""

from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel, field_validator

from fundrag.collect.identify import isin_is_valid


class Product(BaseModel):
    isin: str
    name: str
    issuer: str
    asset_class: str = ""
    language: str = "en"
    kid_url: str = ""
    notes: str = ""

    @field_validator("isin")
    @classmethod
    def _check_isin(cls, v: str) -> str:
        v = v.strip().upper()
        if not isin_is_valid(v):
            raise ValueError(f"invalid ISIN check digit: {v}")
        return v


def load_universe(path: Path) -> list[Product]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("isin", "").strip()]
    products = [Product(**{k: (v or "").strip() for k, v in r.items() if k}) for r in rows]
    dupes = {p.isin for p in products if sum(q.isin == p.isin for q in products) > 1}
    if dupes:
        raise ValueError(f"duplicate ISINs in universe: {sorted(dupes)}")
    return products
