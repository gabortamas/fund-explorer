"""Tests for the REST API.

FastAPI's TestClient sends real HTTP requests to the app without starting a server.
The API is pointed at a small temporary data folder (two funds, one parsed KID), so
these tests do not depend on what is in your real data/ folder.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from test_parse import PAGE_1, PAGE_2, PAGE_3

from fundrag.api.app import app
from fundrag.api.store import FundStore, get_store
from fundrag.parse.kid import check_sections, split_sections
from fundrag.parse.pipeline import PARSER_VERSION, ParsedDocument, output_paths

WORLD = "IE00B4L5Y983"  # has a parsed KID in the test data
BONDS = "IE00B4WXJJ64"  # in the universe, but no KID collected
NOT_IN_UNIVERSE = "IE00B5BMR087"  # valid ISIN, not in the test universe
BAD_CHECK_DIGIT = "IE00B4L5Y984"
DOC_ID = "abcdef0123456789"


@pytest.fixture
def client(tmp_path):
    universe = tmp_path / "universe.csv"
    universe.write_text(
        "isin,name,issuer,asset_class,language,kid_url,notes\n"
        f"{WORLD},iShares Core MSCI World,iShares,equity,en,,global\n"
        f"{BONDS},iShares Core EUR Govt Bond,iShares,bond,en,,\n",
        encoding="utf-8",
    )
    record = {
        "doc_id": DOC_ID, "sha256": DOC_ID * 4, "isin": WORLD, "isins_in_doc": [WORLD],
        "issuer": "iShares", "doc_type": "priips_kid", "doc_type_confidence": 1.0,
        "language": "en", "doc_date": "2026-09-03", "doc_date_cued": True, "n_pages": 3,
        "title_guess": None, "source": "https://example.org/kid.pdf",
        "retrieved_at": "2026-10-17T18:15:22Z", "path": f"raw/ishares/{WORLD}/x.pdf",
        "problems": [],
    }  # fmt: skip
    manifest_path = tmp_path / "manifest.jsonl"
    manifest_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    sections = split_sections([PAGE_1, PAGE_2, PAGE_3])
    doc = ParsedDocument(
        doc_id=DOC_ID, isin=WORLD, issuer="iShares", doc_type="priips_kid", language="en",
        doc_date=date(2026, 9, 3), source=record["source"], raw_path=record["path"],
        parser_version=PARSER_VERSION, parsed_at=datetime.now(UTC), n_pages=3,
        n_chars=sum(len(p) for p in (PAGE_1, PAGE_2, PAGE_3)), sections=sections,
        problems=check_sections(sections),
    )  # fmt: skip
    processed = tmp_path / "processed"
    json_path, _ = output_paths(DOC_ID, processed)
    json_path.parent.mkdir(parents=True)
    json_path.write_text(doc.model_dump_json(), encoding="utf-8")

    store = FundStore(universe_path=universe, manifest_path=manifest_path, processed_dir=processed)
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_funds_shows_kid_status(client):
    body = client.get("/v1/funds").json()
    assert body["total"] == 2
    by_isin = {f["isin"]: f for f in body["items"]}
    assert by_isin[WORLD]["kid_available"] is True
    assert by_isin[WORLD]["kid_date"] == "2026-09-03"
    assert by_isin[BONDS]["kid_available"] is False


def test_list_funds_filters_and_pages(client):
    assert [f["isin"] for f in client.get("/v1/funds?asset_class=bond").json()["items"]] == [BONDS]
    assert client.get("/v1/funds?kid_available=true").json()["total"] == 1
    page = client.get("/v1/funds?limit=1&offset=1").json()
    assert page["total"] == 2 and len(page["items"]) == 1


def test_list_funds_rejects_bad_paging(client):
    assert client.get("/v1/funds?limit=0").status_code == 422
    assert client.get("/v1/funds?limit=1000").status_code == 422


def test_get_fund(client):
    body = client.get(f"/v1/funds/{WORLD}").json()
    assert body["name"] == "iShares Core MSCI World"
    assert body["kid"]["doc_id"] == DOC_ID
    assert body["kid"]["parsed"] is True


def test_get_fund_accepts_lower_case_isin(client):
    assert client.get(f"/v1/funds/{WORLD.lower()}").status_code == 200


def test_fund_without_kid_has_null_kid(client):
    assert client.get(f"/v1/funds/{BONDS}").json()["kid"] is None


def test_status_codes_for_wrong_isins(client):
    assert client.get(f"/v1/funds/{BAD_CHECK_DIGIT}").status_code == 422
    assert client.get(f"/v1/funds/{NOT_IN_UNIVERSE}").status_code == 404
    assert client.get(f"/v1/funds/{BONDS}/kid").status_code == 404


def test_kid_overview_lists_sections_without_text(client):
    body = client.get(f"/v1/funds/{WORLD}/kid").json()
    keys = [s["key"] for s in body["sections"]]
    assert "costs_over_time" in keys
    assert all("text" not in s for s in body["sections"])


def test_kid_section_has_text_and_citation_info(client):
    body = client.get(f"/v1/funds/{WORLD}/kid/sections/costs_over_time").json()
    assert "Total costs 20 USD 180 USD" in body["text"]
    assert body["doc_id"] == DOC_ID
    assert body["page_start"] == 3


def test_unknown_section_lists_the_available_ones(client):
    r = client.get(f"/v1/funds/{WORLD}/kid/sections/nonsense")
    assert r.status_code == 404
    assert "costs_over_time" in r.json()["detail"]
