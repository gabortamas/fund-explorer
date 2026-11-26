"""Tests for the step-3 field list and its consistency rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fundrag.extract.gold import load_gold
from fundrag.extract.schema import COLUMNS, KidFields, consistency_problems

# The values of the Core MSCI World KID dated 2026-09-03, read off the PDF.
WORLD = {
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
    "entry_cost_pct": 0, "exit_cost_pct": 0, "ongoing_costs_pct": 0.20,
    "transaction_costs_pct": 0.00, "performance_fee_pct": 0,
    "total_costs_1y": 20, "total_costs_rhp": 180,
    "annual_cost_impact_1y_pct": 0.2, "annual_cost_impact_rhp_pct": 0.2,
}  # fmt: skip


def test_a_correct_kid_breaks_no_rule():
    assert consistency_problems(KidFields(**WORLD)) == []


def test_swapped_scenarios_are_caught():
    bad = WORLD | {"moderate_rhp_amount": 21200, "favourable_rhp_amount": 17800}
    assert any("not ordered" in p for p in consistency_problems(KidFields(**bad)))


def test_return_that_does_not_match_the_amount_is_caught():
    bad = WORLD | {"stress_1y_return_pct": 25.1}  # sign lost
    assert any("stress 1y" in p for p in consistency_problems(KidFields(**bad)))


def test_costs_that_do_not_add_up_are_caught():
    bad = WORLD | {"total_costs_1y": 200}  # 0.20 % read as 2.0 %
    assert any("total costs" in p for p in consistency_problems(KidFields(**bad)))


@pytest.mark.parametrize("change", [{"sri": 8}, {"currency": "dollar"}, {"distribution": "acc"}])
def test_impossible_values_are_refused(change):
    with pytest.raises(ValidationError):
        KidFields(**(WORLD | change))


def test_answer_key_has_the_same_columns():
    gold = Path(__file__).parents[1] / "data" / "gold" / "kid_fields_gold.csv"
    first_line = gold.read_text(encoding="utf-8-sig").splitlines()[0]
    header = first_line.split(";" if first_line.count(";") > first_line.count(",") else ",")
    assert tuple(header[: len(COLUMNS)]) == COLUMNS


@pytest.mark.parametrize("sep", [",", ";"])
def test_answer_key_is_read_in_both_excel_shapes(tmp_path, sep):
    values = [str(WORLD[c]) for c in COLUMNS]
    if sep == ";":  # German/Hungarian Excel: semicolons and decimal commas
        values = [v.replace(".", ",") for v in values]
    path = tmp_path / "gold.csv"
    path.write_text(sep.join(COLUMNS) + "\n" + sep.join(values) + "\n", encoding="utf-8")
    assert load_gold(path)["IE00B4L5Y983"] == KidFields(**WORLD)
