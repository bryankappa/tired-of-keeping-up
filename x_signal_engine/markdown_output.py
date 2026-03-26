from __future__ import annotations

from pathlib import Path

from x_signal_engine.models import ScoredItem, Verdict


def append_markdown(scored: ScoredItem, high_signal_path: Path, workflow_path: Path, experiments_path: Path) -> None:
    if scored.verdict is Verdict.IGNORE:
        return
    _append(high_signal_path, scored.markdown_entry)
    if scored.verdict in {Verdict.DIGEST, Verdict.ALERT, Verdict.ALERT_AND_EXPERIMENT}:
        _append(workflow_path, _workflow_entry(scored))
    if scored.verdict is Verdict.ALERT_AND_EXPERIMENT:
        _append(experiments_path, _experiment_entry(scored))


def _append(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content.rstrip() + "\n\n")


def _workflow_entry(scored: ScoredItem) -> str:
    takeaways = "; ".join(scored.key_takeaways[:3])
    return (
        f"## {scored.item.title}\n"
        f"- Summary: {scored.short_summary}\n"
        f"- Why this article: {scored.why_this_article}\n"
        f"- Takeaways: {takeaways}\n"
        f"- Impact: {scored.workflow_impact}\n"
        f"- Try next: {scored.concrete_takeaway}\n"
        f"- Source: {scored.item.canonical_url}\n"
    )


def _experiment_entry(scored: ScoredItem) -> str:
    return (
        f"## {scored.item.title}\n"
        f"- Why this article: {scored.why_this_article}\n"
        f"- Decision: adopt_or_test\n"
        f"- Benchmark idea: measure before and after on a focused coding-agent task.\n"
        f"- Repo change idea: add a small fixture or eval that reflects the source claim.\n"
        f"- Source: {scored.item.canonical_url}\n"
    )
