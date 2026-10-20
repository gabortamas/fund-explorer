"""Split a PRIIPs KID into its prescribed sections.

Why split by headings rather than by font size or layout?
The PRIIPs regulation prescribes the section titles of every KID, word for word
("What is this product?", "What are the costs?", ...). Matching those titles is
deterministic, works for every issuer, and fails loudly: if a required section is
missing, the parse report says so, instead of silently producing odd chunks.

The output keeps two levels:
  * level 1: the nine prescribed sections (always present in a valid KID)
  * level 2: well-known sub-parts (Objectives, Risk Indicator, Performance
    Scenarios, Costs over time, Composition of Costs, ...). Optional, because
    issuers word these slightly differently.

Each section remembers the pages it spans, so later answers can cite "KID, p. 2".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
from pydantic import BaseModel


@dataclass(frozen=True)
class SectionSpec:
    key: str
    pattern: re.Pattern[str]  # matched at the start of a line; group "title" is the heading
    level: int  # 1 = prescribed section, 2 = sub-part
    required: bool = False


def _spec(key: str, regex: str, level: int, required: bool = False) -> SectionSpec:
    return SectionSpec(key, re.compile(regex, re.IGNORECASE), level, required)


# Order matters for level-1 sections: the regulation fixes it, and we check it.
SECTION_SPECS: list[SectionSpec] = [
    _spec("purpose", r"^(?P<title>Purpose)\s*$", 1, True),
    _spec("product", r"^(?P<title>Product)\s*$", 1, True),
    _spec("what_is", r"^(?P<title>What is this product\?)", 1, True),
    _spec("objectives", r"^(?P<title>Objectives)\s*$", 2),
    _spec("intended_investor", r"^(?P<title>Intended retail investor)\s*:", 2),
    _spec("insurance_benefits", r"^(?P<title>Insurance benefits)\s*:", 2),
    _spec("risks", r"^(?P<title>What are the risks and what could I get in return\?)", 1, True),
    _spec("risk_indicator", r"^(?P<title>(Summary )?Risk Indicator)\s*$", 2),
    _spec("performance_scenarios", r"^(?P<title>Performance Scenarios)\s*$", 2),
    _spec("default", r"^(?P<title>What happens if .+? (is )?unable to pay out\?)", 1, True),
    _spec("costs", r"^(?P<title>What are the costs\?)", 1, True),
    _spec("costs_over_time", r"^(?P<title>Costs over time)\s*:?", 2),
    _spec("composition_of_costs", r"^(?P<title>Composition of costs)\s*$", 2),
    _spec(
        "holding_period",
        r"^(?P<title>How long should I hold it and can I take (my )?money out early\?)",
        1,
        True,
    ),
    _spec("complaints", r"^(?P<title>How can I complain\?)", 1, True),
    _spec("other_info", r"^(?P<title>Other relevant information)", 1, True),
]
REQUIRED_KEYS = [s.key for s in SECTION_SPECS if s.required]
_PAGE_NUMBER = re.compile(r"^\s*\d{1,2}\s*$")


class Section(BaseModel):
    key: str  # e.g. "costs"; "preamble" for text before the first heading
    level: int
    parent: str | None  # level-2 sections point to their level-1 section
    title: str  # the heading exactly as printed
    page_start: int  # 1-based
    page_end: int
    text: str  # body text, line breaks preserved (tables stay one row per line)


# ---------------------------------------------------------------------------


def extract_pages(path: Path) -> list[str]:
    """Plain text per page, in reading order.

    pdfplumber keeps each table row on one line ("Total costs 20 USD 180 USD"),
    which is exactly what the extraction step needs later.
    """
    with pdfplumber.open(str(path)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _drop_page_number(lines: list[str]) -> list[str]:
    """Remove the footer page number ("1", "2", ...) at the end of a page."""
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _PAGE_NUMBER.match(lines[-1]):
        lines.pop()
    return lines


def _match_heading(line: str) -> tuple[SectionSpec, str, str] | None:
    """If the line starts a known section, return (spec, title, rest of the line)."""
    stripped = line.strip()
    for spec in SECTION_SPECS:
        m = spec.pattern.match(stripped)
        if m:
            rest = stripped[m.end() :].lstrip(" :")
            return spec, m.group("title"), rest
    return None


def split_sections(pages: list[str]) -> list[Section]:
    """Walk through the lines of all pages and cut at every recognised heading."""
    sections: list[Section] = []
    current: dict | None = None
    current_l1: str | None = None

    def close() -> None:
        if current is not None:
            text = "\n".join(current.pop("lines")).strip()
            sections.append(Section(**current, text=text))

    current = {"key": "preamble", "level": 0, "parent": None, "title": "",
               "page_start": 1, "page_end": 1, "lines": []}  # fmt: skip
    for page_no, page_text in enumerate(pages, start=1):
        for line in _drop_page_number(page_text.splitlines()):
            hit = _match_heading(line)
            if hit is None:
                current["lines"].append(line)
                current["page_end"] = page_no  # the page of the section's last line
                continue
            spec, title, rest = hit
            close()
            if spec.level == 1:
                current_l1 = spec.key
            current = {
                "key": spec.key,
                "level": spec.level,
                "parent": current_l1 if spec.level == 2 else None,
                "title": title,
                "page_start": page_no,
                "page_end": page_no,
                "lines": [rest] if rest else [],
            }
    close()

    # The preamble is usually just "Key Information Document"; drop it if empty.
    return [s for s in sections if not (s.key == "preamble" and not s.text)]


def check_sections(sections: list[Section]) -> list[str]:
    """Problems that would make this parse unreliable. Empty list = clean."""
    problems = []
    found = [s.key for s in sections if s.level == 1]
    missing = [k for k in REQUIRED_KEYS if k not in found]
    if missing:
        problems.append(f"missing required sections: {missing}")
    dupes = sorted({k for k in found if found.count(k) > 1})
    if dupes:
        problems.append(f"sections found more than once: {dupes}")
    first_seen = [k for i, k in enumerate(found) if k in REQUIRED_KEYS and k not in found[:i]]
    if first_seen != [k for k in REQUIRED_KEYS if k in first_seen]:
        problems.append(f"sections out of the prescribed order: {first_seen}")
    # A level-1 heading followed directly by sub-parts is fine ("What are the risks?"
    # then "Risk Indicator"); it is only empty if it has no text and no sub-parts.
    parents = {s.parent for s in sections if s.level == 2}
    empty = [s.key for s in sections if s.level == 1 and not s.text and s.key not in parents]
    if empty:
        problems.append(f"empty sections: {empty}")
    return problems
