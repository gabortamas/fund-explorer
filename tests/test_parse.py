"""Section splitting, tested on text shaped like a real KID (no PDF needed)."""

from conftest import make_pdf

from fundrag.parse.kid import REQUIRED_KEYS, check_sections, extract_pages, split_sections

PAGE_1 = """Key Information Document
Purpose
This document provides you with key information about this investment product.
Product
iShares Example UCITS ETF (the "Fund"), USD Acc (the "Share Class"), ISIN: IE00B4L5Y983
What is this product?
Type: The Fund is a sub-fund of iShares III plc.
Objectives
The Fund aims to track the Example Index.
Intended retail investor: The Fund is intended for retail investors.
1"""

PAGE_2 = """What are the risks and what could I get in return?
Risk Indicator
We have classified this product as 4 out of 7, which is a medium risk class.
Performance Scenarios
Moderate** What you might get back after costs 11,550 USD 17,800 USD
What happens if BlackRock Asset Management Ireland Limited is unable to pay out?
The assets of the Fund are held in safekeeping by its depositary.
2"""

PAGE_3 = """What are the costs?
Costs over time: The tables show the amounts taken from your investment.
Total costs 20 USD 180 USD
Composition of Costs
Management fees and 0.20% of the value of your investment per year. 20 USD
How long should I hold it and can I take money out early? Recommended Holding Period: 5 years
The recommended holding period has been calculated in line with the investment strategy.
How can I complain?
Write to the Investor Services Team.
Other relevant information
The latest version of this document can be obtained free of charge.
3"""


def _sections():
    return {s.key: s for s in split_sections([PAGE_1, PAGE_2, PAGE_3])}


def test_all_required_sections_found_and_clean():
    sections = split_sections([PAGE_1, PAGE_2, PAGE_3])
    assert [s.key for s in sections if s.level == 1] == REQUIRED_KEYS
    assert check_sections(sections) == []


def test_sub_sections_know_their_parent():
    s = _sections()
    assert s["objectives"].parent == "what_is"
    assert s["performance_scenarios"].parent == "risks"
    assert s["composition_of_costs"].parent == "costs"


def test_text_after_an_inline_heading_is_kept():
    s = _sections()
    assert s["holding_period"].text.startswith("Recommended Holding Period: 5 years")
    assert s["intended_investor"].text == "The Fund is intended for retail investors."
    assert s["costs_over_time"].text.splitlines()[1] == "Total costs 20 USD 180 USD"


def test_pages_and_page_numbers():
    s = _sections()
    assert (s["purpose"].page_start, s["purpose"].page_end) == (1, 1)
    assert (s["risks"].page_start, s["default"].page_end) == (2, 2)
    assert s["other_info"].page_end == 3
    assert not any(line.strip() in {"1", "2", "3"} for x in s.values() for line in x.text.splitlines())


def test_missing_section_is_reported():
    sections = split_sections([PAGE_1, PAGE_2])  # page 3 lost
    problems = check_sections(sections)
    assert problems and "costs" in problems[0] and "complaints" in problems[0]


def test_section_out_of_order_is_reported():
    sections = split_sections([PAGE_3, PAGE_1, PAGE_2])
    assert any("order" in p for p in check_sections(sections))


def test_end_to_end_from_pdf(tmp_path):
    lines = (PAGE_1 + "\n" + PAGE_2 + "\n" + PAGE_3).splitlines()
    pdf = make_pdf(tmp_path / "kid.pdf", [ln for ln in lines if ln.strip() not in {"1", "2", "3"}])
    sections = split_sections(extract_pages(pdf))
    assert check_sections(sections) == []
