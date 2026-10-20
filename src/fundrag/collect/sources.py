"""Two ways a document enters the corpus.

1. `download_url`: fetch a direct PDF link listed in the universe CSV.
2. `ingest_inbox`: register PDFs you saved by hand into data/inbox/.

Both end in the same place, `manifest.store`, after `identify.inspect_pdf` has
checked what the file really is. That way the corpus does not depend on how a
particular issuer's website happens to work this month.
"""

from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from fundrag import config
from fundrag.collect import manifest
from fundrag.collect.identify import inspect_pdf
from fundrag.collect.universe import Product


@dataclass
class Outcome:
    isin: str
    status: str  # stored | duplicate | failed | skipped | unmatched
    detail: str = ""
    record: manifest.ManifestRecord | None = None


# Issuer adapters: tiny rules that turn a link you found into one that works without
# cookies. The first collector layer: plain code, no LLM, for cases a rule can solve.
_ISHARES_LITERATURE = re.compile(
    r"^https?://(?:www\.)?ishares\.com/.+?/(?P<path>literature/(?:kiid|fact-sheet)/.+\.pdf)$",
    re.IGNORECASE,
)


def normalize_url(url: str) -> str:
    """Rewrite iShares country-site links to BlackRock's direct file server.

    ishares.com/<country>/<investor type>/<lang>/literature/kiid/x.pdf sits behind an
    investor-type pop-up that needs a browser cookie; the same file is served without
    one at blackrock.com/gls-download/literature/kiid/x.pdf.
    """
    m = _ISHARES_LITERATURE.match(url.strip())
    if m:
        return f"https://www.blackrock.com/gls-download/{m.group('path')}"
    return url.strip()


def download_url(product: Product, client: httpx.Client, **store_kwargs) -> Outcome:
    if not product.kid_url:
        return Outcome(product.isin, "skipped", "no kid_url in universe")
    try:
        resp = client.get(normalize_url(product.kid_url), follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return Outcome(product.isin, "failed", f"HTTP error: {exc}")
    if not resp.content.startswith(b"%PDF"):
        ctype = resp.headers.get("content-type", "?")
        return Outcome(
            product.isin,
            "failed",
            f"response is not a PDF (content-type {ctype}); the site may need a cookie or a "
            "disclaimer click. Save the file by hand into the inbox instead.",
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_file = Path(tmp) / "doc.pdf"
        tmp_file.write_bytes(resp.content)
        inspection = inspect_pdf(tmp_file)
        record, status = manifest.store(
            tmp_file,
            isin=product.isin,
            issuer=product.issuer,
            language=product.language,
            source=str(resp.url),
            inspection=inspection,
            **store_kwargs,
        )
    return Outcome(product.isin, status, "; ".join(inspection.problems), record)


def download_all(products: list[Product], delay: float | None = None, **store_kwargs) -> list[Outcome]:
    delay = config.REQUEST_DELAY_SECONDS if delay is None else delay
    outcomes = []
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/pdf,*/*"}
    with httpx.Client(headers=headers, timeout=30.0) as client:
        for i, product in enumerate(products):
            if i and product.kid_url:
                time.sleep(delay)
            outcomes.append(download_url(product, client, **store_kwargs))
    return outcomes


def ingest_inbox(
    products: list[Product],
    inbox: Path | None = None,
    remove_after: bool = True,
    **store_kwargs,
) -> list[Outcome]:
    """Register every PDF in the inbox.

    The file is matched to the universe by the ISINs found *inside* the PDF, not by
    its file name. A KID that mentions exactly one ISIN from the universe is filed
    under it; anything else is reported and left in the inbox for you to look at.
    """
    inbox = inbox or config.INBOX_DIR
    by_isin = {p.isin: p for p in products}
    outcomes = []
    for pdf in sorted(inbox.glob("*.pdf")):
        inspection = inspect_pdf(pdf)
        matches = [i for i in inspection.isins if i in by_isin]
        if len(matches) != 1:
            why = "no ISIN from the universe" if not matches else f"several: {matches}"
            outcomes.append(
                Outcome(pdf.name, "unmatched", f"{why}; ISINs found: {inspection.isins}")
            )
            continue
        product = by_isin[matches[0]]
        record, status = manifest.store(
            pdf,
            isin=product.isin,
            issuer=product.issuer,
            language=product.language,
            source=f"manual:{pdf.name}",
            inspection=inspection,
            **store_kwargs,
        )
        if remove_after:
            pdf.unlink()
        outcomes.append(Outcome(product.isin, status, "; ".join(inspection.problems), record))
    return outcomes
