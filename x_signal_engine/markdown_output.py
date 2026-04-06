from __future__ import annotations

from pathlib import Path
import sqlite3

from x_signal_engine.models import ScoredItem, Verdict
from x_signal_engine.storage import has_output_delivery, record_output_delivery


def append_markdown(
    connection: sqlite3.Connection,
    scored: ScoredItem,
    high_signal_path: Path,
    workflow_path: Path,
    experiments_path: Path,
) -> None:
    if scored.verdict is Verdict.IGNORE:
        return
    if not has_output_delivery(connection, scored.item.dedupe_key, "markdown_high_signal"):
        _append(high_signal_path, scored.markdown_entry)
        record_output_delivery(connection, scored.item.dedupe_key, "markdown_high_signal")
    if scored.verdict in {Verdict.DIGEST, Verdict.ALERT, Verdict.ALERT_AND_EXPERIMENT}:
        if not has_output_delivery(connection, scored.item.dedupe_key, "markdown_workflow"):
            _append(workflow_path, _workflow_entry(scored))
            record_output_delivery(connection, scored.item.dedupe_key, "markdown_workflow")
    if scored.verdict is Verdict.ALERT_AND_EXPERIMENT:
        if not has_output_delivery(connection, scored.item.dedupe_key, "markdown_experiments"):
            _append(experiments_path, _experiment_entry(scored))
            record_output_delivery(connection, scored.item.dedupe_key, "markdown_experiments")


def _append(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content.rstrip() + "\n\n")


def _workflow_entry(scored: ScoredItem) -> str:
    takeaways = "; ".join(scored.key_takeaways[:3])
    surfaced_by = f"- Surfaced by: @{scored.item.discovered_by_author}\n" if scored.item.discovered_by_author else ""
    return (
        f"## {scored.item.title}\n"
        f"- Summary: {scored.short_summary}\n"
        f"- Why this article: {scored.why_this_article}\n"
        f"- Takeaways: {takeaways}\n"
        f"- Impact: {scored.workflow_impact}\n"
        f"- Try next: {scored.concrete_takeaway}\n"
        f"{surfaced_by}"
        f"- Source: {scored.item.canonical_url}\n"
    )


def _experiment_entry(scored: ScoredItem) -> str:
    surfaced_by = f"- Surfaced by: @{scored.item.discovered_by_author}\n" if scored.item.discovered_by_author else ""
    return (
        f"## {scored.item.title}\n"
        f"- Why this article: {scored.why_this_article}\n"
        f"- Decision: adopt_or_test\n"
        f"- Benchmark idea: measure before and after on a focused coding-agent task.\n"
        f"- Repo change idea: add a small fixture or eval that reflects the source claim.\n"
        f"{surfaced_by}"
        f"- Source: {scored.item.canonical_url}\n"
    )
