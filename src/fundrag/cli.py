"""Command line entry point: `fundrag <command>`."""

from __future__ import annotations

from pathlib import Path

import typer

from fundrag import config
from fundrag.collect import manifest
from fundrag.collect.sources import Outcome, download_all, ingest_inbox
from fundrag.collect.universe import load_universe

app = typer.Typer(help="Fund research assistant: corpus tools.", no_args_is_help=True)

DEFAULT_UNIVERSE = config.UNIVERSE_DIR / "ishares_v1.csv"
UniverseOpt = typer.Option(DEFAULT_UNIVERSE, "--universe", "-u", help="Universe CSV")


def _report(outcomes: list[Outcome]) -> None:
    for o in outcomes:
        line = f"{o.status:<10} {o.isin}"
        if o.record:
            line += f"  {o.record.doc_type}  {o.record.doc_date or 'undated'}"
        typer.echo(line)
        if o.detail:
            typer.echo(f"{'':<11}{o.detail}")
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    typer.echo("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))


@app.command()
def download(universe: Path = UniverseOpt) -> None:
    """Download every KID that has a kid_url in the universe CSV."""
    _report(download_all(load_universe(universe)))


@app.command()
def inbox(universe: Path = UniverseOpt, keep: bool = typer.Option(False, help="Keep files")) -> None:
    """Register PDFs saved by hand into data/inbox/."""
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    outcomes = ingest_inbox(load_universe(universe), remove_after=not keep)
    if not outcomes:
        typer.echo(f"Inbox is empty: {config.INBOX_DIR}")
        return
    _report(outcomes)


@app.command()
def status(universe: Path = UniverseOpt) -> None:
    """Show which products in the universe have a usable KID, and what needs attention."""
    products = load_universe(universe)
    latest = manifest.latest_by_isin(manifest.load())
    ready = 0
    for p in products:
        r = latest.get(p.isin)
        if r is None:
            typer.echo(f"MISSING  {p.isin}  {p.name}")
            continue
        flag = "OK     " if not r.problems else "CHECK  "
        ready += not r.problems
        typer.echo(f"{flag}  {p.isin}  {r.doc_date or 'undated'}  {p.name}")
        for prob in r.problems:
            typer.echo(f"{'':<23}- {prob}")
    typer.echo(f"\n{ready}/{len(products)} products have a clean PRIIPs KID.")


if __name__ == "__main__":
    app()
