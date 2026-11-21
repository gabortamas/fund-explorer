"""The shape of every answer the API gives.

Each class below is a "response model". FastAPI uses them for three things:
  * it checks that what we return really has these fields and types,
  * it turns the Python object into JSON,
  * it lists them in the automatic documentation at /docs, so a reader of the API
    knows exactly what they will get back without reading this code.

Rule of thumb: a list endpoint returns small summaries; a detail endpoint returns
more. Big text (a KID section) only comes back when someone asks for that section.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class FundSummary(BaseModel):
    """One row of the fund list."""

    isin: str = Field(examples=["IE00B4L5Y983"])
    name: str
    issuer: str
    asset_class: str
    kid_available: bool = Field(description="True if a PRIIPs KID is stored for this fund")
    kid_date: date | None = Field(description="Date printed on the latest KID")


class FundPage(BaseModel):
    """A page of the fund list, plus what you need to ask for the next page."""

    items: list[FundSummary]
    total: int = Field(description="How many funds match the filters, over all pages")
    limit: int
    offset: int


class KidInfo(BaseModel):
    """Where the latest KID came from, and whether it has been parsed yet."""

    doc_id: str = Field(description="Content-hash id; every later answer cites this")
    doc_date: date | None
    source: str = Field(description="URL it was downloaded from, or manual:<file name>")
    retrieved_at: datetime
    parsed: bool
    problems: list[str] = Field(description="Anything the checks flagged; empty = clean")


class FundDetail(BaseModel):
    isin: str
    name: str
    issuer: str
    asset_class: str
    language: str
    notes: str
    kid: KidInfo | None = Field(description="null if no KID has been collected yet")


class SectionSummary(BaseModel):
    """A KID section without its text: enough to decide which one to fetch."""

    key: str = Field(examples=["costs_over_time"])
    title: str
    level: int = Field(description="1 = section the regulation prescribes, 2 = sub-part")
    parent: str | None
    page_start: int
    page_end: int
    n_chars: int


class KidOverview(BaseModel):
    isin: str
    doc_id: str
    doc_date: date | None
    source: str
    parser_version: str
    n_pages: int
    sections: list[SectionSummary]


class SectionOut(SectionSummary):
    """One section with its full text, plus what a citation needs (fund, document, pages)."""

    isin: str
    doc_id: str
    text: str
