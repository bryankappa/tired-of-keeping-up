from __future__ import annotations

from x_signal_engine.config import Settings
from x_signal_engine.models import SourceConfig, SourceKind


DEFAULT_CURATED_X_AUTHORS = [
    "simonw",
    "latentspacepod",
    "thorstenball",
    "karpathy",
    "vtrivedy10",
    "elliotarledge",
]


def hardcoded_sources(settings: Settings | None = None) -> list[SourceConfig]:
    official_sources = [
        SourceConfig(
            name="OpenAI News",
            url="https://openai.com/news/",
            kind=SourceKind.OFFICIAL_NEWSROOM,
            priority="official",
            discover_limit=6,
        ),
        SourceConfig(
            name="Anthropic News",
            url="https://www.anthropic.com/news",
            kind=SourceKind.OFFICIAL_NEWSROOM,
            priority="official",
            discover_limit=6,
        ),
        SourceConfig(
            name="Google DeepMind Blog",
            url="https://deepmind.google/discover/blog/",
            kind=SourceKind.OFFICIAL_BLOG,
            priority="official",
            discover_limit=4,
        ),
        SourceConfig(
            name="Cursor Blog",
            url="https://cursor.com/blog",
            kind=SourceKind.OFFICIAL_BLOG,
            priority="official",
            discover_limit=5,
        ),
        SourceConfig(
            name="LangChain Blog",
            url="https://blog.langchain.dev/",
            kind=SourceKind.OFFICIAL_BLOG,
            priority="official",
            discover_limit=5,
        ),
    ]
    curated_handles = settings.curated_x_authors if settings and settings.curated_x_authors else DEFAULT_CURATED_X_AUTHORS
    x_sources = [
        SourceConfig(
            name=handle,
            url=f"https://x.com/{handle}",
            kind=SourceKind.X_POST,
            author_handle=handle,
            priority="trusted_creator",
        )
        for handle in curated_handles
    ]
    return [*official_sources, *x_sources]
