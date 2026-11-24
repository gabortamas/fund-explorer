"""The endpoints: which web address runs which code.

Start it with `fundrag serve`, then open http://127.0.0.1:8000/docs in a browser.
That page is generated from this file and lets you try every endpoint.

REST in one paragraph: everything the API knows about is a *resource* with its own
address (a fund is /v1/funds/IE00B4L5Y983, its KID is .../kid). The HTTP *method*
says what to do with it; this first version only uses GET ("give me"). The *status
code* says how it went: 200 OK, 404 Not Found (no such fund), 422 Unprocessable
(the request itself is malformed, e.g. an ISIN with a wrong check digit).

The "/v1" in the addresses is the API version. If the shape of an answer ever has to
change in a way that would break existing users, the new shape goes under /v2 and
/v1 keeps working.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query

from fundrag import __version__, config, db
from fundrag.api.schemas import (
    FundDetail,
    FundPage,
    FundSummary,
    KidInfo,
    KidOverview,
    SectionOut,
    SectionSummary,
)
from fundrag.api.store import FundStore, get_store
from fundrag.collect.identify import isin_is_valid
from fundrag.collect.universe import Product
from fundrag.parse.pipeline import ParsedDocument


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Code before `yield` runs once when the server starts, code after it once at shutdown.

    If a database is configured, make sure the manifest table exists, so that a fresh,
    empty Postgres (the first `docker compose up`) works without a manual step.
    """
    if config.DATABASE_URL:
        db.ensure_schema()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="fund explorer API",
    version=__version__,
    description=(
        "Read-only access to the fund universe and the parsed PRIIPs KIDs. "
        "Informational only; nothing here is investment advice."
    ),
)

# `Depends(get_store)` means: before running the endpoint, call get_store() and pass
# the result in. Tests swap get_store for one that points at test data.
Store = Annotated[FundStore, Depends(get_store)]
IsinParam = Annotated[str, Path(description="ISIN, e.g. IE00B4L5Y983", examples=["IE00B4L5Y983"])]


# ---------------------------------------------------------------------------
# Small helpers shared by several endpoints
# ---------------------------------------------------------------------------


def _clean_isin(isin: str) -> str:
    """Upper-case the ISIN and reject it (422) if its check digit is wrong."""
    isin = isin.strip().upper()
    if not isin_is_valid(isin):
        raise HTTPException(422, f"'{isin}' is not a valid ISIN (wrong format or check digit)")
    return isin


def _product_or_404(store: FundStore, isin: str) -> Product:
    product = store.product(isin)
    if product is None:
        raise HTTPException(404, f"{isin} is not in the fund universe")
    return product


def _parsed_kid_or_404(store: FundStore, isin: str) -> ParsedDocument:
    _product_or_404(store, isin)
    record = store.latest_kids().get(isin)
    if record is None:
        raise HTTPException(404, f"no KID has been collected for {isin} yet")
    doc = store.parsed_kid(record.doc_id)
    if doc is None:
        raise HTTPException(404, f"the KID of {isin} is stored but not parsed yet; run `fundrag parse`")
    return doc


def _section_summary(s) -> dict:
    return {
        "key": s.key,
        "title": s.title,
        "level": s.level,
        "parent": s.parent,
        "page_start": s.page_start,
        "page_end": s.page_end,
        "n_chars": len(s.text),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Is the service up? Kubernetes will call this every few seconds in stage E."""
    return {"status": "ok", "version": __version__}


@app.get("/v1/funds", response_model=FundPage, tags=["funds"])
def list_funds(
    store: Store,
    asset_class: Annotated[str | None, Query(description="e.g. equity, bond")] = None,
    kid_available: Annotated[bool | None, Query(description="only funds with/without a KID")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="page size")] = 20,
    offset: Annotated[int, Query(ge=0, description="how many to skip")] = 0,
):
    """List the funds in the universe, optionally filtered, one page at a time."""
    kids = store.latest_kids()
    rows = [
        FundSummary(
            isin=p.isin,
            name=p.name,
            issuer=p.issuer,
            asset_class=p.asset_class,
            kid_available=p.isin in kids,
            kid_date=kids[p.isin].doc_date if p.isin in kids else None,
        )
        for p in store.products()
    ]
    if asset_class:
        rows = [r for r in rows if r.asset_class.lower() == asset_class.lower()]
    if kid_available is not None:
        rows = [r for r in rows if r.kid_available == kid_available]
    return FundPage(items=rows[offset : offset + limit], total=len(rows), limit=limit, offset=offset)


@app.get("/v1/funds/{isin}", response_model=FundDetail, tags=["funds"])
def get_fund(isin: IsinParam, store: Store):
    """One fund: what the universe says about it, and where its latest KID came from."""
    isin = _clean_isin(isin)
    p = _product_or_404(store, isin)
    record = store.latest_kids().get(isin)
    kid = None
    if record is not None:
        kid = KidInfo(
            doc_id=record.doc_id,
            doc_date=record.doc_date,
            source=record.source,
            retrieved_at=record.retrieved_at,
            parsed=store.is_parsed(record.doc_id),
            problems=record.problems,
        )
    return FundDetail(
        isin=p.isin,
        name=p.name,
        issuer=p.issuer,
        asset_class=p.asset_class,
        language=p.language,
        notes=p.notes,
        kid=kid,
    )


@app.get("/v1/funds/{isin}/kid", response_model=KidOverview, tags=["kid"])
def get_kid(isin: IsinParam, store: Store):
    """The table of contents of a fund's latest KID (sections without their text)."""
    doc = _parsed_kid_or_404(store, _clean_isin(isin))
    return KidOverview(
        isin=doc.isin,
        doc_id=doc.doc_id,
        doc_date=doc.doc_date,
        source=doc.source,
        parser_version=doc.parser_version,
        n_pages=doc.n_pages,
        sections=[SectionSummary(**_section_summary(s)) for s in doc.sections],
    )


@app.get("/v1/funds/{isin}/kid/sections/{key}", response_model=SectionOut, tags=["kid"])
def get_kid_section(
    isin: IsinParam,
    key: Annotated[str, Path(description="section key from the /kid answer", examples=["costs"])],
    store: Store,
):
    """One section of the KID with its full text and pages, ready to cite."""
    doc = _parsed_kid_or_404(store, _clean_isin(isin))
    section = doc.section(key)
    if section is None:
        keys = ", ".join(s.key for s in doc.sections)
        raise HTTPException(404, f"no section '{key}' in this KID. Available: {keys}")
    return SectionOut(**_section_summary(section), isin=doc.isin, doc_id=doc.doc_id, text=section.text)
