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
        numeric = case["published_finding_numeric_verification_rate"]
        candidate = case["candidate_support_rate"]
        table.add_row(
            case["case_id"],
            case["pattern_id"] or "-",
            "[green]yes[/green]" if case["pattern_found"] else "[red]no[/red]",
            f"{case['published_findings_numeric_valid']}"
            f"/{case['published_findings_numeric_checked']}"
            if case["published_findings_numeric_checked"]
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


@app.command("evaluate-real-model")
def evaluate_real_model(
    out: Annotated[Path | None, typer.Option(help="Where to write the report")] = None,
    datasets: Annotated[
        str | None, typer.Option(help="Comma-separated dataset ids; default is all")
    ] = None,
    questions: Annotated[
        int | None, typer.Option(help="Cap questions per dataset, for a quick pass")
    ] = None,
    skip_warehouse: Annotated[bool, typer.Option(help="Skip the demo warehouse")] = False,
    data_dir: Annotated[
        Path | None, typer.Option(help="Where to generate the evaluation datasets")
    ] = None,
    question_timeout_seconds: Annotated[
        float, typer.Option(help="Hard ceiling on one question, in seconds")
    ] = 600.0,
    resume: Annotated[
        bool, typer.Option(help="Skip questions a previous run already completed")
    ] = True,
    resume_incompatible: Annotated[
        bool, typer.Option(help="Resume even if the model or build differs")
    ] = False,
    checkpoint_dir: Annotated[
        Path | None, typer.Option(help="Where to keep the resumable checkpoint")
    ] = None,
) -> None:
    """Evaluate a *real* model against varied datasets. Opt-in; never in CI.

    Uses whatever `AAE_PROVIDER_MODE` selects, so `local` talks to Ollama and
    `cloud` spends money. This is not the deterministic benchmark: it has no
    answer key and no pass mark, and it reports what a model did rather than
    whether it was right.
    """
    from agentic_analytics.evaluation.real_model import (
        run_real_model_evaluation,
        write_report,
    )

    configure_logging("WARNING", json_output=False)
    cfg = get_settings()
    if cfg.provider_mode == "fake":
        console.print(
            "[red]AAE_PROVIDER_MODE=fake.[/red] This command evaluates a real "
            "model; set `local` for Ollama or `cloud` for a hosted API."
        )
        raise typer.Exit(code=2)

    target = data_dir or (cfg.data_dir.parent / "evaluation-datasets")
    report = asyncio.run(
        run_real_model_evaluation(
            cfg,
            target,
            dataset_ids=[d.strip() for d in datasets.split(",")] if datasets else None,
            include_warehouse=not skip_warehouse,
            max_questions=questions,
            question_timeout_seconds=question_timeout_seconds,
            checkpoint_dir=checkpoint_dir,
            resume=resume,
            resume_incompatible_ok=resume_incompatible,
        )
    )

    env = report["environment"]
    table = Table(
        title=(
            f"Real-model evaluation -- {env['provider_mode']}: {env['model']} "
            f"({env.get('quantization', '?')}, {env.get('parameter_size', '?')})"
        )
    )
    for column, justify in (
        ("dataset", "left"),
        ("kind", "left"),
        ("question", "left"),
        ("done", "center"),
        ("tools", "right"),
        ("pub", "right"),
        ("held", "right"),
        ("plan", "left"),
        ("calls", "right"),
        ("secs", "right"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]
    for outcome in report["outcomes"]:
        if outcome["error"]:
            status = "[red]crash[/red]"
        elif outcome["question_timeout"]:
            status = "[red]t/o[/red]"
        elif outcome["completed"]:
            status = "[green]yes[/green]"
        else:
            status = "[yellow]stop[/yellow]"
        # Whether the model planned this itself, or the engine rescued it.
        if outcome["fallback_plan_used"]:
            plan = "[yellow]fallback[/yellow]"
        elif outcome["tasks_redirected_by_engine"]:
            plan = "[yellow]redirect[/yellow]"
        elif outcome["model_plan_directly_executable"]:
            plan = "[green]direct[/green]"
        else:
            plan = "-"
        table.add_row(
            outcome["dataset"],
            outcome["kind"],
            outcome["question"][:40],
            status,
            str(outcome["tool_calls"]),
            str(outcome["published_findings"]),
            str(outcome["withheld_findings"]),
            plan,
            str(outcome["provider_calls"]),
            f"{outcome['runtime_seconds']:.0f}",
        )
    console.print(table)
    console.print(json.dumps({k: v for k, v in report.items() if k != "outcomes"}, indent=2))

    if out:
        write_report(report, out)
        console.print(f"wrote {out}")


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
