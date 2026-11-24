"""Check one candidate KID link: download it, look inside, and store it if it is right.

This is plain code, no AI. The collector agent (collect/agent.py) can propose as many
links as it likes, but a document only enters the corpus if it passes the checks here:

  1. The link is on the issuer's own website (provenance: we only trust the source).
  2. The download really is a PDF.
  3. `identify.inspect_pdf` recognises it as an English PRIIPs KID. (Its markers are the
     English section titles, so a UK-style KIID or a German KID is rejected too.)
  4. The KID names the ISIN we are looking for.

The answer (a `Verdict`) goes back to the agent as text. A rejection says *why*, so
the agent can try something better: "HTTP 404" means a wrong address, "looks like
ucits_kiid" means it landed on the UK site, and so on.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx

from fundrag.collect import manifest
from fundrag.collect.identify import inspect_pdf
from fundrag.collect.sources import normalize_url
from fundrag.collect.universe import Product

# The websites each issuer publishes its own documents on. Links elsewhere (brokers,
# data vendors) are refused even if the PDF is genuine: we want the primary source.
ISSUER_DOMAINS: dict[str, tuple[str, ...]] = {
    "ishares": ("ishares.com", "blackrock.com"),
}
MAX_PDF_BYTES = 10 * 1024 * 1024  # a KID is 3 pages; anything this big is something else


@dataclass
class Verdict:
    url: str
    accepted: bool
    reason: str = ""  # why it was rejected (empty when accepted)
    status: str = ""  # when accepted: stored | duplicate | dry-run
    doc_type: str = ""
    isins_found: list[str] = field(default_factory=list)
    doc_date: date | None = None
    title: str | None = None
    record: manifest.ManifestRecord | None = None

    def for_agent(self) -> str:
        """The short JSON text the agent reads as the tool's answer."""
        out = {"url": self.url, "accepted": self.accepted}
        if self.accepted:
            out |= {"status": self.status, "doc_date": str(self.doc_date), "title": self.title}
        else:
            out |= {"reason": self.reason}
            if self.isins_found:
                out["isins_found"] = self.isins_found
        return json.dumps(out, ensure_ascii=False)


def on_issuer_site(url: str, issuer: str) -> bool:
    domains = ISSUER_DOMAINS.get(issuer.lower())
    if not domains:
        return True  # issuer we have no list for yet: don't block
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def check_url(
    url: str,
    product: Product,
    client: httpx.Client,
    *,
    store: bool = True,
    **store_kwargs,
) -> Verdict:
    """Download `url` and decide whether it is the KID of `product`.

    store=True: an accepted KID is stored in the corpus with its manifest record.
    store=False ("dry run"): only check, store nothing.
    """
    url = normalize_url(url)

    def reject(reason: str, **extra) -> Verdict:
        return Verdict(url=url, accepted=False, reason=reason, **extra)

    if not url.lower().startswith(("http://", "https://")):
        return reject("not a web address")
    if not on_issuer_site(url, product.issuer):
        allowed = ", ".join(ISSUER_DOMAINS[product.issuer.lower()])
        return reject(f"not on the issuer's own website (allowed: {allowed})")

    try:
        resp = client.get(url, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return reject(f"HTTP {exc.response.status_code}: no file at this address")
    except httpx.HTTPError as exc:
        return reject(f"download failed: {exc.__class__.__name__}")

    if len(resp.content) > MAX_PDF_BYTES:
        return reject("file is far too large to be a KID")
    if not resp.content.startswith(b"%PDF"):
        ctype = resp.headers.get("content-type", "?")
        if "/gls-download/" in url:  # the address we asked for, not where it redirected to
            # BlackRock's file server answers a wrong file name by redirecting to an HTML
            # error page, not with "404 Not Found".
            return reject("no file at this address (the file server sent a web page instead): "
                          "the file name is probably wrong")
        return reject(f"not a PDF but {ctype}; this is probably a web page, not the file itself")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_file = Path(tmp) / "candidate.pdf"
        tmp_file.write_bytes(resp.content)
        insp = inspect_pdf(tmp_file)
        found = {
            "doc_type": insp.doc_type,
            "isins_found": insp.isins,
            "doc_date": insp.doc_date,
            "title": insp.title_guess,
        }
        if insp.doc_type != "priips_kid":
            return reject(
                f"not an English PRIIPs KID (looks like: {insp.doc_type}). UK-style KIIDs and "
                "KIDs in other languages are rejected",
                **found,
            )
        if product.isin not in insp.isins:
            return reject(f"a KID, but not for {product.isin}", **found)

        if not store:
            return Verdict(url=url, accepted=True, status="dry-run", **found)
        record, status = manifest.store(
            tmp_file,
            isin=product.isin,
            issuer=product.issuer,
            language=product.language,
            source=str(resp.url),
            inspection=insp,
            **store_kwargs,
        )
    return Verdict(url=url, accepted=True, status=status, record=record, **found)
