"""The manifest: one JSON line per stored document version.

This is the corpus's provenance record. Every chunk and every extracted number we
produce later will point back to a `doc_id` here, so an answer can say exactly which
document version (and which download) it came from.

Design choices:
  * Records are only ever added, never changed: nothing is silently overwritten.
  * `doc_id` is derived from the file's SHA-256, so the same PDF downloaded twice
    is recognised as the same document, and a re-issued KID gets a new id.

Where the records are kept (the "backend"):
  * Postgres, if FUNDRAG_DATABASE_URL is set (see fundrag/db.py), else
  * the JSONL file data/manifest.jsonl, one JSON object per line.

The rule for the functions below: pass `manifest_path` and you get that JSONL file
(the tests and `fundrag db import` do this); pass nothing and you get whichever
backend is configured. The CLI, the parser and the API pass nothing, so all of them
switch to Postgres together when the variable is set.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel

from fundrag import config, db
from fundrag.collect.identify import PdfInspection


class ManifestRecord(BaseModel):
    doc_id: str  # first 16 hex chars of the SHA-256
    sha256: str
    isin: str  # the ISIN we filed it under
    isins_in_doc: list[str]  # every valid ISIN found in the text
    issuer: str
    doc_type: str  # priips_kid | ucits_kiid | unknown
    doc_type_confidence: float
    language: str
    doc_date: date | None
    doc_date_cued: bool
    n_pages: int
    title_guess: str | None
    source: str  # URL, or "manual:<original file name>"
    retrieved_at: datetime
    path: str  # relative to DATA_DIR
    problems: list[str]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _use_db(manifest_path: Path | None) -> bool:
    return manifest_path is None and config.DATABASE_URL is not None


def location(manifest_path: Path | None = None) -> str:
    """Human-readable answer to "where is the manifest right now?"."""
    if _use_db(manifest_path):
        return db.describe()
    return str(manifest_path or config.MANIFEST_PATH)


def load(manifest_path: Path | None = None) -> list[ManifestRecord]:
    if _use_db(manifest_path):
        return [ManifestRecord.model_validate(row) for row in db.load_all()]
    manifest_path = manifest_path or config.MANIFEST_PATH
    if not manifest_path.exists():
        return []
    with manifest_path.open(encoding="utf-8") as f:
        return [ManifestRecord.model_validate_json(line) for line in f if line.strip()]


def known_hashes(manifest_path: Path | None = None) -> set[str]:
    if _use_db(manifest_path):
        return db.known_hashes()
    return {r.sha256 for r in load(manifest_path)}


def store(
    tmp_file: Path,
    *,
    isin: str,
    issuer: str,
    language: str,
    source: str,
    inspection: PdfInspection,
    data_dir: Path | None = None,
    manifest_path: Path | None = None,
) -> tuple[ManifestRecord | None, str]:
    """Move a PDF into the corpus and append its manifest record.

    Returns (record, status) where status is 'stored' or 'duplicate'.
    Duplicates (same SHA-256 already in the manifest) are not stored again.
    """
    data_dir = data_dir or config.DATA_DIR
    use_db = _use_db(manifest_path)

    digest = sha256_of(tmp_file)
    if digest in known_hashes(manifest_path):
        return None, "duplicate"

    doc_id = digest[:16]
    doc_type = inspection.doc_type
    dated = inspection.doc_date.isoformat() if inspection.doc_date else "undated"
    rel_path = Path("raw") / issuer.lower() / isin / f"{doc_type}_{language}_{dated}_{doc_id}.pdf"
    dest = data_dir / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tmp_file, dest)

    problems = list(inspection.problems)
    if inspection.isins and isin not in inspection.isins:
        problems.append(f"filed under {isin}, but the document mentions {inspection.isins}")

    record = ManifestRecord(
        doc_id=doc_id,
        sha256=digest,
        isin=isin,
        isins_in_doc=inspection.isins,
        issuer=issuer,
        doc_type=doc_type,
        doc_type_confidence=inspection.doc_type_confidence,
        language=language,
        doc_date=inspection.doc_date,
        doc_date_cued=inspection.doc_date_cued,
        n_pages=inspection.n_pages,
        title_guess=inspection.title_guess,
        source=source,
        retrieved_at=datetime.now(UTC),
        path=rel_path.as_posix(),
        problems=problems,
    )
    if use_db:
        # The database refuses a second row with the same sha256. This catches the rare
        # case where another process stored the same file a moment after our check above.
        # (The file it copied is identical to ours and has the same name, so no harm.)
        if not db.insert(record.model_dump()):
            return None, "duplicate"
    else:
        manifest_path = manifest_path or config.MANIFEST_PATH
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")
    return record, "stored"


def latest_by_isin(records: list[ManifestRecord], doc_type: str = "priips_kid") -> dict:
    """The newest document of a given type per ISIN (by document date, then retrieval time)."""
    best: dict[str, ManifestRecord] = {}
    for r in records:
        if r.doc_type != doc_type:
            continue
        key = (r.doc_date or date.min, r.retrieved_at)
        cur = best.get(r.isin)
        if cur is None or key > (cur.doc_date or date.min, cur.retrieved_at):
            best[r.isin] = r
    return best
