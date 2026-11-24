"""The collector agent: ISIN in, verified KID out.

What makes this an "agent" rather than a script: we do not tell it *how* to find the
document. We give it a goal, three tools and a budget, and it decides step by step
what to do next, based on what the previous step returned.

The loop, in plain words:
  1. Send Claude the task, the tools it may use, and the conversation so far.
  2. Claude answers. The answer may contain tool calls.
       * web_search and web_fetch run at Anthropic ("server tools"): their results are
         already inside the answer when it arrives.
       * try_kid_url runs here, on your laptop ("client tool"): we run
         `verify.check_url` and send the verdict back in the next message.
  3. Repeat until a KID is accepted, Claude says it is done, or the budget runs out.

Only `try_kid_url` can put a document into the corpus, and it only accepts a file
that passes the same checks as everything else (see verify.py). So the agent can be
wrong about where to look, but it cannot get a wrong document stored.

Everything the agent does is recorded as a list of steps (`AgentResult.steps`) and
saved to data/agent_runs/, so you can read afterwards how it searched.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import httpx

from fundrag import config
from fundrag.collect.sources import normalize_url
from fundrag.collect.universe import Product
from fundrag.collect.verify import ISSUER_DOMAINS, Verdict, check_url

DEFAULT_MODEL = os.environ.get("FUNDRAG_AGENT_MODEL", "claude-sonnet-5-5")

# The budget for one fund. Each limit stops a confused agent from running up costs.
MAX_REQUESTS = 15  # calls to the Claude API
MAX_SEARCHES = 5  # web searches ($10 per 1000 searches, i.e. 1 cent each)
MAX_FETCHES = 5  # web pages read
MAX_ATTEMPTS = 6  # candidate links checked with try_kid_url

# Keep the funds of the evaluation set (the three collected by hand) OUT of these
# examples: an example address in the prompt would hand the agent the answer.
SYSTEM_PROMPT = """\
You find the official English-language EU PRIIPs Key Information Document (KID) of one \
fund share class, identified by its ISIN, on the issuer's own website.

Useful facts about iShares (BlackRock):
- KIDs are PDF files whose address contains /literature/kiid/ and whose file name has \
the form eu-priips-<fund name>-<share class>-<isin in lower case>-<language>.pdf, for \
example eu-priips-ishares-ultrashort-bond-ucits-etf-eur-acc-ie000rhyor04-en.pdf. Some \
funds with a single share class have no share-class part.
- The fund-name part follows the issuer's current official name, which can differ from \
the name you are given (funds get renamed).
- If you find the right file in another language (ending in -de.pdf, -lv.pdf, ...), the \
English version has the same name ending in -en.pdf.
- The UK website serves UK-style KIIDs, which are not PRIIPs KIDs. Avoid /uk/ pages.
- Fund pages such as blackrock.com/lu/individual/en/products/<number>/... list a \
"PRIIP KID" document.

How to work:
1. Search the web, for example for the ISIN together with "eu-priips", or for the fund \
name with "KID".
2. As soon as you have a candidate address, call try_kid_url. It downloads the file and \
checks it. It is the only way a document is accepted: never assume a link works \
without it.
3. If it is rejected, read the reason and adjust: a different file name, the -en.pdf \
version, another search.
4. Use web_fetch only on HTML web pages, never on PDF files (try_kid_url handles PDFs).
5. Stop when a KID is accepted. If you run out of ideas, stop and say in one or two \
sentences what you tried and why it failed.
"""

TRY_KID_URL_TOOL = {
    "name": "try_kid_url",
    "description": (
        "Download the PDF at this address and check whether it is the English PRIIPs KID "
        "of the fund we are looking for. Accepted files are stored in the corpus. The "
        "answer says accepted true/false and, if false, the reason."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full address of the candidate PDF"},
        },
        "required": ["url"],
    },
}


def tools_for(product: Product) -> list[dict]:
    fetch: dict = {
        "type": "web_fetch_20250910",
        "name": "web_fetch",
        "max_uses": MAX_FETCHES,
        "max_content_tokens": 20_000,  # a fund page is long; this caps the token bill
    }
    if domains := ISSUER_DOMAINS.get(product.issuer.lower()):
        fetch["allowed_domains"] = list(domains)  # read only the issuer's own pages
    return [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES},
        fetch,
        TRY_KID_URL_TOOL,
    ]


def task_for(product: Product) -> str:
    return (
        "Find the English EU PRIIPs KID of this fund share class.\n"
        f"ISIN: {product.isin}\n"
        f"Name in our list (may be inexact or outdated): {product.name}\n"
        f"Issuer: {product.issuer}"
    )


@dataclass
class Step:
    kind: str  # think | search | fetch | try | verdict | error
    detail: str


@dataclass
class AgentResult:
    isin: str
    model: str
    verdict: Verdict | None = None  # the accepted one, if any
    attempts: list[Verdict] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    final_text: str = ""
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    fetches: int = 0

    @property
    def found(self) -> bool:
        return self.verdict is not None


def find_kid(
    product: Product,
    *,
    client: anthropic.Anthropic | None = None,
    http: httpx.Client | None = None,
    model: str = DEFAULT_MODEL,
    store: bool = True,
    on_step: Callable[[Step], None] | None = None,
    **store_kwargs,
) -> AgentResult:
    """Run the agent for one fund. `client` and `http` can be swapped for fakes in tests."""
    client = client or anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    own_http = http is None
    http = http or httpx.Client(
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/pdf,*/*"},
        timeout=30.0,
    )
    result = AgentResult(isin=product.isin, model=model)

    def log(kind: str, detail: str) -> None:
        step = Step(kind, detail)
        result.steps.append(step)
        if on_step:
            on_step(step)

    def run_try_kid_url(url: str) -> tuple[str, bool]:
        """Returns (text for the agent, is_error)."""
        if result.found:  # several candidates in one answer: the first good one wins
            return "Already accepted another candidate; this one was not checked.", False
        if len(result.attempts) >= MAX_ATTEMPTS:
            return f"Limit of {MAX_ATTEMPTS} attempts reached. Stop and summarise.", True
        log("try", normalize_url(url))  # show the address that is really downloaded
        verdict = check_url(url, product, http, store=store, **store_kwargs)
        result.attempts.append(verdict)
        if verdict.accepted:
            result.verdict = verdict
            log("verdict", f"ACCEPTED ({verdict.status}), KID dated {verdict.doc_date}")
        else:
            log("verdict", f"rejected: {verdict.reason}")
        return verdict.for_agent(), False

    messages: list[dict] = [{"role": "user", "content": task_for(product)}]
    tools = tools_for(product)
    try:
        for _ in range(MAX_REQUESTS):
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )
            _count_usage(result, response)
            # The assistant's answer becomes part of the conversation, unchanged
            # (search results carry encrypted content the API needs back).
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    log("think", block.text.strip())
                    result.final_text = block.text.strip()
                elif block.type == "server_tool_use":
                    what = block.input.get("query") or block.input.get("url") or str(block.input)
                    log("search" if block.name == "web_search" else "fetch", what)
                elif block.type in ("web_search_tool_result", "web_fetch_tool_result"):
                    err = getattr(block.content, "error_code", None)
                    if err:
                        log("error", f"{block.type}: {err}")
                elif block.type == "tool_use":
                    if block.name == "try_kid_url":
                        text, is_error = run_try_kid_url(str(block.input.get("url", "")))
                    else:
                        text, is_error = f"unknown tool {block.name}", True
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": text,
                         "is_error": is_error}
                    )  # fmt: skip

            if result.found:
                break  # job done; no need to pay for a closing remark
            if response.stop_reason == "tool_use" and tool_results:
                messages.append({"role": "user", "content": tool_results})
                continue
            if response.stop_reason == "pause_turn":
                continue  # a long search turn was paused; sending it back resumes it
            break  # end_turn (or max_tokens): the agent has finished
        else:
            log("error", f"stopped after {MAX_REQUESTS} requests")
    finally:
        if own_http:
            http.close()
    return result


def _count_usage(result: AgentResult, response) -> None:
    result.requests += 1
    usage = response.usage
    result.input_tokens += usage.input_tokens or 0
    result.output_tokens += usage.output_tokens or 0
    server = getattr(usage, "server_tool_use", None)
    if server is not None:
        result.searches += getattr(server, "web_search_requests", 0) or 0
        result.fetches += getattr(server, "web_fetch_requests", 0) or 0


def save_run(result: AgentResult, runs_dir: Path | None = None) -> Path:
    """Write the full record of one run to data/agent_runs/<time>_<isin>.json."""
    runs_dir = runs_dir or config.AGENT_RUNS_DIR
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = runs_dir / f"{stamp}_{result.isin}.json"
    data = asdict(result)
    data["found"] = result.found
    for v in [data["verdict"], *data["attempts"]]:
        if v:
            v.pop("record", None)  # already in the manifest
    path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return path
