from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Literal


class SourceKind(StrEnum):
    X_POST = "x_post"
    X_ARTICLE = "x_article"
    OFFICIAL_BLOG = "official_blog"
    OFFICIAL_NEWSROOM = "official_newsroom"
    OFFICIAL_ARTICLE = "official_article"


class Verdict(StrEnum):
    IGNORE = "ignore"
    STORE = "store"
    DIGEST = "digest"
    ALERT = "alert"
    ALERT_AND_EXPERIMENT = "alert_and_experiment"


@dataclass(slots=True)
class SourceConfig:
    name: str
    url: str
    kind: SourceKind
    author_handle: str | None = None
    priority: Literal["official", "trusted_creator", "watch"] = "watch"
    discover_limit: int = 8


RouteBucket = Literal["official_company", "trusted_creator", "broad_discovery", "reject"]
ExpansionStatus = Literal["pending", "expanded", "preview_only", "unresolved", "failed"]
BodySource = Literal["preview_text", "expanded_article"]


@dataclass(slots=True)
class NormalizedItem:
    source_name: str
    source_kind: SourceKind
    source_priority: str
    external_id: str
    url: str
    canonical_url: str
    title: str
    body: str
    author: str
    published_at: str
    canonical_author: str = ""
    discovered_by_author: str = ""
    route_bucket: RouteBucket = "broad_discovery"
    discovered_via: str = "unknown"
    body_source: BodySource = "preview_text"
    expansion_status: ExpansionStatus = "pending"
    expansion_strategy: str | None = None
    resolution_status: str = "preview"
    resolution_reason: str | None = None
    article_validated: bool = False
    view_count: int = 0
    bookmark_count: int = 0
    like_count: int = 0
    repost_count: int = 0
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        if self.source_kind in {SourceKind.X_POST, SourceKind.X_ARTICLE}:
            return f"x:{self.external_id}"
        return f"official:{self.canonical_url or self.external_id}"


@dataclass(slots=True)
class ScoreBreakdown:
    implementation_density: int
    novelty: int
    relevance: int
    actionability: int
    credibility: int
    signal_to_hype_ratio: int


@dataclass(slots=True)
class ScoredItem:
    item: NormalizedItem
    breakdown: ScoreBreakdown
    total_score: int
    verdict: Verdict
    tags: list[str]
    short_summary: str
    why_this_article: str
    why_it_matters: list[str]
    key_takeaways: list[str]
    concrete_takeaway: str
    workflow_impact: str
    suspicious_or_weak_points: list[str]
    markdown_entry: str

    def to_json(self) -> str:
        return json_dumps(
            {
                "item": asdict(self.item),
                "breakdown": asdict(self.breakdown),
                "total_score": self.total_score,
                "verdict": self.verdict.value,
                "tags": self.tags,
                "short_summary": self.short_summary,
                "why_this_article": self.why_this_article,
                "why_it_matters": self.why_it_matters,
                "key_takeaways": self.key_takeaways,
                "concrete_takeaway": self.concrete_takeaway,
                "workflow_impact": self.workflow_impact,
                "suspicious_or_weak_points": self.suspicious_or_weak_points,
                "markdown_entry": self.markdown_entry,
            }
        )


def json_dumps(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload)
