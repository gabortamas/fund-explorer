"""The fields we extract from every KID: one fixed list, the same for every fund.

Why fixed columns instead of text? Comparing funds ("which is cheapest?"), filtering
("risk class at most 3") and checking the assistant's answers later ("did it quote the
ongoing costs correctly?") all need numbers in known places.

The list is deliberately flat (no nested objects): one KID becomes one row, so the
hand-made answer key (data/gold/kid_fields_gold.csv), the extractor's output and the
evaluation all share the same columns, and comparing them is a column-by-column job.

Conventions, so that the answer key and the extractors agree:
  * Percentages are numbers in percent: 0.20 means 0.20 %, -25.1 means -25.1 %.
  * "We do not charge an entry fee" / "There is no performance fee" -> 0.0.
  * Money amounts are numbers in the KID's currency, without thousands separators:
    "7,490 USD" -> 7490.
  * "rhp" = recommended holding period; "1y" = "if you exit after 1 year".
  * None means "the KID does not state this". It is not the same as 0.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCENARIOS = ("stress", "unfavourable", "moderate", "favourable")  # worst to best


class KidFields(BaseModel):
    isin: str

    # --- Product ("Product" and "What is this product?") --------------------------
    share_class: str | None = Field(None, description='e.g. "USD Accu", "EUR Hedged (Acc)"')
    distribution: Literal["accumulating", "distributing"] | None = None
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$", description="share class currency")
    benchmark_index: str | None = Field(None, description='e.g. "MSCI World Index"')
    management_style: Literal["passive", "active"] | None = None

    # --- Risk ("Risk Indicator", "How long should I hold it?") --------------------
    sri: int | None = Field(None, ge=1, le=7, description="summary risk indicator, 1-7")
    rhp_years: float | None = Field(None, gt=0, description="recommended holding period")

    # --- Performance scenarios -----------------------------------------------------
    example_amount: float | None = Field(None, gt=0, description="usually 10000")
    stress_1y_amount: float | None = None
    stress_1y_return_pct: float | None = None
    stress_rhp_amount: float | None = None
    stress_rhp_return_pct: float | None = None
    unfavourable_1y_amount: float | None = None
    unfavourable_1y_return_pct: float | None = None
    unfavourable_rhp_amount: float | None = None
    unfavourable_rhp_return_pct: float | None = None
    moderate_1y_amount: float | None = None
    moderate_1y_return_pct: float | None = None
    moderate_rhp_amount: float | None = None
    moderate_rhp_return_pct: float | None = None
    favourable_1y_amount: float | None = None
    favourable_1y_return_pct: float | None = None
    favourable_rhp_amount: float | None = None
    favourable_rhp_return_pct: float | None = None

    # --- Costs ("Costs over time", "Composition of costs") ------------------------
    entry_cost_pct: float | None = Field(None, ge=0)
    exit_cost_pct: float | None = Field(None, ge=0)
    ongoing_costs_pct: float | None = Field(None, ge=0, description="management fees etc.")
    transaction_costs_pct: float | None = Field(None, ge=0)
    performance_fee_pct: float | None = Field(None, ge=0)
    total_costs_1y: float | None = Field(None, ge=0, description="money amount")
    total_costs_rhp: float | None = Field(None, ge=0, description="money amount")
    annual_cost_impact_1y_pct: float | None = Field(None, ge=0)
    annual_cost_impact_rhp_pct: float | None = Field(None, ge=0)


COLUMNS: tuple[str, ...] = tuple(KidFields.model_fields)


def consistency_problems(f: KidFields) -> list[str]:
    """Rules that every correct KID satisfies. A broken rule means a wrong value
    somewhere (a misread number, a swapped column), even without an answer key."""
    problems = []

    # 1. Scenarios are ordered from worst to best.
    for period in ("1y", "rhp"):
        amounts = [getattr(f, f"{s}_{period}_amount") for s in SCENARIOS]
        if None not in amounts and amounts != sorted(amounts):
            problems.append(f"{period} scenario amounts are not ordered stress <= ... <= favourable")

    # 2. After one year, an amount below the investment means a negative return.
    if f.example_amount:
        for s in SCENARIOS:
            amount, ret = getattr(f, f"{s}_1y_amount"), getattr(f, f"{s}_1y_return_pct")
            if amount is not None and ret is not None:
                implied = (amount / f.example_amount - 1) * 100
                if abs(implied - ret) > 0.6:  # values are rounded to 0.1 % and whole units
                    problems.append(
                        f"{s} 1y: {amount:g} back on {f.example_amount:g} is {implied:.1f} %, "
                        f"but the return says {ret:g} %"
                    )

    # 3. First-year costs = investment x (ongoing + transaction costs), at 0 % return.
    parts = (f.example_amount, f.ongoing_costs_pct, f.transaction_costs_pct, f.total_costs_1y)
    if None not in parts:
        expected = f.example_amount * (f.ongoing_costs_pct + f.transaction_costs_pct) / 100
        if abs(expected - f.total_costs_1y) > max(2.0, 0.1 * expected):
            problems.append(
                f"total costs after 1 year are {f.total_costs_1y:g}, but ongoing + transaction "
                f"costs imply about {expected:.0f}"
            )
    return problems
