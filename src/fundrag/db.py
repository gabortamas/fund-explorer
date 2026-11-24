"""The manifest as a Postgres table.

Why a database instead of the JSONL file?
  * Several processes can write at the same time (parallel Airflow tasks, several
    containers). Postgres makes each write all-or-nothing and keeps them apart; two
    processes appending to one text file can interleave and corrupt it.
  * Every container can reach the database over the network; a file on your laptop
    is only visible to containers you explicitly attach it to.
  * The rules live in the table itself: a column is a date, `sha256` must be unique,
    and so on. A bad record is refused instead of being written.

A few words of SQL, as used below:
  * A *table* is like a CSV file whose columns have fixed names and types.
  * `CREATE TABLE IF NOT EXISTS` creates it once and does nothing afterwards.
  * `INSERT ... ON CONFLICT DO NOTHING` adds a row unless a row with the same unique
    value (here doc_id or sha256) is already there. That is the duplicate check,
    done by the database in one step, so two processes cannot both "win".
  * `%s` in a query is a placeholder. psycopg sends the values separately from the
    query text, so a value can never be mistaken for SQL ("SQL injection").

This module works with plain dicts. `collect/manifest.py` turns them into
`ManifestRecord` objects; keeping the conversion there avoids a circular import.
"""

from __future__ import annotations

from collections.abc import Iterable

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from fundrag import config

# Same fields, same order as ManifestRecord (tests/test_db.py checks that they match).
COLUMNS = (
    "doc_id", "sha256", "isin", "isins_in_doc", "issuer", "doc_type",
    "doc_type_confidence", "language", "doc_date", "doc_date_cued", "n_pages",
    "title_guess", "source", "retrieved_at", "path", "problems",
)  # fmt: skip

SCHEMA = """
CREATE TABLE IF NOT EXISTS manifest (
    doc_id              text PRIMARY KEY,         -- first 16 hex chars of the SHA-256
    sha256              text NOT NULL UNIQUE,     -- the same file is never stored twice
    isin                text NOT NULL,            -- the ISIN it is filed under
    isins_in_doc        text[] NOT NULL,          -- text[] = a list of texts
    issuer              text NOT NULL,
    doc_type            text NOT NULL,            -- priips_kid | ucits_kiid | unknown
    doc_type_confidence double precision NOT NULL,
    language            text NOT NULL,
    doc_date            date,                     -- may be empty (NULL): undated
    doc_date_cued       boolean NOT NULL,
    n_pages             integer NOT NULL,
    title_guess         text,
    source              text NOT NULL,            -- URL, or manual:<file name>
    retrieved_at        timestamptz NOT NULL,     -- timestamp with time zone
    path                text NOT NULL,            -- relative to the data folder
    problems            text[] NOT NULL
);
-- An index is like the index of a book: finds all rows of one ISIN without reading
-- the whole table. Not needed for 3 rows, useful for 3000.
CREATE INDEX IF NOT EXISTS manifest_isin_idx ON manifest (isin);
"""

_COLS = ", ".join(COLUMNS)
_PLACEHOLDERS = ", ".join(["%s"] * len(COLUMNS))
_INSERT = f"INSERT INTO manifest ({_COLS}) VALUES ({_PLACEHOLDERS}) ON CONFLICT DO NOTHING"


def connect(url: str | None = None) -> psycopg.Connection:
    """Open a connection. Use it in a `with` block: at the end of the block the work is
    committed (saved for good) and the connection closed; if an error happens inside
    the block, everything done in it is rolled back instead."""
    url = url or config.DATABASE_URL
    if not url:
        raise RuntimeError(
            "No database configured. Set FUNDRAG_DATABASE_URL, e.g. "
            "postgresql://fundrag:<password>@localhost:5432/fundrag"
        )
    return psycopg.connect(url, row_factory=dict_row)  # dict_row: rows come back as dicts


def describe(url: str | None = None) -> str:
    """Where the database is, without the password (safe to print)."""
    i = conninfo_to_dict(url or config.DATABASE_URL or "")
    where = f"{i.get('host', '?')}:{i.get('port', 5432)}/{i.get('dbname', '?')}"
    return f"Postgres {i.get('user', '?')}@{where}"


def ensure_schema(url: str | None = None) -> None:
    """Create the manifest table if it does not exist yet. Safe to run any number of times."""
    with connect(url) as conn:
        conn.execute(SCHEMA)


def insert(record: dict, url: str | None = None) -> bool:
    """Add one record. True if it was added, False if it was already there."""
    return insert_many([record], url) == 1


def insert_many(records: Iterable[dict], url: str | None = None) -> int:
    """Add several records in one transaction. Returns how many were new."""
    added = 0
    with connect(url) as conn:
        for r in records:
            cur = conn.execute(_INSERT, [r[c] for c in COLUMNS])
            added += cur.rowcount  # 1 if inserted, 0 if ON CONFLICT skipped it
    return added


def load_all(url: str | None = None) -> list[dict]:
    """Every record, oldest download first (the same order as the JSONL file)."""
    with connect(url) as conn:
        query = f"SELECT {_COLS} FROM manifest ORDER BY retrieved_at, doc_id"
        return conn.execute(query).fetchall()


def known_hashes(url: str | None = None) -> set[str]:
    with connect(url) as conn:
        return {row["sha256"] for row in conn.execute("SELECT sha256 FROM manifest")}
