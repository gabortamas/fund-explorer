"""Work out what a PDF actually is, without trusting its file name or URL.

Every stored document passes through `inspect_pdf`. It answers three questions:
  * Is this really a PRIIPs KID (and not a UK KIID, factsheet or prospectus)?
  * Which ISINs does it mention? (checksum-validated, so random strings don't count)
  * When was it produced? (KIDs are re-issued at least yearly, so the date matters)

The checks are deliberately simple text heuristics. They will be replaced by the
structured extraction step later; here they only guard the corpus against junk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pdfplumber

# ---------------------------------------------------------------------------
# ISINs
# ---------------------------------------------------------------------------

ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")


def isin_is_valid(isin: str) -> bool:
    """Validate an ISIN's check digit (ISO 6166: letters to numbers, then Luhn)."""
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin[:-1])
    total = 0
    # Luhn over the expanded digits, doubling from the rightmost digit leftwards.
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - total % 10) % 10 == int(isin[-1])


def find_isins(text: str) -> list[str]:
    """All distinct valid ISINs in the text, in order of first appearance."""
    seen: dict[str, None] = {}
    for match in ISIN_RE.finditer(text):
        if isin_is_valid(match.group(1)):
            seen.setdefault(match.group(1), None)
    return list(seen)


# ---------------------------------------------------------------------------
# Document type
# ---------------------------------------------------------------------------

# Section headings the PRIIPs regulation prescribes for every KID (English versions).
PRIIPS_KID_MARKERS = [
    "key information document",
    "what is this product",
    "what are the risks",
    "what are the costs",
    "how long should i hold it",
]
# The old UCITS KIID (still used in the UK) has different headings.
UCITS_KIID_MARKERS = [
    "key investor information",
    "objectives and investment policy",
    "risk and reward profile",
    "ongoing charges",
]


def classify(text: str) -> tuple[str, float]:
    """Return (doc_type, confidence). doc_type is 'priips_kid', 'ucits_kiid' or 'unknown'."""
    low = re.sub(r"\s+", " ", text.lower())
    kid_hits = sum(m in low for m in PRIIPS_KID_MARKERS)
    kiid_hits = sum(m in low for m in UCITS_KIID_MARKERS)
    if kid_hits >= 4 and kid_hits / len(PRIIPS_KID_MARKERS) > kiid_hits / len(UCITS_KIID_MARKERS):
        return "priips_kid", kid_hits / len(PRIIPS_KID_MARKERS)
    if kiid_hits >= 3:
        return "ucits_kiid", kiid_hits / len(UCITS_KIID_MARKERS)
    return "unknown", 0.0


# ---------------------------------------------------------------------------
# Document date
# ---------------------------------------------------------------------------

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
)
_DATE_PATTERNS = [
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS})\s+(\d{{4}})\b", re.IGNORECASE), "dmy_text"),
    (re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE), "mdy_text"),
    (re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b"), "dmy_num"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
]
# Words that tend to sit right before the production date of a KID.
_DATE_CUES = re.compile(
    r"(accurate as at|valid as at|last (?:updated|revised)|produced|production date|"
    r"dated|as of|as at|date of)",
    re.IGNORECASE,
)


def _parse(kind: str, groups: tuple[str, ...]) -> date | None:
    try:
        if kind == "dmy_text":
            return datetime.strptime(f"{groups[0]} {groups[1]} {groups[2]}", "%d %B %Y").date()
        if kind == "mdy_text":
            return datetime.strptime(f"{groups[1]} {groups[0]} {groups[2]}", "%d %B %Y").date()
        if kind == "dmy_num":
            return date(int(groups[2]), int(groups[1]), int(groups[0]))
        if kind == "iso":
            return date(int(groups[0]), int(groups[1]), int(groups[2]))
    except ValueError:
        return None
    return None


def find_document_date(text: str) -> tuple[date | None, bool]:
    """Best guess at the KID's production date.

    Returns (date, cued). `cued` is True when the date sat right after a phrase such
    as "accurate as at"; uncued guesses should be checked by hand.
    """
    candidates: list[tuple[int, date, bool]] = []
    for pattern, kind in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            d = _parse(kind, m.groups())
            if d is None or not (2017 <= d.year <= date.today().year + 1):
                continue
            window = text[max(0, m.start() - 60) : m.start()]
            candidates.append((m.start(), d, bool(_DATE_CUES.search(window))))
    if not candidates:
        return None, False
    cued = [c for c in candidates if c[2]]
    pool = cued or candidates
    # The most recent cued date is the usual answer (older dates refer to fund launch etc.).
    best = max(pool, key=lambda c: c[1])
    return best[1], best[2]


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------


@dataclass
class PdfInspection:
    doc_type: str
    doc_type_confidence: float
    isins: list[str] = field(default_factory=list)
    doc_date: date | None = None
    doc_date_cued: bool = False
    n_pages: int = 0
    title_guess: str | None = None
    problems: list[str] = field(default_factory=list)


def extract_text(path: Path, max_pages: int = 4) -> tuple[str, int]:
    with pdfplumber.open(str(path)) as pdf:
        pages = pdf.pages[:max_pages]
        return "\n".join((p.extract_text() or "") for p in pages), len(pdf.pages)


# KIDs name the product as: <name> (the "Fund"), <share class> (the "Share Class"), ...
_FUND_NAME = re.compile(r"^(?P<name>.+?)\s*\(the [“\"]Fund[”\"]\)")


def guess_title(text: str) -> str | None:
    for line in text.splitlines():
        m = _FUND_NAME.match(line.strip())
        if m:
            return m.group("name")
    return next((ln.strip() for ln in text.splitlines() if "ucits etf" in ln.lower()), None)


def inspect_text(text: str, n_pages: int = 0) -> PdfInspection:
    doc_type, conf = classify(text)
    isins = find_isins(text)
    doc_date, cued = find_document_date(text)
    title = guess_title(text)

    problems = []
    if doc_type != "priips_kid":
        problems.append(f"not recognised as a PRIIPs KID (looks like: {doc_type})")
    if not isins:
        problems.append("no valid ISIN found")
    if doc_date is None:
        problems.append("no document date found")
    elif not cued:
        problems.append("document date is a guess; check it by hand")
    if text.strip() == "":
        problems.append("no extractable text (scanned PDF?)")

    return PdfInspection(doc_type, conf, isins, doc_date, cued, n_pages, title, problems)


def inspect_pdf(path: Path) -> PdfInspection:
    try:
        text, n_pages = extract_text(path)
    except Exception as exc:  # noqa: BLE001 - PDF libs raise many types; report, don't crash
        return PdfInspection("unknown", 0.0, problems=[f"could not read PDF: {exc}"])
    return inspect_text(text, n_pages)
