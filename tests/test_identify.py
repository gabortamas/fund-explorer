from datetime import date

from conftest import KID_LINES, KIID_LINES

from fundrag.collect.identify import (
    classify,
    find_document_date,
    find_isins,
    inspect_pdf,
    isin_is_valid,
)


def test_isin_check_digit():
    assert isin_is_valid("IE00B4L5Y983")
    assert isin_is_valid("US0378331005")  # Apple, a well-known reference ISIN
    assert not isin_is_valid("IE00B4L5Y984")  # wrong check digit
    assert not isin_is_valid("IE00B4L5Y98")  # too short


def test_find_isins_ignores_lookalikes():
    text = "Share class IE00B4L5Y983, ref ABCDEFGHIJK1, again IE00B4L5Y983"
    assert find_isins(text) == ["IE00B4L5Y983"]


def test_classify_kid_vs_kiid():
    assert classify("\n".join(KID_LINES))[0] == "priips_kid"
    assert classify("\n".join(KIID_LINES))[0] == "ucits_kiid"
    assert classify("Factsheet as at 30 September 2026")[0] == "unknown"


def test_document_date_prefers_cued_date():
    text = "Fund launched 25 September 2009. This document is accurate as at 15 February 2026."
    assert find_document_date(text) == (date(2026, 2, 15), True)


def test_document_date_uncued_is_flagged():
    d, cued = find_document_date("Printed 03/04/2025")
    assert d == date(2025, 4, 3) and cued is False


def test_inspect_pdf_end_to_end(pdf_factory):
    result = inspect_pdf(pdf_factory("kid.pdf", KID_LINES))
    assert result.doc_type == "priips_kid"
    assert result.isins == ["IE00B4L5Y983"]
    assert result.doc_date == date(2026, 2, 15)
    assert result.problems == []


def test_inspect_pdf_flags_kiid(pdf_factory):
    result = inspect_pdf(pdf_factory("kiid.pdf", KIID_LINES))
    assert result.doc_type == "ucits_kiid"
    assert any("not recognised as a PRIIPs KID" in p for p in result.problems)
