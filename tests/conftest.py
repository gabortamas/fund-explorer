"""Test helpers: build tiny text PDFs without any extra dependency."""

from __future__ import annotations

from pathlib import Path

import pytest


def make_pdf(path: Path, lines: list[str]) -> Path:
    """Write a minimal one-page PDF whose text pypdf can extract."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 10 Tf 40 800 Td 14 TL\n"
    content += "".join(f"({esc(line)}) Tj T*\n" for line in lines) + "ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(out)
    return path


KID_LINES = [
    "Key Information Document",
    "iShares Core MSCI World UCITS ETF USD (Acc)",
    "ISIN: IE00B4L5Y983",
    "This document is accurate as at 15 February 2026.",
    "What is this product?",
    "What are the risks and what could I get in return?",
    "What are the costs?",
    "How long should I hold it and can I take money out early?",
]

KIID_LINES = [
    "Key Investor Information",
    "iShares Core MSCI World UCITS ETF  ISIN: IE00B4L5Y983",
    "Objectives and investment policy",
    "Risk and reward profile",
    "Ongoing charges 0.20%",
    "This key investor information is accurate as at 10 February 2026.",
]


@pytest.fixture
def pdf_factory(tmp_path):
    def _make(name: str, lines: list[str]) -> Path:
        return make_pdf(tmp_path / name, lines)

    return _make
