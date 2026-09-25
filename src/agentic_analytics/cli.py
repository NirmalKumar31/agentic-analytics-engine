"""Command line entry points."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from agentic_analytics import __version__
from agentic_analytics.config import get_settings
from agentic_analytics.data.cli import build as build_warehouse
from agentic_analytics.logging import configure_logging

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Agentic Analytics Engine: demo data, recorded runs and evaluation.",
)
console = Console()


@app.command()
def version() -> None:
    """Print the package version."""
    console.print(__version__)


@app.command("generate-data")
def generate_data(
    out: Annotated[Path | None, typer.Option(help="Output directory")] = None,
    seed: Annotated[int | None, typer.Option(help="Random seed")] = None,
) -> None:
    """Generate the built-in commerce warehouse."""
    cfg = get_settings()
    directory = out or cfg.demo_warehouse_dir
    from agentic_analytics.data.generator import DEFAULT_SEED

    manifest = build_warehouse(directory, seed if seed is not None else DEFAULT_SEED)

    table = Table(title=f"Commerce warehouse -> {directory}")
    table.add_column("table")
    table.add_column("rows", justify="right")
    for name, count in manifest.row_counts.items():
        table.add_row(name, f"{count:,}")
    console.print(table)
    console.print(f"fingerprint: [bold]{manifest.dataset_fingerprint}[/bold]")


@app.command()
def record(
    out: Annotated[Path | None, typer.Option(help="Recordings directory")] = None,
) -> None:
    """Run the demo questions and write recordings, if they pass acceptance."""
    from agentic_analytics.recordings.runner import record_demos

    configure_logging("WARNING", json_output=False)
    cfg = get_settings()
    directory = out or cfg.recordings_dir
    written = asyncio.run(record_demos(directory))
    for path, metrics in written:
        console.print(
            f"[green]wrote[/green] {path.name}  "
            f"findings={metrics['findings_published']} "
            f"rejected={metrics['findings_rejected']} "
            f"tools={metrics['mcp_tool_calls']}"
        )


@app.command()
def evaluate(
    out: Annotated[Path | None, typer.Option(help="Where to write the report")] = None,
) -> None:
    """Run the benchmark suite against the injected ground truth."""
    from agentic_analytics.evaluation.harness import run_benchmark

    configure_logging("WARNING", json_output=False)
    report = asyncio.run(run_benchmark())

    table = Table(title="Deterministic engine benchmark (scripted provider)")
    table.add_column("case")
    table.add_column("pattern")
    table.add_column("found", justify="center")
    table.add_column("numeric", justify="right")
    table.add_column("ok", justify="center")
    table.add_column("candidates", justify="right")
    for case in report["cases"]:
        numeric = case["numeric_accuracy"]
        candidate = case["candidate_support_rate"]
        table.add_row(
            case["case_id"],
            case["pattern_id"] or "-",
            "[green]yes[/green]" if case["pattern_found"] else "[red]no[/red]",
            f"{case['numeric_assertions_correct']}/{case['numeric_assertions']}"
            if case["numeric_assertions"]
            else "-",
            "[green]ok[/green]" if numeric in (1.0, None) else "[red]fail[/red]",
            f"{case['supported_candidate_findings']}/{case['candidate_findings']}"
            if candidate is not None
            else "-",
        )
    console.print(table)
    summary = report["summary"]
    console.print(json.dumps(summary, indent=2))

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
        console.print(f"wrote {out}")

    if summary["patterns_found"] < summary["patterns_expected"]:
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run the web application."""
    import os

    import uvicorn

    # The MCP host policy keys off where the server binds, so the CLI's
    # choice has to reach the settings the application reads.
    os.environ.setdefault("AAE_BIND_HOST", host)
    uvicorn.run("agentic_analytics.api.app:app", host=host, port=port, reload=reload)


@app.command("validate-recordings")
def validate_recordings(
    directory: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Check every recording against the publication acceptance rules."""
    from agentic_analytics.recordings.schema import validate_recording

    cfg = get_settings()
    target = directory or cfg.recordings_dir
    files = sorted(target.glob("*.json"))
    if not files:
        console.print(f"[yellow]no recordings found in {target}[/yellow]")
        raise typer.Exit(code=1)

    failed = 0
    for path in files:
        report = validate_recording(json.loads(path.read_text()))
        if report.ok:
            console.print(f"[green]pass[/green] {path.name}")
            for warning in report.warnings:
                console.print(f"       [yellow]warning:[/yellow] {warning}")
        else:
            failed += 1
            console.print(f"[red]FAIL[/red] {path.name}")
            for error in report.errors:
                console.print(f"       {error}")
    if failed:
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
