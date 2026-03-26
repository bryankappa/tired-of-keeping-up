from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from x_signal_engine.routing import broad_discovery_queries

@dataclass(slots=True)
class Settings:
    base_dir: Path
    db_path: Path
    high_signal_feed_path: Path
    workflow_upgrades_path: Path
    experiments_path: Path
    score_prompt_path: Path
    twscrape_accounts_db_path: Path
    openrouter_base_url: str
    openrouter_api_key: str | None
    openrouter_model: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_parse_mode: str
    tws_account_username: str | None
    tws_account_password: str | None
    tws_account_email: str | None
    tws_account_email_password: str | None
    tws_account_cookies: str | None
    tws_proxy: str | None
    x_search_queries: list[str]
    x_min_views: int
    x_min_bookmarks: int
    x_min_likes: int
    x_article_min_chars: int
    x_expand_candidates_per_query: int
    x_query_since_days: int
    x_search_minutes: int
    x_search_poll_seconds: int
    priority_authors: list[str]
    priority_companies: list[str]
    priority_topics: list[str]
    digest_max_items: int
    official_source_limit: int
    official_article_min_chars: int
    browser_expand_x: bool
    playwright_cli_path: Path
    playwright_headed: bool
    playwright_timeout_ms: int
    openclaw_webhook_url: str | None
    openclaw_auth_token: str | None


def load_settings(base_dir: Path | None = None) -> Settings:
    root = base_dir or Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    data_dir = root / "data"
    notes_dir = root / "notes"
    data_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)
    accounts_db = os.getenv("TWS_ACCOUNTS_DB_PATH", "data/twscrape_accounts.db")
    return Settings(
        base_dir=root,
        db_path=data_dir / "x_signal_engine.db",
        high_signal_feed_path=notes_dir / "high-signal-ai-feed.md",
        workflow_upgrades_path=notes_dir / "workflow-upgrades.md",
        experiments_path=notes_dir / "experiments-to-run.md",
        score_prompt_path=root / "prompts" / "score_article.md",
        twscrape_accounts_db_path=root / accounts_db,
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
        openrouter_model=os.getenv("OPENROUTER_MODEL"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        telegram_parse_mode=os.getenv("TELEGRAM_PARSE_MODE", "HTML"),
        tws_account_username=os.getenv("TWS_ACCOUNT_USERNAME"),
        tws_account_password=os.getenv("TWS_ACCOUNT_PASSWORD"),
        tws_account_email=os.getenv("TWS_ACCOUNT_EMAIL"),
        tws_account_email_password=os.getenv("TWS_ACCOUNT_EMAIL_PASSWORD"),
        tws_account_cookies=os.getenv("TWS_ACCOUNT_COOKIES"),
        tws_proxy=os.getenv("TWS_PROXY"),
        x_search_queries=parse_search_queries(os.getenv("X_SEARCH_QUERIES", "")) or broad_discovery_queries(),
        x_min_views=int(os.getenv("X_MIN_VIEWS", "10000")),
        x_min_bookmarks=int(os.getenv("X_MIN_BOOKMARKS", "100")),
        x_min_likes=int(os.getenv("X_MIN_LIKES", "50")),
        x_article_min_chars=int(os.getenv("X_ARTICLE_MIN_CHARS", "500")),
        x_expand_candidates_per_query=int(os.getenv("X_EXPAND_CANDIDATES_PER_QUERY", "5")),
        x_query_since_days=int(os.getenv("X_QUERY_SINCE_DAYS", "14")),
        x_search_minutes=int(os.getenv("X_SEARCH_MINUTES", "0")),
        x_search_poll_seconds=int(os.getenv("X_SEARCH_POLL_SECONDS", "90")),
        priority_authors=parse_csv(os.getenv("PRIORITY_AUTHORS", "")),
        priority_companies=parse_csv(os.getenv("PRIORITY_COMPANIES", "")),
        priority_topics=parse_csv(
            os.getenv(
                "PRIORITY_TOPICS",
                "agents,coding agents,deep agents,workers,evals,cursor,langgraph,langchain,cuda,kernels,inference,rag,benchmarks,deployment,context engineering",
            )
        ),
        digest_max_items=int(os.getenv("DIGEST_MAX_ITEMS", "3")),
        official_source_limit=int(os.getenv("OFFICIAL_SOURCE_LIMIT", "5")),
        official_article_min_chars=int(os.getenv("OFFICIAL_ARTICLE_MIN_CHARS", "600")),
        browser_expand_x=parse_bool(os.getenv("BROWSER_EXPAND_X", "1")),
        playwright_cli_path=Path(
            os.getenv("PLAYWRIGHT_CLI_PATH", str(Path.home() / ".codex" / "skills" / "playwright" / "scripts" / "playwright_cli.sh"))
        ),
        playwright_headed=parse_bool(os.getenv("PLAYWRIGHT_HEADED", "0")),
        playwright_timeout_ms=int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000")),
        openclaw_webhook_url=os.getenv("OPENCLAW_WEBHOOK_URL"),
        openclaw_auth_token=os.getenv("OPENCLAW_AUTH_TOKEN"),
    )


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'").strip('"')


def parse_search_queries(raw_value: str) -> list[str]:
    return [query.strip() for query in raw_value.split("||") if query.strip()]


def parse_csv(raw_value: str) -> list[str]:
    return [value.strip().lower() for value in raw_value.split(",") if value.strip()]


def parse_bool(raw_value: str) -> bool:
    return raw_value.strip().lower() not in {"", "0", "false", "no", "off"}
