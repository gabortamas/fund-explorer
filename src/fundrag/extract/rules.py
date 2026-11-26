"""Extractor 1: plain text patterns ("rules"). No AI, free, instant.

Every KID is written to a template prescribed by the PRIIPs regulation, and all KIDs
of one issuer use the same wording. So most fields can be read with a regular
expression: a pattern such as "classified this product as (\\d) out of 7", where the
part in brackets is the value we want.

The weakness of rules: they only know the wording they were written for. A KID from
another issuer, or a re-worded template, makes them miss. A missed field comes back
as None, never as a guess, and the evaluation counts it as wrong.

A note on regular expressions, as used below:
  \\d       one digit                 [\\d,]+   digits and commas, e.g. 11,960
  \\s*      any amount of space       (...)     the part we want to keep
  .+?      as little text as possible up to what follows
"""

from __future__ import annotations

import re

from fundrag.extract.schema import SCENARIOS, KidFields
from fundrag.parse.pipeline import ParsedDocument

CURRENCY_NAMES = {
    "us dollar": "USD", "euro": "EUR", "sterling": "GBP", "pound sterling": "GBP",
    "swiss franc": "CHF", "japanese yen": "JPY", "swedish krona": "SEK",
}  # fmt: skip
_MINUS = "[-–−]"  # hyphen, en dash and the real minus sign all mean "minus"
_PCT = rf"({_MINUS}?\d+(?:\.\d+)?)\s*%"  # e.g. -25.1%
_AMOUNT = r"(\d[\d,]*)\s+[A-Z]{3}"  # e.g. 11,960 USD


def _num(text: str) -> float:
    return float(re.sub(_MINUS, "-", text).replace(",", ""))


def _flat(text: str) -> str:
    """Join lines and squeeze spaces, so a phrase split over two lines still matches."""
    return re.sub(r"\s+", " ", text)


def _find(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def extract_rules(doc: ParsedDocument) -> KidFields:
    sections = {s.key: s.text for s in doc.sections}
    whole = "\n".join(sections.values())  # fallback: search the whole document
    flat = _flat(whole)
    out: dict = {"isin": doc.isin}

    # --- Product -------------------------------------------------------------------
    out["share_class"] = _find(r"\(the [“\"]Fund[”\"]\),\s*(.+?)\s*\(the [“\"]Share Class", flat)
    out["distribution"] = _find(r"Your shares will be (accumulating|distributing) shares", flat)
    if out["distribution"]:
        out["distribution"] = out["distribution"].lower()
    # Share-class currency. A hedged class says "Shares for this Share Class are
    # denominated in Euro"; others "Your shares will be denominated in US Dollar".
    name = _find(r"Share Class are denominated in ([A-Za-z ]+?)[.,]", flat) or _find(
        r"shares will be denominated in ([A-Za-z ]+?)[.,]", flat
    )
    out["currency"] = CURRENCY_NAMES.get((name or "").lower()) or _find(
        r"Example Investment\s*:\s*([A-Z]{3})", flat  # fallback: the scenarios' currency
    )
    # "...reflects the return of the MSCI World Index, the Fund's benchmark index" and
    # variants: "...Index (the Index)", "...Index (Index)", "the Funds benchmark index".
    out["benchmark_index"] = _find(
        r"reflects the return of the (.+?)"
        r"(?:, the Funds?[’']?s? benchmark index| \((?:the )?Index\))",
        flat,
    )
    style = _find(r"\b(passively|actively) managed", flat)
    out["management_style"] = {"passively": "passive", "actively": "active"}.get(
        (style or "").lower()
    )

    # --- Risk ----------------------------------------------------------------------
    sri = _find(r"classified this product as (\d) out of 7", flat)
    out["sri"] = int(sri) if sri else None
    rhp = _find(r"Recommended holding period\s*:?\s*(\d+(?:\.\d+)?)\s*years?", flat)
    out["rhp_years"] = float(rhp) if rhp else None

    # --- Performance scenarios ----------------------------------------------------
    example = _find(r"Example Investment\s*:\s*[A-Z]{3}\s*(\d[\d,]*)", flat)
    out["example_amount"] = _num(example) if example else None
    lines = [ln.strip() for ln in sections.get("performance_scenarios", whole).splitlines()]
    for i, line in enumerate(lines):
        for name in SCENARIOS:
            if not re.match(rf"{name}\**\s+What you might get back", line, re.IGNORECASE):
                continue
            amounts = re.findall(_AMOUNT, line)
            if len(amounts) >= 2:  # first column = after 1 year, last = after the RHP
                out[f"{name}_1y_amount"] = _num(amounts[0])
                out[f"{name}_rhp_amount"] = _num(amounts[-1])
            following = lines[i + 1] if i + 1 < len(lines) else ""
            if following.lower().startswith("average return each year"):
                pcts = re.findall(_PCT, following)
                if len(pcts) >= 2:
                    out[f"{name}_1y_return_pct"] = _num(pcts[0])
                    out[f"{name}_rhp_return_pct"] = _num(pcts[-1])

    # --- Costs -----------------------------------------------------------------------
    for field, label in (("entry_cost_pct", "entry"), ("exit_cost_pct", "exit")):
        if re.search(rf"We do not charge an? {label} fee", flat, re.IGNORECASE):
            out[field] = 0.0
        else:
            value = _find(rf"{label} costs\s+{_PCT}", flat)
            out[field] = _num(value) if value else None
    value = _find(rf"Management fees and\s+{_PCT}", flat)
    out["ongoing_costs_pct"] = _num(value) if value else None
    value = _find(rf"Transaction costs\s+{_PCT}", flat)
    out["transaction_costs_pct"] = _num(value) if value else None
    if re.search(r"There is no performance fee", flat, re.IGNORECASE):
        out["performance_fee_pct"] = 0.0
    else:
        value = _find(rf"Performance fees\s+{_PCT}", flat)
        out["performance_fee_pct"] = _num(value) if value else None

    totals = re.search(rf"Total costs\s+{_AMOUNT}\s+{_AMOUNT}", flat)
    if totals:
        out["total_costs_1y"], out["total_costs_rhp"] = _num(totals[1]), _num(totals[2])
    impact = re.search(rf"Annual cost Impact\s*\(\*\)\s*{_PCT}\s*{_PCT}", flat, re.IGNORECASE)
    if impact:
        out["annual_cost_impact_1y_pct"] = _num(impact[1])
        out["annual_cost_impact_rhp_pct"] = _num(impact[2])

    return KidFields(**{k: v for k, v in out.items() if v is not None})
