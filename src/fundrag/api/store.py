"""Where the API gets its data from.

The API never opens files itself; it asks a FundStore. Why the extra layer?
  * Tests can hand the API a FundStore pointing at a small temporary folder.
  * The manifest can live in a JSONL file or in Postgres (see collect/manifest.py);
    the endpoints in app.py do not need to know which.

The data is re-read on every request: the files are tiny (a CSV, a few JSON files)
and the manifest query takes about a millisecond. In return the API never shows stale
data after you run `fundrag download` or `fundrag parse`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fundrag import config
from fundrag.collect import manifest
from fundrag.collect.universe import Product, load_universe
from fundrag.parse import pipeline


@dataclass
class FundStore:
    universe_path: Path = field(default_factory=lambda: config.DEFAULT_UNIVERSE)
    # None = the configured manifest: Postgres if FUNDRAG_DATABASE_URL is set, otherwise
    # data/manifest.jsonl. Tests pass a path to a small JSONL file instead.
    manifest_path: Path | None = None
    processed_dir: Path = field(default_factory=lambda: config.PROCESSED_DIR / "kid")

    def products(self) -> list[Product]:
        return load_universe(self.universe_path)

    def product(self, isin: str) -> Product | None:
        return next((p for p in self.products() if p.isin == isin), None)

    def latest_kids(self) -> dict[str, manifest.ManifestRecord]:
        """The newest KID per ISIN, from the manifest."""
        return manifest.latest_by_isin(manifest.load(self.manifest_path))

    def parsed_kid(self, doc_id: str) -> pipeline.ParsedDocument | None:
        """The parsed sections of one KID, or None if `fundrag parse` has not run on it."""
        try:
            return pipeline.load(doc_id, self.processed_dir)
        except FileNotFoundError:
            return None

    def is_parsed(self, doc_id: str) -> bool:
        json_path, _ = pipeline.output_paths(doc_id, self.processed_dir)
        return json_path.exists()


def get_store() -> FundStore:
    """FastAPI calls this for every request that needs data (see `Depends` in app.py)."""
    return FundStore()
