from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click

from .agent import TemporalHistoryAgent
from .authorization import AllowAllAuthorization, StaticFixtureAuthorization
from .browser import browse_history
from .context_assembly import assemble_context
from .core import (
    GRANULARITIES,
    build_qa_report,
    estimate_workload,
    load_events,
    normalize_export,
    read_json,
    read_jsonl,
    retrieve,
    run_azure_smoke,
    run_pipeline,
    simulate_inflight_safety,
    validate_export,
    write_json,
)
from .integrity import validate_integrity
from .models import CoverageRecord, SummaryRecord
from .slack_identity import fetch_slack_directory, load_slack_directory
from .visualization import build_visualization

DEFAULT_INPUT = Path("data/raw/export")
DEFAULT_OUTPUT = Path("output")


def emit(value: Any) -> None:
    click.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


def path_option(name: str, default: Path, help_text: str):
    return click.option(
        name,
        type=click.Path(path_type=Path),
        default=default,
        show_default=True,
        help=help_text,
    )


@click.group()
def cli() -> None:
    """Build and inspect a calendar-based temporal history."""


@cli.command("profile")
@path_option("--input-dir", DEFAULT_INPUT, "Extracted source export directory.")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def profile_command(input_dir: Path, output_dir: Path) -> None:
    result = validate_export(input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "source_profile.json", result)
    emit(result)


@cli.command("slack-users")
@click.option("--refresh", is_flag=True, help="Refresh with local SLACK_BOT_TOKEN.")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def slack_users_command(refresh: bool, output_dir: Path) -> None:
    path = output_dir / "slack_users.json"
    snapshot = fetch_slack_directory(path) if refresh else load_slack_directory(path)
    emit(
        {
            "path": str(path),
            "source": snapshot.get("source"),
            "fetched_at": snapshot.get("fetched_at"),
            "user_count": snapshot.get("user_count", 0),
        }
    )


@cli.command("normalize")
@path_option("--input-dir", DEFAULT_INPUT, "Extracted source export directory.")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def normalize_command(input_dir: Path, output_dir: Path) -> None:
    emit(normalize_export(input_dir, output_dir))


@cli.command("estimate")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def estimate_command(output_dir: Path) -> None:
    emit(estimate_workload(output_dir))


@cli.command("azure-smoke")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def azure_smoke_command(output_dir: Path) -> None:
    """Make exactly one small structured-output Azure request."""
    emit(run_azure_smoke(output_dir))


@cli.command("run")
@path_option("--input-dir", DEFAULT_INPUT, "Extracted source export directory.")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
@click.option(
    "--provider",
    type=click.Choice(["mock", "azure"]),
    default="mock",
    show_default=True,
)
@click.option(
    "--max-workers",
    type=click.IntRange(min=1, max=16),
    default=lambda: int(os.environ.get("TEMPORAL_MAX_WORKERS", "3")),
    show_default="TEMPORAL_MAX_WORKERS or 3",
)
@click.option(
    "--pressure-token-limit",
    type=click.IntRange(min=100),
    default=200000,
    show_default=True,
)
def run_command(
    input_dir: Path,
    output_dir: Path,
    provider: str,
    max_workers: int,
    pressure_token_limit: int,
) -> None:
    emit(
        run_pipeline(
            input_dir,
            output_dir,
            provider,
            max_workers,
            pressure_token_limit,
        )
    )


@cli.command("backfill")
@path_option("--input-dir", DEFAULT_INPUT, "Extracted source export directory.")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
@click.option(
    "--provider",
    type=click.Choice(["mock", "azure"]),
    default="mock",
    show_default=True,
)
@click.option("--max-workers", type=click.IntRange(min=1, max=16), default=3)
@click.option(
    "--pressure-token-limit",
    type=click.IntRange(min=100),
    default=200000,
    show_default=True,
)
def backfill_command(
    input_dir: Path,
    output_dir: Path,
    provider: str,
    max_workers: int,
    pressure_token_limit: int,
) -> None:
    """Phase 2 backfill with thread scopes and pressure simulation."""
    emit(
        run_pipeline(
            input_dir,
            output_dir,
            provider,
            max_workers,
            pressure_token_limit,
        )
    )


@cli.command("summarize")
@path_option("--input-dir", DEFAULT_INPUT, "Extracted source export directory.")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
@click.option(
    "--provider",
    type=click.Choice(["mock", "azure"]),
    default="mock",
    show_default=True,
)
@click.option("--max-workers", type=click.IntRange(min=1, max=16), default=3)
@click.option("--pressure-token-limit", type=click.IntRange(min=100), default=200000)
def summarize_command(
    input_dir: Path,
    output_dir: Path,
    provider: str,
    max_workers: int,
    pressure_token_limit: int,
) -> None:
    """Normalize and generate all summary levels."""
    emit(
        run_pipeline(
            input_dir,
            output_dir,
            provider,
            max_workers,
            pressure_token_limit,
        )
    )


@cli.command("retrieve")
@click.option("--range", "range_value", required=True, help="Date or start/end range.")
@click.option(
    "--granularity",
    type=click.Choice(list(GRANULARITIES)),
    default="week",
    show_default=True,
)
@click.option("--raw", is_flag=True, help="Return normalized raw events.")
@click.option("--thread", "thread_root_id", help="Filter by thread root ID.")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def retrieve_command(
    range_value: str,
    granularity: str,
    raw: bool,
    thread_root_id: str | None,
    output_dir: Path,
) -> None:
    emit(
        retrieve(
            output_dir,
            range_value,
            granularity=granularity,  # type: ignore[arg-type]
            raw=raw,
            thread_root_id=thread_root_id,
        )
    )


@cli.command("context")
@click.option("--channel", "channel_id", required=True)
@click.option("--thread", "thread_id")
@click.option("--at", "at_value", required=True, help="Timezone-aware ISO timestamp.")
@click.option("--window-hours", type=click.IntRange(min=1, max=168), default=24)
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def context_command(
    channel_id: str,
    thread_id: str | None,
    at_value: str,
    window_hours: int,
    output_dir: Path,
) -> None:
    package = assemble_context(
        output_dir,
        channel_id,
        at_value,
        thread_id=thread_id,
        window_hours=window_hours,
    )
    safe_thread = (thread_id or "channel").replace("/", "_")
    example_path = (
        output_dir
        / "context_examples"
        / f"{channel_id}_{safe_thread}_{package.at.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    write_json(example_path, package.model_dump(mode="json"))
    emit(package.model_dump(mode="json"))


@cli.command("browse")
@click.option("--actor", "actor_id", required=True)
@click.option("--channel", "channel_id", required=True)
@click.option("--thread", "thread_id")
@click.option("--query", default="", show_default=True)
@click.option("--range", "range_value", required=True)
@click.option(
    "--authorization-fixture",
    type=click.Path(path_type=Path, exists=True),
)
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def browse_command(
    actor_id: str,
    channel_id: str,
    thread_id: str | None,
    query: str,
    range_value: str,
    authorization_fixture: Path | None,
    output_dir: Path,
) -> None:
    authorization = (
        StaticFixtureAuthorization.from_path(authorization_fixture)
        if authorization_fixture
        else AllowAllAuthorization()
    )
    emit(
        browse_history(
            output_dir=output_dir,
            actor_id=actor_id,
            channel_id=channel_id,
            query=query,
            range_value=range_value,
            authorization=authorization,
            thread_id=thread_id,
        )
    )


@cli.command("ask")
@click.option(
    "--question",
    "-q",
    help="Ask one question and exit. Omit for an interactive session.",
)
@click.option(
    "--max-tool-rounds",
    type=click.IntRange(min=1, max=20),
    default=8,
    show_default=True,
)
@click.option(
    "--show-tools/--hide-tools",
    default=True,
    show_default=True,
    help="Print each temporal search performed by the agent.",
)
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def ask_command(
    question: str | None,
    max_tool_rounds: int,
    show_tools: bool,
    output_dir: Path,
) -> None:
    """Ask GPT questions using period- and piece-scoped history search."""
    try:
        agent = TemporalHistoryAgent(
            output_dir,
            max_tool_rounds=max_tool_rounds,
            show_tools=show_tools,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    if question:
        try:
            click.echo(agent.ask(question))
        except Exception as exc:
            raise click.ClickException(str(exc)) from exc
        return

    metadata = agent.search.metadata()
    click.echo(
        f"Temporal History Agent · {agent.provider.model_name}\n"
        f"Coverage: {metadata['source_period']['start_inclusive']} to "
        f"{metadata['source_period']['end_exclusive']} (exclusive)\n"
        "Type /exit to quit."
    )
    while True:
        try:
            prompt = click.prompt("\nYou", prompt_suffix="> ").strip()
        except (click.Abort, EOFError):
            click.echo()
            return
        if prompt.casefold() in {"/exit", "/quit", "exit", "quit"}:
            return
        if not prompt:
            continue
        try:
            answer = agent.ask(prompt)
        except Exception as exc:
            click.echo(f"\nError: {exc}", err=True)
            continue
        click.echo(f"\nAgent:\n{answer}")


@cli.command("validate")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def validate_command(output_dir: Path) -> None:
    result = validate_integrity(output_dir)
    write_json(output_dir / "integrity_report.json", result)
    emit(result)
    if not result["passed"]:
        raise click.ClickException("Temporal history integrity validation failed")


@cli.command("visualize")
@click.option(
    "--output",
    "html_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_OUTPUT / "temporal_history_visualization.html",
    show_default=True,
)
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def visualize_command(output_dir: Path, html_path: Path) -> None:
    result = build_visualization(output_dir, html_path)
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        manifest.setdefault("artifacts", {}).update(
            {
                "visualization_html": result["html"],
                "visualization_data": result["data"],
                "integrity_report": str(output_dir / "integrity_report.json"),
                "qa_report": str(output_dir / "qa_report.md"),
            }
        )
        write_json(manifest_path, manifest)
    emit(result)


@cli.command("safety-simulate")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def safety_command(output_dir: Path) -> None:
    result = simulate_inflight_safety()
    write_json(output_dir / "safety_simulation.json", result.model_dump(mode="json"))
    emit(result.model_dump(mode="json"))


@cli.command("qa")
@path_option("--output-dir", DEFAULT_OUTPUT, "Experiment output directory.")
def qa_command(output_dir: Path) -> None:
    manifest = read_json(output_dir / "manifest.json")
    summaries = {
        level: [
            SummaryRecord.model_validate(row)
            for scope in ("channel", "thread")
            for row in read_jsonl(output_dir / "summaries" / scope / f"{level}.jsonl")
        ]
        for level in GRANULARITIES
    }
    coverage = [
        CoverageRecord.model_validate(row)
        for row in read_jsonl(output_dir / "coverage.jsonl")
    ]
    report = build_qa_report(output_dir, manifest, summaries, coverage)
    report_path = output_dir / "qa_report.md"
    report_path.write_text(report, encoding="utf-8")
    emit(
        {
            "qa_report": str(report_path),
            "normalized_events": len(load_events(output_dir)),
            "summary_counts": {
                level: len(records) for level, records in summaries.items()
            },
        }
    )
