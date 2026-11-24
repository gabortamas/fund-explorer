"""Tests for the collector agent, without internet and without spending money.

Two fakes stand in for the outside world:
  * a fake *web* (httpx.MockTransport): a few addresses that return a KID, a UK KIID,
    a web page or "404 Not Found";
  * a fake *Claude* that replays answers we wrote in advance, like an actor with a
    script. That lets us test our side of the loop: are tool calls executed, do the
    verdicts go back to Claude, does the loop stop at the right moment?
Whether the real Claude finds the real links is checked by running `fundrag find`
on the funds whose KIDs we already collected by hand (see the README).
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import httpx
import pytest
from conftest import KID_LINES, KIID_LINES, make_pdf

from fundrag.collect import agent, manifest
from fundrag.collect.universe import Product
from fundrag.collect.verify import check_url

WORLD = Product(isin="IE00B4L5Y983", name="Core MSCI World", issuer="iShares")
BASE = "https://www.blackrock.com/gls-download/literature/kiid/"
GOOD = BASE + "eu-priips-ishares-core-msci-world-ucits-etf-ie00b4l5y983-en.pdf"
KIID = BASE + "ishares-core-msci-world-ucits-etf-kiid-gb.pdf"
PAGE = "https://www.blackrock.com/lu/individual/en/products/251882/fund"
ERROR_PAGE = "https://www.blackrock.com/corporate/page-not-found"


@pytest.fixture
def web(tmp_path):
    """An httpx client whose 'internet' consists of three addresses."""
    kid = make_pdf(tmp_path / "kid.pdf", KID_LINES).read_bytes()
    kiid = make_pdf(tmp_path / "kiid.pdf", KIID_LINES).read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == GOOD:
            return httpx.Response(200, content=kid, headers={"content-type": "application/pdf"})
        if url == KIID:
            return httpx.Response(200, content=kiid, headers={"content-type": "application/pdf"})
        if "/gls-download/" in url:  # like the real server: unknown names are redirected
            return httpx.Response(302, headers={"location": ERROR_PAGE})  # to an error page
        if url in (PAGE, ERROR_PAGE):
            return httpx.Response(200, text="<html>fund page</html>",
                                  headers={"content-type": "text/html"})  # fmt: skip
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yield client


@pytest.fixture
def store_kw(tmp_path):
    data = tmp_path / "data"
    return {"data_dir": data, "manifest_path": data / "manifest.jsonl"}


# ----------------------------------------------------------------- verify.check_url


def test_right_kid_is_accepted_and_stored(web, store_kw):
    v = check_url(GOOD, WORLD, web, **store_kw)
    assert v.accepted and v.status == "stored"
    assert manifest.load(store_kw["manifest_path"])[0].source == GOOD
    assert check_url(GOOD, WORLD, web, **store_kw).status == "duplicate"


def test_dry_run_stores_nothing(web, store_kw):
    v = check_url(GOOD, WORLD, web, store=False, **store_kw)
    assert v.accepted and v.status == "dry-run"
    assert manifest.load(store_kw["manifest_path"]) == []


@pytest.mark.parametrize(
    ("url", "product", "reason"),
    [
        ("https://www.justetf.com/kid.pdf", WORLD, "issuer's own website"),
        (BASE + "does-not-exist-en.pdf", WORLD, "file name is probably wrong"),
        ("https://www.ishares.com/nothing-here.pdf", WORLD, "HTTP 404"),
        (PAGE, WORLD, "not a PDF"),
        (KIID, WORLD, "looks like: ucits_kiid"),
        (GOOD, Product(isin="IE00B5BMR087", name="S&P 500", issuer="iShares"), "not for"),
    ],
)
def test_wrong_candidates_are_rejected_with_a_reason(web, store_kw, url, product, reason):
    v = check_url(url, product, web, **store_kw)
    assert not v.accepted
    assert reason in v.reason
    assert "reason" in json.loads(v.for_agent())  # the agent gets to read why


def test_country_site_link_is_rewritten_before_download(web, store_kw):
    country = GOOD.replace("www.blackrock.com/gls-download", "www.ishares.com/de/privatanleger/de")
    assert check_url(country, WORLD, web, **store_kw).accepted


# ----------------------------------------------------------------- the agent loop


def _response(*blocks, stop="tool_use"):
    usage = NS(input_tokens=1000, output_tokens=100,
               server_tool_use=NS(web_search_requests=0, web_fetch_requests=0))  # fmt: skip
    return NS(content=list(blocks), stop_reason=stop, usage=usage)


def _try(url, block_id):
    return NS(type="tool_use", id=block_id, name="try_kid_url", input={"url": url})


class FakeClaude:
    """Replays prepared answers and remembers what it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self  # so that client.messages.create(...) works

    def create(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs["messages"], default=str)))
        return self.responses.pop(0)


def test_agent_learns_from_a_rejection_and_stops_when_accepted(web, store_kw):
    claude = FakeClaude(
        _response(
            NS(type="text", text="Searching for the KID."),
            NS(
                type="server_tool_use", id="s1", name="web_search",
                input={"query": "IE00B4L5Y983 eu-priips"},
            ),
            NS(type="web_search_tool_result", tool_use_id="s1", content=[]),
            _try(BASE + "wrong-name-ie00b4l5y983-en.pdf", "t1"),
        ),
        _response(_try(GOOD, "t2")),
        _response(NS(type="text", text="never reached"), stop="end_turn"),
    )
    result = agent.find_kid(WORLD, client=claude, http=web, **store_kw)

    assert result.found and result.verdict.url == GOOD
    assert [v.accepted for v in result.attempts] == [False, True]
    assert len(claude.calls) == 2  # stopped right after the acceptance
    # The rejection reason was sent back to Claude in the second request:
    assert "probably wrong" in json.dumps(claude.calls[1][-1])
    assert [s.kind for s in result.steps][:2] == ["think", "search"]
    assert len(manifest.load(store_kw["manifest_path"])) == 1


def test_agent_that_gives_up_reports_why(web, store_kw):
    claude = FakeClaude(
        _response(NS(type="text", text="No English KID exists for this ISIN."), stop="end_turn")
    )
    result = agent.find_kid(WORLD, client=claude, http=web, **store_kw)
    assert not result.found
    assert result.final_text.startswith("No English KID")


def test_attempt_limit_is_enforced(web, store_kw):
    bad = [_response(_try(BASE + f"guess-{i}.pdf", f"t{i}")) for i in range(agent.MAX_ATTEMPTS + 1)]
    claude = FakeClaude(*bad, _response(NS(type="text", text="giving up"), stop="end_turn"))
    result = agent.find_kid(WORLD, client=claude, http=web, **store_kw)
    assert len(result.attempts) == agent.MAX_ATTEMPTS
    assert "Limit" in json.dumps(claude.calls[-1][-1])


def test_run_record_is_saved(web, store_kw, tmp_path):
    claude = FakeClaude(_response(_try(GOOD, "t1")))
    result = agent.find_kid(WORLD, client=claude, http=web, store=False, **store_kw)
    path = agent.save_run(result, tmp_path / "runs")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["found"] is True and saved["attempts"][0]["url"] == GOOD


def test_only_the_first_good_candidate_is_checked(web, store_kw):
    claude = FakeClaude(_response(_try(GOOD, "t1"), _try(GOOD.replace("www.", ""), "t2")))
    result = agent.find_kid(WORLD, client=claude, http=web, **store_kw)
    assert result.found and len(result.attempts) == 1


def test_prompt_does_not_contain_the_evaluation_funds():
    for isin in ("IE00B4L5Y983", "IE00B4WXJJ64", "IE00B1XNHC34"):
        assert isin.lower() not in agent.SYSTEM_PROMPT.lower()
