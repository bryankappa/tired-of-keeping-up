from __future__ import annotations

from typing import TYPE_CHECKING

from x_signal_engine.models import NormalizedItem, RouteBucket, ScoredItem

if TYPE_CHECKING:
    from x_signal_engine.config import Settings


HIGH_SIGNAL_TITLE_PATTERNS = [
    "agent worker",
    "agent workers",
    "benchmark",
    "benchmarks",
    "building",
    "cli",
    "clis",
    "cuda",
    "deep agents",
    "deep dive",
    "design",
    "evals",
    "failure analysis",
    "gpu",
    "guide",
    "how we built",
    "how we made",
    "how to",
    "inference",
    "kernel",
    "kernels",
    "lessons from",
    "langgraph",
    "rag",
    "retrieval",
    "rl",
    "rlhf",
    "runtime",
    "simulation",
    "sims",
    "serving",
    "system design",
    "terminal",
    "throughput",
    "triton",
    "what i learned",
    "worker",
    "workers",
    "the case for",
    "context engineering",
    "agent harness",
    "sandbox",
    "skills",
    "architecture",
    "framework",
    "deployment",
    "eval",
]

LOW_SIGNAL_PATTERNS = [
    "vaguepost",
    "you are using",
    "people are using",
    "great reading",
    "looking forward",
    "thank you for the article",
    "feature requests",
    "ai is changing everything",
    "great article",
    "future of ai",
    "thread about",
    "quote tweet",
    "hot take",
]


def resolve_priority(username: str, body: str, settings: Settings) -> str:
    bucket = resolve_route_bucket(username=username, body=body, settings=settings)
    if bucket == "trusted_creator":
        return "trusted_creator"
    if bucket == "official_company":
        return "official"
    return "watch"


def resolve_route_bucket(username: str, body: str, settings: Settings) -> RouteBucket:
    uname = username.lower()
    text = body.lower()
    if uname in settings.priority_authors:
        return "trusted_creator"
    if uname in settings.priority_companies or any(company in text for company in settings.priority_companies):
        return "official_company"
    if any(topic in text for topic in settings.priority_topics):
        return "broad_discovery"
    if looks_like_high_signal_article(body):
        return "broad_discovery"
    return "reject"


def has_article_style_headline(text: str) -> bool:
    headline = pick_headline(text).lower()
    return any(pattern in headline for pattern in HIGH_SIGNAL_TITLE_PATTERNS)


def looks_like_high_signal_article(text: str) -> bool:
    lowered = text.lower()
    if any(pattern in lowered for pattern in LOW_SIGNAL_PATTERNS):
        return False
    if len(" ".join(lowered.split())) < 120:
        return False
    return any(pattern in lowered for pattern in HIGH_SIGNAL_TITLE_PATTERNS)


def validate_article_candidate(
    *,
    title: str,
    body: str,
    route_bucket: RouteBucket,
    min_chars: int,
) -> bool:
    normalized_title = " ".join(title.split()).lower()
    normalized_body = " ".join(body.split())
    lowered_body = normalized_body.lower()
    if any(pattern in normalized_title or pattern in lowered_body for pattern in LOW_SIGNAL_PATTERNS):
        return False
    if route_bucket == "official_company":
        return len(normalized_body) >= max(200, min_chars // 2)
    if len(normalized_body) < min_chars:
        return False
    return has_article_style_headline(title or body) or looks_like_high_signal_article(f"{title}\n{body}")


def is_digest_worthy(scored: ScoredItem) -> bool:
    title = scored.item.title.lower()
    body = scored.item.body.lower()
    if not scored.item.article_validated:
        return False
    if scored.total_score < 72 and scored.item.route_bucket not in {"official_company", "trusted_creator"}:
        return False
    if any(pattern in title or pattern in body for pattern in LOW_SIGNAL_PATTERNS):
        return False
    return looks_like_high_signal_article(f"{title}\n{body}") or scored.item.route_bucket in {"official_company", "trusted_creator"}


def pick_headline(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if len(line) >= 20:
            return line
    return body


def broad_discovery_queries() -> list[str]:
    return [
        'article min_faves:50 ("how we built" OR "what we learned" OR "lessons from" OR "how to" OR architecture) (agents OR "coding agent" OR "deep agents" OR workers OR "agent worker" OR "Claude Code" OR Codex OR Cursor OR MCP OR sandbox) lang:en',
        'article min_faves:50 ("context engineering" OR "agent harness" OR "tool calling" OR "tool use" OR skills OR architecture OR orchestration) ("Claude Code" OR Codex OR Cursor OR Anthropic OR OpenAI OR agents) lang:en',
        'article min_faves:50 (evals OR evaluation OR benchmark OR benchmarks OR "failure analysis") (agents OR "deep agents" OR workers OR "coding agent" OR LangGraph OR LangChain) lang:en',
        'article min_faves:50 (inference OR serving OR vllm OR sglang OR quantization OR batching OR latency OR throughput) (benchmark OR benchmarks OR architecture OR deployment OR kernels OR runtime) lang:en',
        'article min_faves:50 (CUDA OR Triton OR kernels OR kernel OR GPU OR compiler OR runtime OR sims OR simulation) ("how to" OR architecture OR benchmark OR benchmarks OR design) lang:en',
        'article min_faves:50 (RAG OR retrieval OR reranker OR embeddings OR evals OR LangGraph OR LangChain) ("how to" OR architecture OR benchmark OR benchmarks OR implementation) lang:en',
        'article min_faves:50 ("reinforcement learning" OR RLHF OR GRPO OR DPO OR PPO OR "reward model") (training OR evals OR benchmark OR implementation) lang:en',
        'article min_faves:50 (OpenAI OR Anthropic OR xAI OR Gemini OR "Google DeepMind" OR LangChain OR LangGraph OR Cursor OR Baseten) (agents OR inference OR evals OR deployment OR engineering OR release) lang:en',
    ]
