"""Tests for the Postgres manifest (fundrag/db.py and the database half of manifest.py).

Most of these need a running Postgres. They are skipped unless you point them at one:

    # PowerShell, with `docker compose up -d` running
    $env:FUNDRAG_TEST_DATABASE_URL = "postgresql://fundrag:<password>@localhost:5432/fundrag"
    uv run pytest tests/test_db.py -v

Each test works in its own throw-away *schema* (a separate namespace of tables inside
the database) that is deleted afterwards, so your real manifest table is never touched.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import psycopg
import pytest
from conftest import KID_LINES
from psycopg.conninfo import make_conninfo

from fundrag import config, db
from fundrag.api.store import FundStore
from fundrag.collect import manifest
from fundrag.collect.identify import inspect_pdf

TEST_URL = os.environ.get("FUNDRAG_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="set FUNDRAG_TEST_DATABASE_URL to run")


def test_columns_match_the_record_model():
    """If someone adds a field to ManifestRecord, the table must get it too."""
    assert db.COLUMNS == tuple(manifest.ManifestRecord.model_fields)


@pytest.fixture
def db_url(monkeypatch):
    """A connection string whose tables live in a fresh, empty schema."""
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    # search_path = which schema unqualified table names refer to
    url = make_conninfo(TEST_URL, options=f"-c search_path={schema}")
    monkeypatch.setattr(config, "DATABASE_URL", url)  # as if FUNDRAG_DATABASE_URL were set
    db.ensure_schema()
    yield url
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


def _record(doc_id: str = "0123456789abcdef", **changes) -> manifest.ManifestRecord:
    fields = {
        "doc_id": doc_id, "sha256": doc_id * 4, "isin": "IE00B4L5Y983",
        "isins_in_doc": ["IE00B4L5Y983"], "issuer": "iShares", "doc_type": "priips_kid",
        "doc_type_confidence": 1.0, "language": "en", "doc_date": date(2026, 9, 3),
        "doc_date_cued": True, "n_pages": 3, "title_guess": None,
        "source": "https://example.org/kid.pdf",
        "retrieved_at": datetime(2026, 10, 17, 18, 15, tzinfo=UTC),
        "path": "raw/ishares/IE00B4L5Y983/x.pdf", "problems": [],
    }  # fmt: skip
    return manifest.ManifestRecord(**(fields | changes))


@needs_db
def test_record_survives_the_round_trip(db_url):
    """What goes in comes out unchanged: dates, empty lists, None."""
    rec = _record(problems=["a problem"], isins_in_doc=[], doc_date=None)
    assert db.insert(rec.model_dump()) is True
    assert manifest.load() == [rec]


@needs_db
def test_database_refuses_duplicates(db_url):
    rec = _record()
    assert db.insert(rec.model_dump()) is True
    assert db.insert(rec.model_dump()) is False
    assert db.insert_many([rec.model_dump(), _record("fedcba9876543210").model_dump()]) == 1
    assert len(manifest.load()) == 2


@needs_db
def test_store_writes_to_postgres_when_configured(db_url, pdf_factory, tmp_path):
    pdf = pdf_factory("kid.pdf", KID_LINES)
    kw = {
        "isin": "IE00B4L5Y983", "issuer": "iShares", "language": "en", "source": "manual:kid.pdf",
        "inspection": inspect_pdf(pdf), "data_dir": tmp_path / "data",
    }  # fmt: skip

    record, status = manifest.store(pdf, **kw)
    assert status == "stored"
    assert manifest.store(pdf, **kw) == (None, "duplicate")
    assert not (tmp_path / "data" / "manifest.jsonl").exists()  # nothing went to a file

    # The API's store sees it without being told where the manifest is.
    assert FundStore().latest_kids()["IE00B4L5Y983"].doc_id == record.doc_id


@needs_db
def test_import_from_jsonl_is_repeatable(db_url, tmp_path):
    path = tmp_path / "manifest.jsonl"
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in (_record("1" * 16), _record("2" * 16))),
        encoding="utf-8",
    )
    records = manifest.load(path)  # explicit path: read the file, not the database
    assert db.insert_many(r.model_dump() for r in records) == 2
    assert db.insert_many(r.model_dump() for r in records) == 0
    assert {r.doc_id for r in manifest.load()} == {"1" * 16, "2" * 16}
