"""Checks on YOUR real KIDs in data/raw/. Skipped when no KIDs are stored.

The other tests use synthetic documents and run anywhere. These run the parser on
the real corpus, so a KID with an unusual layout shows up as a failing test with its
ISIN in the name, the moment you collect it.
"""

import pytest

from fundrag import config
from fundrag.collect import manifest
from fundrag.parse.kid import check_sections, extract_pages, split_sections

LATEST = manifest.latest_by_isin(manifest.load()) if config.MANIFEST_PATH.exists() else {}


@pytest.mark.skipif(not LATEST, reason="no KIDs collected yet")
@pytest.mark.parametrize("isin", sorted(LATEST))
def test_real_kid_parses_cleanly(isin):
    record = LATEST[isin]
    sections = split_sections(extract_pages(config.DATA_DIR / record.path))
    assert check_sections(sections) == []
    text = {s.key: s.text for s in sections}
    assert "out of 7" in text["risk_indicator"]
    assert "Recommended Holding Period" in text["holding_period"]
    assert "Total costs" in text["costs_over_time"]
