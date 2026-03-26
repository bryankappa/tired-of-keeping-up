from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from urllib import request

from x_signal_engine.config import Settings
from x_signal_engine.models import NormalizedItem, ScoreBreakdown, ScoredItem, Verdict
from x_signal_engine.storage import get_author_trust


IMPLEMENTATION_TERMS = {
    "agent worker": 9,
    "agent workers": 9,
    "architecture": 8,
    "benchmark": 8,
    "benchmarks": 8,
    "code": 8,
    "context engineering": 10,
    "cuda": 9,
    "cursor": 6,
    "deep agents": 10,
    "deployment": 7,
    "eval": 9,
    "evals": 9,
    "failure analysis": 10,
    "framework": 7,
    "kernel": 9,
    "kernels": 9,
    "harness": 8,
    "how we built": 10,
    "how we made": 10,
    "how to": 8,
    "implementation": 8,
    "langchain": 6,
    "langgraph": 8,
    "lessons from": 8,
    "mcp": 8,
    "reproducible": 7,
    "simulation": 8,
    "sims": 8,
    "sandbox": 8,
    "skills": 8,
    "tool contract": 8,
    "triton": 8,
    "worker": 7,
    "workers": 7,
    "workflow": 7,
}

HYPE_PATTERNS = [
    "game changer",
    "future of ai",
    "great article",
    "must read",
    "changing everything",
    "thread about",
    "thought leadership",
]


def load_score_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def score_item(item: NormalizedItem, score_prompt: str, settings: Settings | None = None) -> ScoredItem:
    if settings and settings.openrouter_api_key and settings.openrouter_model:
        try:
            return _score_with_openrouter(item, score_prompt, settings)
        except Exception:
            pass
    return _score_deterministically(item, settings=settings)


def rerank_digest_candidates(items: list[ScoredItem], settings: Settings) -> list[ScoredItem]:
    if not items:
        return []
    if settings.openrouter_api_key and settings.openrouter_model:
        try:
            reranked = _rerank_with_openrouter(items, settings)
            if reranked:
                return reranked[: settings.digest_max_items]
        except Exception:
            pass
    editorial = sorted(items, key=editorial_priority, reverse=True)
    selected: list[ScoredItem] = []
    seen_authors: set[str] = set()
    for scored in editorial:
        if scored.total_score < 78:
            continue
        if scored.item.author in seen_authors and len(editorial) > settings.digest_max_items:
            continue
        selected.append(scored)
        seen_authors.add(scored.item.author)
        if len(selected) >= settings.digest_max_items:
            break
    return selected


def _score_deterministically(item: NormalizedItem, settings: Settings | None = None) -> ScoredItem:
    text = f"{item.title}\n{item.body}".lower()
    keyword_score = sum(weight for keyword, weight in IMPLEMENTATION_TERMS.items() if keyword in text)
    authority_bonus = 10 if item.route_bucket == "official_company" else 5 if item.route_bucket == "trusted_creator" else 0
    expansion_bonus = 12 if item.body_source == "expanded_article" and item.article_validated else -10
    hype_penalty = sum(4 for pattern in HYPE_PATTERNS if pattern in text)
    popularity_bonus = min(4, item.bookmark_count // 100) if item.route_bucket != "official_company" else 0
    trust_bonus = load_trust_bonus(item.author, settings)
    implementation_density = clamp_score_component(2 + keyword_score // 7 + (2 if "code" in text or "command" in text else 0))
    novelty = clamp_score_component(3 + (3 if "failure analysis" in text or "benchmark" in text else 1))
    relevance = clamp_score_component(4 + min(4, len(item.tags)))
    actionability = clamp_score_component(3 + (3 if any(term in text for term in ["how to", "reproducible", "workflow", "runbook"]) else 1))
    credibility = clamp_score_component(4 + authority_bonus // 3 + (2 if item.article_validated else 0))
    signal_to_hype_ratio = clamp_score_component(5 + keyword_score // 10 - hype_penalty // 2 + (2 if item.body_source == "expanded_article" else -1))
    breakdown = ScoreBreakdown(
        implementation_density=implementation_density,
        novelty=novelty,
        relevance=relevance,
        actionability=actionability,
        credibility=credibility,
        signal_to_hype_ratio=signal_to_hype_ratio,
    )
    total_score = max(
        0,
        min(
            100,
            implementation_density * 2
            + novelty * 2
            + relevance * 2
            + actionability * 2
            + credibility
            + signal_to_hype_ratio
            + authority_bonus
            + expansion_bonus
            + popularity_bonus
            + trust_bonus
            - hype_penalty,
        ),
    )
    verdict = verdict_for_score(total_score, item)
    short_summary = build_short_summary(item)
    why_it_matters = build_why_it_matters(item, text)
    key_takeaways = build_key_takeaways(item, text)
    why_this_article = build_why_this_article(item, text, why_it_matters, key_takeaways)
    concrete_takeaway = build_concrete_takeaway(item, text)
    workflow_impact = build_workflow_impact(item, text)
    suspicious = build_suspicious_points(item, text)
    markdown_entry = build_markdown_entry(
        item,
        total_score,
        verdict.value,
        short_summary,
        why_this_article,
        concrete_takeaway,
        item.tags,
    )
    return ScoredItem(
        item=item,
        breakdown=breakdown,
        total_score=total_score,
        verdict=verdict,
        tags=item.tags,
        short_summary=short_summary,
        why_this_article=why_this_article,
        why_it_matters=why_it_matters,
        key_takeaways=key_takeaways,
        concrete_takeaway=concrete_takeaway,
        workflow_impact=workflow_impact,
        suspicious_or_weak_points=suspicious,
        markdown_entry=markdown_entry,
    )


def _score_with_openrouter(item: NormalizedItem, score_prompt: str, settings: Settings) -> ScoredItem:
    user_prompt = build_user_prompt(item)
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": score_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    content = post_openrouter(payload, settings)
    parsed = json.loads(content)
    return scored_item_from_llm(item, parsed)


def _rerank_with_openrouter(items: list[ScoredItem], settings: Settings) -> list[ScoredItem]:
    indexed = {str(index): item for index, item in enumerate(items)}
    prompt = {
        "task": "Pick the 3 AI engineering articles worth reading this morning.",
        "rules": [
            "Prefer implementation density over popularity.",
            "Prefer expanded canonical content over X previews.",
            "Avoid sending mediocre items; return fewer than 3 if needed.",
            "Reward architecture, evals, deployment, benchmarks, failure analysis, and reproducible workflows.",
        ],
        "candidates": [
            {
                "id": candidate_id,
                "title": scored.item.title,
                "author": scored.item.author,
                "route_bucket": scored.item.route_bucket,
                "body_source": scored.item.body_source,
                "score": scored.total_score,
                "summary": scored.short_summary,
                "why_this_article": scored.why_this_article,
                "why_it_matters": scored.why_it_matters,
                "key_takeaways": scored.key_takeaways,
                "concrete_takeaway": scored.concrete_takeaway,
                "url": scored.item.canonical_url,
            }
            for candidate_id, scored in indexed.items()
        ],
        "response_schema": {"selected_ids": ["string"]},
    }
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a ruthless editorial reranker for elite AI engineering reading. Return JSON only.",
            },
            {"role": "user", "content": json.dumps(prompt, indent=2)},
        ],
        "response_format": {"type": "json_object"},
    }
    content = post_openrouter(payload, settings)
    parsed = json.loads(content)
    selected_ids = [str(value) for value in parsed.get("selected_ids", [])]
    return [indexed[item_id] for item_id in selected_ids if item_id in indexed]


def post_openrouter(payload: dict[str, object], settings: Settings) -> str:
    body = json.dumps(payload).encode("utf-8")
    endpoint = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
    http_request = request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(http_request, timeout=60) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    return parsed["choices"][0]["message"]["content"]


def build_user_prompt(item: NormalizedItem) -> str:
    metrics = {
        "views": item.view_count,
        "bookmarks": item.bookmark_count,
        "likes": item.like_count,
        "reposts": item.repost_count,
    }
    return json.dumps(
        {
            "title": item.title,
            "body": item.body,
            "author": item.author,
            "source": item.source_name,
            "source_priority": item.source_priority,
            "route_bucket": item.route_bucket,
            "url": item.canonical_url,
            "body_source": item.body_source,
            "article_validated": item.article_validated,
            "tags": item.tags,
            "engagement_metrics": metrics,
            "published_at": item.published_at,
        },
        indent=2,
    )


def scored_item_from_llm(item: NormalizedItem, parsed: dict[str, object]) -> ScoredItem:
    total_score = int(parsed.get("total_score", 0))
    verdict_value = str(parsed.get("verdict", verdict_for_score(total_score, item).value))
    short_summary = str(parsed.get("short_summary") or build_short_summary(item))
    why_this_article = str(parsed.get("why_this_article") or "")
    why_it_matters = ensure_list(parsed.get("why_it_matters"), fallback=["High-signal technical content."])
    key_takeaways = ensure_list(parsed.get("key_takeaways"), fallback=build_key_takeaways(item, f"{item.title}\n{item.body}".lower()))
    if not why_this_article:
        why_this_article = build_why_this_article(item, f"{item.title}\n{item.body}".lower(), why_it_matters, key_takeaways)
    suspicious = ensure_list(parsed.get("suspicious_or_weak_points"), fallback=[])
    tags = ensure_list(parsed.get("tags"), fallback=item.tags)
    breakdown_data = parsed.get("breakdown")
    breakdown = ScoreBreakdown(
        implementation_density=clamp_score_component(int((breakdown_data or {}).get("implementation_density", total_score // 10)) if isinstance(breakdown_data, dict) else total_score // 10),
        novelty=clamp_score_component(int((breakdown_data or {}).get("novelty", total_score // 10)) if isinstance(breakdown_data, dict) else total_score // 10),
        relevance=clamp_score_component(int((breakdown_data or {}).get("relevance", total_score // 10)) if isinstance(breakdown_data, dict) else total_score // 10),
        actionability=clamp_score_component(int((breakdown_data or {}).get("actionability", total_score // 10)) if isinstance(breakdown_data, dict) else total_score // 10),
        credibility=clamp_score_component(int((breakdown_data or {}).get("credibility", total_score // 10)) if isinstance(breakdown_data, dict) else total_score // 10),
        signal_to_hype_ratio=clamp_score_component(int((breakdown_data or {}).get("signal_to_hype_ratio", total_score // 10)) if isinstance(breakdown_data, dict) else total_score // 10),
    )
    return ScoredItem(
        item=item,
        breakdown=breakdown,
        total_score=max(0, min(100, total_score)),
        verdict=Verdict(verdict_value),
        tags=tags,
        short_summary=short_summary,
        why_this_article=why_this_article,
        why_it_matters=why_it_matters,
        key_takeaways=key_takeaways,
        concrete_takeaway=str(parsed.get("concrete_takeaway") or "Try the core idea in a small repo experiment."),
        workflow_impact=str(parsed.get("workflow_impact") or "Potential workflow impact not provided."),
        suspicious_or_weak_points=suspicious,
        markdown_entry=str(
            parsed.get("markdown_entry")
            or build_markdown_entry(
                item,
                total_score,
                verdict_value,
                short_summary,
                why_this_article,
                str(parsed.get("concrete_takeaway") or "Try the core idea in a small repo experiment."),
                tags,
            )
        ),
    )


def build_markdown_entry(
    item: NormalizedItem,
    total_score: int,
    verdict: str,
    summary: str,
    why_this_article: str,
    action: str,
    tags: list[str],
) -> str:
    tag_text = ", ".join(tags) if tags else "none"
    return (
        f"## {item.title}\n"
        f"- Source: {item.author} ({item.canonical_url})\n"
        f"- Score: {total_score}\n"
        f"- Decision: {verdict}\n"
        f"- Tags: {tag_text}\n"
        f"- Summary: {summary}\n"
        f"- Why this article: {why_this_article}\n"
        f"- Try: {action}\n"
    )


def build_why_it_matters(item: NormalizedItem, text: str) -> list[str]:
    reasons = []
    if any(term in text for term in ["architecture", "framework", "harness"]):
        reasons.append("Explains architecture choices instead of stopping at opinion.")
    if any(term in text for term in ["eval", "benchmark", "failure analysis"]):
        reasons.append("Includes evidence loops, evaluation detail, or failure analysis.")
    if item.body_source == "expanded_article":
        reasons.append("The score is based on expanded canonical content, not a tweet preview.")
    if not reasons:
        reasons.append("Contains enough implementation detail to test quickly in a real workflow.")
    while len(reasons) < 3:
        reasons.append("Looks more operational than promotional.")
    return reasons[:3]


def build_short_summary(item: NormalizedItem) -> str:
    sentences = sentence_candidates(item.body)
    if sentences:
        return trim_text(" ".join(sentences[:2]), limit=420)
    fallback = collapse_whitespace(item.body or item.title)
    return trim_text(fallback, limit=420)


def build_why_this_article(item: NormalizedItem, text: str, why_it_matters: list[str], takeaways: list[str]) -> str:
    clauses: list[str] = []
    if item.route_bucket == "official_company":
        clauses.append("it comes from an official source with a higher credibility floor")
    elif item.route_bucket == "trusted_creator":
        clauses.append("it comes from a creator who tends to publish technical workflow details")
    if item.body_source == "expanded_article":
        clauses.append("the selection is based on the full article text instead of a social preview")
    if any(term in text for term in ["eval", "benchmark", "failure analysis", "architecture", "kernel", "cuda", "worker", "workers"]):
        clauses.append("it includes concrete mechanisms rather than generic commentary")
    if any(term in text for term in ["how to", "workflow", "reproducible", "deployment", "simulation", "sims"]):
        clauses.append("the ideas look testable in a short engineering experiment")
    lead = "This article made the cut because "
    if clauses:
        return lead + join_clauses(clauses) + "."
    if takeaways:
        return lead + takeaways[0][0].lower() + takeaways[0][1:] + "."
    if why_it_matters:
        sentence = why_it_matters[0].rstrip(".")
        return lead + sentence[0].lower() + sentence[1:] + "."
    return "This article made the cut because it contains more usable implementation signal than noise."


def build_key_takeaways(item: NormalizedItem, text: str) -> list[str]:
    takeaways: list[str] = []
    if any(term in text for term in ["eval", "benchmark", "failure analysis"]):
        takeaways.append("It exposes an evaluation loop, benchmark frame, or failure-analysis habit you can mirror.")
    if any(term in text for term in ["agent", "workers", "worker", "deep agents", "langgraph"]):
        takeaways.append("The interesting part is the agent topology: orchestration boundaries, worker roles, or handoff design.")
    if any(term in text for term in ["cuda", "triton", "kernel", "kernels", "gpu", "runtime"]):
        takeaways.append("The value is in low-level performance decisions such as kernel shape, memory movement, or runtime tradeoffs.")
    if any(term in text for term in ["deployment", "serving", "inference", "latency", "throughput"]):
        takeaways.append("It contains operational details that can tighten serving reliability, latency, or deployment discipline.")
    if any(term in text for term in ["mcp", "sandbox", "tool contract", "context engineering", "skills"]):
        takeaways.append("It highlights interface design choices that can improve tool use, context control, or agent reliability.")
    if not takeaways:
        sentences = sentence_candidates(item.body)
        if sentences:
            takeaways.append(trim_text(sentences[0], limit=180))
    while len(takeaways) < 2:
        takeaways.append("It looks specific enough to turn into a concrete test instead of just a note.")
    return takeaways[:3]


def build_concrete_takeaway(item: NormalizedItem, text: str) -> str:
    if "mcp" in text:
        return "Test the same MCP/tool contract pattern in one internal workflow this week."
    if "eval" in text or "benchmark" in text:
        return "Recreate the evaluation loop on one existing task and compare failure cases."
    if "cuda" in text or "kernel" in text or "triton" in text:
        return "Port one hotspot into a small benchmark harness and measure the claimed kernel/runtime gains."
    if "worker" in text or "workers" in text or "deep agents" in text:
        return "Recreate the worker split on one agent task and inspect whether the handoff improves reliability."
    if "deployment" in text:
        return "Map the deployment guardrails onto your current release path and check for gaps."
    return "Pull one reproducible idea from the piece and test it in a one-hour experiment."


def build_workflow_impact(item: NormalizedItem, text: str) -> str:
    if "agents" in text or "sandbox" in text or "worker" in text or "workers" in text:
        return "Most likely useful for tightening coding-agent reliability and operational safety."
    if "deployment" in text or "inference" in text or "cuda" in text or "kernel" in text:
        return "Most likely useful for shipping AI systems with less guesswork."
    return "Most likely useful as a workflow upgrade rather than general commentary."


def build_suspicious_points(item: NormalizedItem, text: str) -> list[str]:
    suspicious = []
    if item.body_source != "expanded_article":
        suspicious.append("Still based on preview text rather than expanded canonical content.")
    if any(pattern in text for pattern in HYPE_PATTERNS):
        suspicious.append("Contains hype language that may overstate the value.")
    if not item.article_validated:
        suspicious.append("Did not fully clear the article-validation bar.")
    return suspicious


def load_trust_bonus(author: str, settings: Settings | None) -> int:
    if not settings:
        return 0
    try:
        connection = sqlite3.connect(settings.db_path)
        trust_score = get_author_trust(connection, author)
        connection.close()
    except Exception:
        return 0
    return min(8, max(-4, trust_score * 2))


def ensure_list(value: object, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        cleaned = [str(x).strip() for x in value if str(x).strip()]
        return cleaned or fallback
    return fallback


def sentence_candidates(text: str) -> list[str]:
    collapsed = collapse_whitespace(text)
    if not collapsed:
        return []
    parts = re.split(r"(?<=[.!?])\s+", collapsed)
    return [part.strip() for part in parts if len(part.strip()) >= 24]


def collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def trim_text(value: str, limit: int) -> str:
    compact = collapse_whitespace(value)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def join_clauses(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def clamp_score_component(value: int) -> int:
    return max(1, min(10, value))


def editorial_priority(scored: ScoredItem) -> tuple[int, int, int, int, int]:
    route_weight = {"official_company": 3, "trusted_creator": 2, "broad_discovery": 1, "reject": 0}[scored.item.route_bucket]
    expanded_weight = 1 if scored.item.body_source == "expanded_article" else 0
    return (
        scored.total_score,
        route_weight,
        expanded_weight,
        scored.breakdown.implementation_density,
        scored.breakdown.actionability,
    )


def verdict_for_score(score: int, item: NormalizedItem) -> Verdict:
    if not item.article_validated and score < 65:
        return Verdict.IGNORE
    if score >= 88 and item.body_source == "expanded_article":
        return Verdict.ALERT_AND_EXPERIMENT
    if score >= 80 and item.body_source == "expanded_article":
        return Verdict.ALERT
    if score >= 68:
        return Verdict.DIGEST
    if score >= 50:
        return Verdict.STORE
    return Verdict.IGNORE
