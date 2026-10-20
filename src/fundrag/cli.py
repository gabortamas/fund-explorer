"""Command line entry point: `fundrag <command>`.

This file only reads what you typed and calls the real code in the other modules.
"""

from __future__ import annotations

from pathlib import Path

import typer

from fundrag import config
from fundrag.collect import manifest
from fundrag.collect.sources import Outcome, download_all, ingest_inbox
from fundrag.collect.universe import load_universe
from fundrag.parse import pipeline

app = typer.Typer(help="Fund research assistant: corpus tools.", no_args_is_help=True)

DEFAULT_UNIVERSE = config.UNIVERSE_DIR / "ishares_v1.csv"
UniverseOpt = typer.Option(DEFAULT_UNIVERSE, "--universe", "-u", help="Universe CSV")
VerboseOpt = typer.Option(False, "--verbose", "-v", help="Also list skipped rows")


def _report(outcomes: list[Outcome], verbose: bool = False) -> None:
    for o in outcomes:
        if o.status == "skipped" and not verbose:
            continue
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
def download(universe: Path = UniverseOpt, verbose: bool = VerboseOpt) -> None:
    """Download every KID that has a kid_url in the universe CSV."""
    _report(download_all(load_universe(universe)), verbose)


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


@app.command()
def parse(force: bool = typer.Option(False, help="Re-parse even if output exists")) -> None:
    """Split every stored KID into its sections (data/processed/kid/)."""
    outcomes = pipeline.parse_all(force=force)
    if not outcomes:
        typer.echo("No KIDs in the manifest yet. Run `fundrag download` first.")
        return
    for o in outcomes:
        flag = {"parsed": "parsed   ", "unchanged": "unchanged", "failed": "FAILED   "}[o.status]
        extra = f"  {o.n_sections} sections" if o.status == "parsed" else ""
        typer.echo(f"{flag}  {o.isin}  {o.doc_id}{extra}")
        for prob in o.problems:
            typer.echo(f"{'':<13}- {prob}")
    clean = sum(not o.problems and o.status != "failed" for o in outcomes)
    typer.echo(f"\n{clean}/{len(outcomes)} KIDs parsed cleanly. Output: {config.PROCESSED_DIR / 'kid'}")


@app.command()
def show(isin: str, section: str = typer.Argument(None, help="Section key, e.g. costs")) -> None:
    """Print the parsed sections of one fund's latest KID (all, or just one)."""
    record = manifest.latest_by_isin(manifest.load()).get(isin.strip().upper())
    if record is None:
        raise typer.BadParameter(f"no KID in the manifest for {isin}")
    try:
        doc = pipeline.load(record.doc_id)
    except FileNotFoundError:
        raise typer.BadParameter(f"{isin} is not parsed yet. Run `fundrag parse` first.") from None
    for s in doc.sections:
        if section and s.key != section:
            continue
        pages = f"p.{s.page_start}" + (f"-{s.page_end}" if s.page_end != s.page_start else "")
        typer.echo(f"\n{'  ' * max(s.level - 1, 0)}[{s.key}] {s.title}  ({pages}, {len(s.text)} chars)")
        if section:
            typer.echo(s.text)
    if section and not doc.section(section):
        keys = ", ".join(s.key for s in doc.sections)
        typer.echo(f"No section '{section}'. Available: {keys}")


if __name__ == "__main__":
    app()
