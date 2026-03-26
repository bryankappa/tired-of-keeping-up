from __future__ import annotations

from x_signal_engine.models import SourceConfig, SourceKind


def hardcoded_sources() -> list[SourceConfig]:
    return [
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
        SourceConfig(
            name="Simon Willison",
            url="https://x.com/simonw",
            kind=SourceKind.X_POST,
            author_handle="simonw",
            priority="trusted_creator",
        ),
        SourceConfig(
            name="Latent Space",
            url="https://x.com/latentspacepod",
            kind=SourceKind.X_POST,
            author_handle="latentspacepod",
            priority="trusted_creator",
        ),
        SourceConfig(
            name="Thorsten Ball",
            url="https://x.com/thorstenball",
            kind=SourceKind.X_POST,
            author_handle="thorstenball",
            priority="trusted_creator",
        ),
        SourceConfig(
            name="Andrej Karpathy",
            url="https://x.com/karpathy",
            kind=SourceKind.X_POST,
            author_handle="karpathy",
            priority="trusted_creator",
        ),
        SourceConfig(
            name="Varun Trivedi",
            url="https://x.com/Vtrivedy10",
            kind=SourceKind.X_POST,
            author_handle="Vtrivedy10",
            priority="trusted_creator",
        ),
        SourceConfig(
            name="Elliot Arledge",
            url="https://x.com/elliotarledge",
            kind=SourceKind.X_POST,
            author_handle="elliotarledge",
            priority="trusted_creator",
        ),
    ]
