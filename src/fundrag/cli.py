"""Command line entry point: `fundrag <command>`.

This file only reads what you typed and calls the real code in the other modules.
"""

from __future__ import annotations

from pathlib import Path

import typer

from fundrag import config, db
from fundrag.collect import manifest
from fundrag.collect.sources import Outcome, download_all, ingest_inbox
from fundrag.collect.universe import load_universe
from fundrag.parse import pipeline

app = typer.Typer(help="Fund research assistant: corpus tools.", no_args_is_help=True)

UniverseOpt = typer.Option(config.DEFAULT_UNIVERSE, "--universe", "-u", help="Universe CSV")
VerboseOpt = typer.Option(False, "--verbose", "-v", help="Also list skipped rows")
JsonlOpt = typer.Option(config.MANIFEST_PATH, "--path", help="JSONL manifest to import")
IsinsArg = typer.Argument(None, help="ISINs to look for")
MissingOpt = typer.Option(False, "--missing", help="Every fund in the universe without a KID")
DryRunOpt = typer.Option(False, "--dry-run", help="Check the links found, but store nothing")
ModelOpt = typer.Option(None, "--model", help="Claude model (default: FUNDRAG_AGENT_MODEL or claude-sonnet-5-5)")


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
    typer.echo(f"Manifest: {manifest.location()}\n")
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


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="0.0.0.0 makes it reachable from outside"),
    port: int = typer.Option(8000, help="Port number"),
    reload: bool = typer.Option(False, help="Restart automatically when you edit the code"),
) -> None:
    """Start the REST API. Then open http://127.0.0.1:8000/docs in your browser."""
    import uvicorn  # imported here so the other commands start fast

    uvicorn.run("fundrag.api.app:app", host=host, port=port, reload=reload)


@app.command()
def find(
    isins: list[str] = IsinsArg,
    missing: bool = MissingOpt,
    dry_run: bool = DryRunOpt,
    model: str = ModelOpt,
    universe: Path = UniverseOpt,
) -> None:
    """Let the collector agent search the web for KIDs (needs ANTHROPIC_API_KEY)."""
    import os

    from fundrag.collect import agent  # imported here: the other commands stay fast

    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("ANTHROPIC_API_KEY is not set. See 'Step 1b' in the README for how to get one.")
        raise typer.Exit(1)

    products = load_universe(universe)
    if missing:
        have = manifest.latest_by_isin(manifest.load())
        targets = [p for p in products if p.isin not in have]
    else:
        by_isin = {p.isin: p for p in products}
        unknown = [i for i in (isins or []) if i.strip().upper() not in by_isin]
        if unknown:
            typer.echo(f"Not in the universe CSV: {', '.join(unknown)}")
            raise typer.Exit(1)
        targets = [by_isin[i.strip().upper()] for i in (isins or [])]
    if not targets:
        typer.echo("Nothing to do. Name some ISINs, or use --missing.")
        return

    labels = {"think": "think", "search": "search", "fetch": "read", "try": "try",
              "verdict": "", "error": "ERROR"}  # fmt: skip
    found = 0
    total_searches = 0
    for n, product in enumerate(targets, start=1):
        typer.echo(f"\n[{n}/{len(targets)}] {product.isin}  {product.name}")

        def show(step, _labels=labels):
            text = step.detail if len(step.detail) < 300 else step.detail[:300] + " ..."
            typer.echo(f"  {_labels[step.kind]:<7}{text}")

        result = agent.find_kid(
            product, model=model or agent.DEFAULT_MODEL, store=not dry_run, on_step=show
        )
        run_file = agent.save_run(result)
        found += result.found
        total_searches += result.searches
        outcome = "FOUND" if result.found else "not found"
        typer.echo(
            f"  => {outcome}: {result.requests} requests, {result.searches} searches, "
            f"{result.fetches} pages read, {result.input_tokens:,} tokens in / "
            f"{result.output_tokens:,} out. Record: {run_file.name}"
        )
        if result.verdict and result.verdict.status == "duplicate":
            typer.echo("     (the same file is already in the corpus)")

    typer.echo(
        f"\n{found}/{len(targets)} found. {total_searches} web searches "
        f"(${total_searches * 0.01:.2f}) plus tokens; exact costs: Usage page of the Console."
    )
    if found and not dry_run:
        typer.echo("Next: `fundrag parse` to split the new KIDs into sections.")


# ---------------------------------------------------------------------------
# `fundrag db ...`: commands for the Postgres manifest
# ---------------------------------------------------------------------------

db_app = typer.Typer(help="Postgres commands (need FUNDRAG_DATABASE_URL).", no_args_is_help=True)
app.add_typer(db_app, name="db")


def _require_database() -> None:
    if not config.DATABASE_URL:
        typer.echo(
            "FUNDRAG_DATABASE_URL is not set, so there is no database to talk to.\n"
            "Inside Docker Compose it is set for you; on the laptop see the README."
        )
        raise typer.Exit(1)


@db_app.command("init")
def db_init() -> None:
    """Create the manifest table if it does not exist yet (safe to repeat)."""
    _require_database()
    db.ensure_schema()
    typer.echo(f"Table 'manifest' is ready in {db.describe()}.")


@db_app.command("import")
def db_import(path: Path = JsonlOpt) -> None:
    """Copy the records of a JSONL manifest into Postgres. Already imported ones are skipped,
    so running it twice does no harm."""
    _require_database()
    if not path.exists():
        typer.echo(f"No such file: {path}")
        raise typer.Exit(1)
    records = manifest.load(path)  # an explicit path always means the JSONL file
    db.ensure_schema()
    added = db.insert_many(r.model_dump() for r in records)
    typer.echo(f"{len(records)} records in {path.name}: {added} imported, "
               f"{len(records) - added} were already in the database.")


if __name__ == "__main__":
    app()
