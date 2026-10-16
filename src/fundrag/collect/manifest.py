"""The manifest: one JSON line per stored document version.

This is the corpus's provenance record. Every chunk and every extracted number we
produce later will point back to a `doc_id` here, so an answer can say exactly which
document version (and which download) it came from.

Design choices:
  * Append-only JSONL: easy to diff in git, nothing is ever silently overwritten.
  * `doc_id` is derived from the file's SHA-256, so the same PDF downloaded twice
    is recognised as the same document, and a re-issued KID gets a new id.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel

from fundrag import config
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


def load(manifest_path: Path | None = None) -> list[ManifestRecord]:
    manifest_path = manifest_path or config.MANIFEST_PATH
    if not manifest_path.exists():
        return []
    with manifest_path.open(encoding="utf-8") as f:
        return [ManifestRecord.model_validate_json(line) for line in f if line.strip()]


def known_hashes(manifest_path: Path | None = None) -> set[str]:
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
    manifest_path = manifest_path or config.MANIFEST_PATH

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
