"""Tests for the rule-based extractor (extract/rules.py) and the scoring (evaluate.py)."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from fundrag import config
from fundrag.extract.evaluate import score
from fundrag.extract.rules import extract_rules
from fundrag.extract.schema import KidFields

# Short excerpts in the wording of a real iShares KID, one per section.
SECTIONS = {
    "product": 'iShares Core MSCI World UCITS ETF (the “Fund”), USD Accu (the "Share Class"), '
    "ISIN: IE00B4L5Y983, is authorised in Ireland",
    "objectives": "income on the Fund’s assets, which reflects the return of the MSCI World\n"
    "Index, the Fund’s benchmark index (Index).\nThe Share Class, via the Fund is passively\n"
    "managed. Your shares will be accumulating shares (i.e. income will be included).\n"
    "Your shares will be denominated in US Dollar, the Fund's base currency.",
    "risk_indicator": "We have classified this product as 4 out of 7, which is a medium risk",
    "performance_scenarios": (
        "Recommended holding period : 5 years Example Investment : USD 10,000\n"
        "Stress* What you might get back after costs 7,490 USD 3,850 USD\n"
        "Average return each year -25.1% \u201317.4%\n"  # en dash as minus sign
        "Unfavourable** What you might get back after costs 8,050 USD 11,960 USD\n"
        "Average return each year -19.5% 3.7%\n"
        "Moderate** What you might get back after costs 11,550 USD 17,800 USD\n"
        "Average return each year 15.5% 12.2%\n"
        "Favourable** What you might get back after costs 15,420 USD 21,200 USD\n"
        "Average return each year 54.2% 16.2%"
    ),
    "costs_over_time": "Total costs 20 USD 180 USD\nAnnual cost Impact (*) 0.2% 0.2%",
    "composition_of_costs": "Entry costs We do not charge an entry fee.1 -\n"
    "Exit costs We do not charge an exit fee.1 -\nManagement fees and 0.20% of the value of "
    "your investment\nTransaction costs 0.00% of the value\n"
    "Performance fees There is no performance fee for this product. -",
}  # fmt: skip


def _doc(sections: dict[str, str], isin: str = "IE00B4L5Y983"):
    """A stand-in for a ParsedDocument: the extractor only reads .isin and .sections."""
    return NS(isin=isin, sections=[NS(key=k, text=v) for k, v in sections.items()])


def test_every_field_is_read_from_the_template_wording():
    f = extract_rules(_doc(SECTIONS))
    assert f.model_dump() == {
        "isin": "IE00B4L5Y983", "share_class": "USD Accu", "distribution": "accumulating",
        "currency": "USD", "benchmark_index": "MSCI World Index", "management_style": "passive",
        "sri": 4, "rhp_years": 5, "example_amount": 10000,
        "stress_1y_amount": 7490, "stress_1y_return_pct": -25.1,
        "stress_rhp_amount": 3850, "stress_rhp_return_pct": -17.4,
        "unfavourable_1y_amount": 8050, "unfavourable_1y_return_pct": -19.5,
        "unfavourable_rhp_amount": 11960, "unfavourable_rhp_return_pct": 3.7,
        "moderate_1y_amount": 11550, "moderate_1y_return_pct": 15.5,
        "moderate_rhp_amount": 17800, "moderate_rhp_return_pct": 12.2,
        "favourable_1y_amount": 15420, "favourable_1y_return_pct": 54.2,
        "favourable_rhp_amount": 21200, "favourable_rhp_return_pct": 16.2,
        "entry_cost_pct": 0, "exit_cost_pct": 0, "ongoing_costs_pct": 0.2,
        "transaction_costs_pct": 0.0, "performance_fee_pct": 0,
        "total_costs_1y": 20, "total_costs_rhp": 180,
        "annual_cost_impact_1y_pct": 0.2, "annual_cost_impact_rhp_pct": 0.2,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("reflects the return of the MSCI World Index, the Fund’s benchmark index", "MSCI World Index"),
        ("reflects the return of the S&P Global Clean Energy Transition Index (the Index).",
         "S&P Global Clean Energy Transition Index"),
        ("reflects the return of the STOXX Global Automation & Robotics Index (Index).",
         "STOXX Global Automation & Robotics Index"),
        ("reflects the return of the ICE U.S. Treasury 7-10 Year Bond Index, the Funds benchmark index",
         "ICE U.S. Treasury 7-10 Year Bond Index"),
        ("reflects the return of the MSCI All Countries World Index Net USD, the Fund’s benchmark index",
         "MSCI All Countries World Index Net USD"),
    ],
)  # fmt: skip
def test_benchmark_wordings(sentence, expected):
    assert extract_rules(_doc({"objectives": sentence})).benchmark_index == expected


def test_hedged_share_class_currency_wins_over_the_fund_currency():
    text = ("The Fund's base currency is US Dollar. Shares for this Share Class are "
            "denominated in Euro. The performance may be affected.")  # fmt: skip
    assert extract_rules(_doc({"objectives": text})).currency == "EUR"


def test_what_is_not_stated_stays_empty():
    f = extract_rules(_doc({"objectives": "The Fund aims to achieve a return."}))
    assert f.management_style is None and f.sri is None and f.benchmark_index is None


def test_score_counts_wrong_and_missing_values():
    truth = {"A": KidFields(isin="A", sri=4, share_class="USD Accu"), "B": KidFields(isin="B", sri=2)}
    guess = {"A": KidFields(isin="A", sri=5, share_class="usd  accu")}  # B not extracted
    result = score(guess, truth)
    assert result.n_funds == 2 and result.missing_funds == ["B"]
    assert result.correct["share_class"] == 2  # case and spaces ignored; B: both empty
    assert result.correct["sri"] == 0
    assert ("A", "sri", 4, 5) in result.mistakes


@pytest.mark.skipif(
    not (config.DATA_DIR / "processed" / "fields").parent.exists(), reason="no parsed KIDs"
)
def test_rules_still_score_100_percent_on_the_real_answer_key():
    """Regression guard: run on your real KIDs and answer key (skipped in a fresh clone)."""
    from fundrag.extract.gold import load_gold
    from fundrag.extract.table import extract_all

    gold = load_gold()
    extracted = extract_all("rules")
    if not gold or not set(gold) <= set(extracted):
        pytest.skip("answer key or parsed KIDs missing")
    result = score(extracted, gold)
    assert result.mistakes == []
