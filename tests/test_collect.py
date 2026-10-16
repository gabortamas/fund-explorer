from pathlib import Path

from conftest import KID_LINES, KIID_LINES, make_pdf

from fundrag.collect import manifest
from fundrag.collect.sources import ingest_inbox
from fundrag.collect.universe import Product, load_universe

UNIVERSE = [Product(isin="IE00B4L5Y983", name="Core MSCI World", issuer="iShares")]


def _dirs(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    data = tmp_path / "data"
    return inbox, {"data_dir": data, "manifest_path": data / "manifest.jsonl"}


def test_inbox_stores_kid_with_provenance(tmp_path):
    inbox, kw = _dirs(tmp_path)
    make_pdf(inbox / "whatever-name.pdf", KID_LINES)

    [outcome] = ingest_inbox(UNIVERSE, inbox=inbox, **kw)

    assert outcome.status == "stored"
    rec = outcome.record
    assert rec.isin == "IE00B4L5Y983" and rec.doc_type == "priips_kid"
    assert rec.source == "manual:whatever-name.pdf"
    assert (kw["data_dir"] / rec.path).exists()
    assert rec.path.startswith("raw/ishares/IE00B4L5Y983/priips_kid_en_2026-02-15_")
    assert not (inbox / "whatever-name.pdf").exists()  # moved out of the inbox


def test_same_file_twice_is_a_duplicate(tmp_path):
    inbox, kw = _dirs(tmp_path)
    make_pdf(inbox / "a.pdf", KID_LINES)
    ingest_inbox(UNIVERSE, inbox=inbox, **kw)
    make_pdf(inbox / "a-again.pdf", KID_LINES)

    [outcome] = ingest_inbox(UNIVERSE, inbox=inbox, **kw)

    assert outcome.status == "duplicate"
    assert len(manifest.load(kw["manifest_path"])) == 1


def test_reissued_kid_becomes_new_latest_version(tmp_path):
    inbox, kw = _dirs(tmp_path)
    make_pdf(inbox / "old.pdf", KID_LINES)
    ingest_inbox(UNIVERSE, inbox=inbox, **kw)
    newer = [ln.replace("15 February 2026", "12 August 2026") for ln in KID_LINES]
    make_pdf(inbox / "new.pdf", newer)
    ingest_inbox(UNIVERSE, inbox=inbox, **kw)

    records = manifest.load(kw["manifest_path"])
    latest = manifest.latest_by_isin(records)

    assert len(records) == 2
    assert str(latest["IE00B4L5Y983"].doc_date) == "2026-08-12"


def test_unknown_isin_stays_in_inbox(tmp_path):
    inbox, kw = _dirs(tmp_path)
    other = [ln.replace("IE00B4L5Y983", "US0378331005") for ln in KID_LINES]
    make_pdf(inbox / "other.pdf", other)

    [outcome] = ingest_inbox(UNIVERSE, inbox=inbox, **kw)

    assert outcome.status == "unmatched"
    assert (inbox / "other.pdf").exists()


def test_kiid_is_stored_but_flagged(tmp_path):
    inbox, kw = _dirs(tmp_path)
    make_pdf(inbox / "uk.pdf", KIID_LINES)

    [outcome] = ingest_inbox(UNIVERSE, inbox=inbox, **kw)

    assert outcome.record.doc_type == "ucits_kiid"
    assert manifest.latest_by_isin(manifest.load(kw["manifest_path"])) == {}


def test_repo_universe_loads():
    path = Path(__file__).parents[1] / "data" / "universe" / "ishares_v1.csv"
    products = load_universe(path)
    assert len(products) >= 20
    assert all(p.issuer == "iShares" for p in products)
