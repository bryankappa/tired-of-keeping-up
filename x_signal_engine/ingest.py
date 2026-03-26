from __future__ import annotations

import asyncio
import json
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib import parse

from x_signal_engine.config import Settings
from x_signal_engine.models import NormalizedItem, SourceConfig, SourceKind
from x_signal_engine.official_sources import collapse_article_body, discover_official_articles, fetch_article_page
from x_signal_engine.routing import (
    has_article_style_headline,
    pick_headline,
    resolve_priority,
    resolve_route_bucket,
    validate_article_candidate,
)
from x_signal_engine.storage import SeenItemLookup
from x_signal_engine.twscrape_patch import apply_twscrape_patch


def sample_ingest(sources: list[SourceConfig]) -> list[NormalizedItem]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return [
        NormalizedItem(
            source_name="Anthropic News",
            source_kind=SourceKind.OFFICIAL_ARTICLE,
            source_priority="official",
            external_id="https://www.anthropic.com/news/sample-agent-architecture",
            url="https://www.anthropic.com/news/sample-agent-architecture",
            canonical_url="https://www.anthropic.com/news/sample-agent-architecture",
            title="How we built a safer agent harness for tool-using coding workflows",
            body=(
                "Architecture notes covering tool contracts, sandbox boundaries, eval setup, rollback paths, "
                "and failure analysis for an internal coding-agent harness."
            ),
            author="Anthropic",
            published_at=now,
            route_bucket="official_company",
            discovered_via="sample_official",
            body_source="expanded_article",
            expansion_status="expanded",
            expansion_strategy="sample_fixture",
            article_validated=True,
            tags=["agents", "sandbox", "evals", "architecture"],
        ),
        NormalizedItem(
            source_name="X Search: sample",
            source_kind=SourceKind.X_ARTICLE,
            source_priority="trusted_creator",
            external_id="sample-x-article-001",
            url="https://x.com/simonw/status/sample-x-article-001",
            canonical_url="https://x.com/simonw/status/sample-x-article-001",
            title="What I learned building a reproducible MCP debugging workflow",
            body=(
                "Long-form writeup with concrete MCP contracts, terminal workflow changes, failure cases, "
                "and a small experiment harness that can be reproduced in under an hour."
            ),
            author="simonw",
            published_at=now,
            route_bucket="trusted_creator",
            discovered_via="sample_x",
            body_source="expanded_article",
            expansion_status="expanded",
            expansion_strategy="sample_fixture",
            article_validated=True,
            view_count=18000,
            bookmark_count=240,
            like_count=900,
            repost_count=120,
            tags=["mcp", "workflow", "agents", "evals"],
        ),
    ]


def ingest_live_x(
    settings: Settings,
    sources: list[SourceConfig],
    limit_per_query: int = 20,
    seen_lookup: SeenItemLookup | None = None,
    search_minutes: int = 0,
) -> list[NormalizedItem]:
    return asyncio.run(
        _ingest_live_sources(
            settings,
            sources=sources,
            limit_per_query=limit_per_query,
            seen_lookup=seen_lookup,
            search_minutes=search_minutes,
        )
    )


async def _ingest_live_sources(
    settings: Settings,
    sources: list[SourceConfig],
    limit_per_query: int,
    seen_lookup: SeenItemLookup | None,
    search_minutes: int,
) -> list[NormalizedItem]:
    official_items = filter_seen_items(discover_official_articles(sources, settings), seen_lookup)
    x_candidates = await discover_live_x_candidates(
        settings,
        limit_per_query=limit_per_query,
        search_minutes=search_minutes,
    )
    unseen_x_candidates = filter_seen_items(x_candidates, seen_lookup)
    expanded_x_items = expand_x_candidates(unseen_x_candidates, settings)
    expanded_x_items = filter_seen_items(expanded_x_items, seen_lookup)
    return dedupe_items([*official_items, *expanded_x_items])


async def discover_live_x_candidates(settings: Settings, limit_per_query: int, search_minutes: int) -> list[NormalizedItem]:
    try:
        apply_twscrape_patch()
        from twscrape import API
    except ImportError as exc:
        raise RuntimeError("twscrape is not installed. Run `uv sync` first.") from exc

    if not settings.x_search_queries:
        raise RuntimeError("No X search queries configured. Set X_SEARCH_QUERIES in .env.")

    api = API(str(settings.twscrape_accounts_db_path), proxy=settings.tws_proxy)
    await ensure_account(api, settings)

    seen_ids: set[str] = set()
    items: list[NormalizedItem] = []
    deadline = time.monotonic() + search_minutes * 60 if search_minutes > 0 else None
    while True:
        for query in settings.x_search_queries:
            effective_query = with_since_filter(query, settings.x_query_since_days)
            async for tweet in api.search(effective_query, limit=limit_per_query):
                if deadline is not None and time.monotonic() >= deadline:
                    return items
                expanded_tweet = await expand_tweet(api, tweet)
                normalized = normalize_x_candidate(expanded_tweet, query=effective_query, settings=settings)
                if normalized is None:
                    continue
                if normalized.external_id in seen_ids:
                    continue
                seen_ids.add(normalized.external_id)
                items.append(normalized)
        if deadline is None:
            return items
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return items
        await asyncio.sleep(min(settings.x_search_poll_seconds, max(1, int(remaining))))


async def ensure_account(api: object, settings: Settings) -> None:
    if settings.tws_account_cookies:
        await api.pool.add_account(
            settings.tws_account_username or "x_signal_engine",
            settings.tws_account_password or "cookie-login-not-used",
            settings.tws_account_email or "cookie@example.com",
            settings.tws_account_email_password or "cookie-email-not-used",
            cookies=settings.tws_account_cookies,
            proxy=settings.tws_proxy,
        )
        return

    required = [
        settings.tws_account_username,
        settings.tws_account_password,
        settings.tws_account_email,
        settings.tws_account_email_password,
    ]
    if not all(required):
        raise RuntimeError(
            "twscrape requires either TWS_ACCOUNT_COOKIES or the full login path "
            "(TWS_ACCOUNT_USERNAME, TWS_ACCOUNT_PASSWORD, TWS_ACCOUNT_EMAIL, TWS_ACCOUNT_EMAIL_PASSWORD)."
        )

    await api.pool.add_account(
        settings.tws_account_username,
        settings.tws_account_password,
        settings.tws_account_email,
        settings.tws_account_email_password,
        proxy=settings.tws_proxy,
    )
    await api.pool.login_all()


def normalize_x_candidate(tweet: object, *, query: str, settings: Settings) -> NormalizedItem | None:
    if is_reply_like(tweet):
        return None
    body = extract_expanded_body(tweet)
    username = str(getattr(getattr(tweet, "user", None), "username", "") or "")
    article_url = extract_x_article_url(tweet)
    external_article_url = extract_external_article_url(tweet)
    route_bucket = resolve_route_bucket(username=username, body=body, settings=settings)
    preview_title = build_title(username=username, body=body)
    preview_validated = validate_article_candidate(
        title=preview_title,
        body=body,
        route_bucket=route_bucket,
        min_chars=max(200, settings.x_article_min_chars // 2),
    )
    article_hint = is_article_candidate(tweet, settings) or bool(article_url) or bool(external_article_url)
    is_article_like = article_hint or preview_validated
    if not is_article_like:
        return None
    if route_bucket == "reject" and not preview_validated and not article_hint:
        return None
    source_kind = SourceKind.X_ARTICLE if article_hint or preview_validated else SourceKind.X_POST
    external_id = str(tweet.id)
    status_url = f"https://x.com/{username}/status/{tweet.id}"
    canonical_url = external_article_url or article_url or status_url
    item = NormalizedItem(
        source_name=f"X Search: {query}",
        source_kind=source_kind,
        source_priority=resolve_priority(username, body, settings),
        external_id=external_id,
        url=status_url,
        canonical_url=canonical_url,
        title=preview_title,
        body=body,
        author=username or "unknown",
        published_at=coerce_datetime(getattr(tweet, "date", None)),
        route_bucket=route_bucket if route_bucket != "reject" else "broad_discovery",
        discovered_via="x_search",
        body_source="preview_text",
        expansion_status="pending",
        expansion_strategy="twscrape_preview",
        article_validated=preview_validated,
        view_count=int(getattr(tweet, "viewCount", 0) or 0),
        bookmark_count=int(getattr(tweet, "bookmarkedCount", 0) or getattr(tweet, "bookmarkCount", 0) or 0),
        like_count=int(getattr(tweet, "likeCount", 0) or 0),
        repost_count=int(getattr(tweet, "retweetCount", 0) or 0),
        tags=infer_tags(query=query, body=body),
        metadata={
            "query": query,
            "status_url": status_url,
            "article_url": article_url or "",
            "external_article_url": external_article_url or "",
        },
    )
    if not passes_x_ingestion_gate(item, settings):
        return None
    return item


def build_title(username: str, body: str) -> str:
    headline = pick_headline(body)
    snippet = " ".join(headline.split())[:100].rstrip()
    return f"@{username}: {snippet}" if snippet else f"@{username}"


def is_article_candidate(tweet: object, settings: Settings) -> bool:
    body = extract_expanded_body(tweet)
    card = getattr(tweet, "card", None)
    long_form = len(body.strip()) >= settings.x_article_min_chars
    has_article_card = bool(getattr(card, "title", None) and getattr(card, "description", None))
    return bool(extract_x_article_url(tweet)) or bool(extract_external_article_url(tweet)) or has_article_card or (long_form and has_article_style_headline(body))


async def expand_tweet(api: object, tweet: object) -> object:
    details = await api.tweet_details(int(tweet.id))
    return details or tweet


def expand_x_candidates(items: list[NormalizedItem], settings: Settings) -> list[NormalizedItem]:
    shortlist = shortlist_for_expansion(items, settings)
    shortlist_ids = {item.external_id for item in shortlist}
    expanded: list[NormalizedItem] = []
    for item in items:
        if item.external_id not in shortlist_ids:
            item.expansion_status = "preview_only"
            item.expansion_strategy = "shortlist_skip"
            expanded.append(item)
            continue
        expanded.append(expand_x_item(item, settings))
    return expanded


def shortlist_for_expansion(items: list[NormalizedItem], settings: Settings) -> list[NormalizedItem]:
    grouped: dict[str, list[NormalizedItem]] = defaultdict(list)
    for item in items:
        grouped[item.metadata.get("query", item.source_name)].append(item)

    shortlisted: list[NormalizedItem] = []
    for query_items in grouped.values():
        query_items.sort(key=expansion_priority, reverse=True)
        shortlisted.extend(query_items[: settings.x_expand_candidates_per_query])
    return shortlisted


def expansion_priority(item: NormalizedItem) -> tuple[int, int, int, int]:
    route_weight = {"official_company": 3, "trusted_creator": 2, "broad_discovery": 1, "reject": 0}[item.route_bucket]
    article_hint = 1 if item.article_validated or item.source_kind == SourceKind.X_ARTICLE else 0
    engagement = min(1000, item.bookmark_count + item.view_count // 100)
    topic_bonus = len(item.tags)
    return (route_weight, article_hint, engagement, topic_bonus)


def expand_x_item(item: NormalizedItem, settings: Settings) -> NormalizedItem:
    external_article_url = item.metadata.get("external_article_url", "").strip()
    if external_article_url:
        external_result = expand_external_article(item, external_article_url, settings)
        if external_result is not None:
            return external_result
    if not settings.browser_expand_x:
        item.expansion_status = "preview_only"
        item.expansion_strategy = "browser_disabled"
        return item
    if not settings.playwright_cli_path.exists():
        item.expansion_status = "failed"
        item.expansion_strategy = "missing_playwright_cli"
        return item
    try:
        extracted = extract_x_article_with_playwright(resolve_x_expansion_url(item), settings)
    except Exception:
        item.expansion_status = "failed"
        item.expansion_strategy = "playwright_error"
        return item
    title = extracted.get("title") or item.title
    body = extracted.get("body") or item.body
    canonical_url = extracted.get("canonical_url") or item.canonical_url
    title = normalize_x_expansion_title(title)
    body = clean_x_expansion_body(body=body, author=item.author, title=title)
    article_validated = validate_article_candidate(
        title=title,
        body=body,
        route_bucket=item.route_bucket,
        min_chars=settings.x_article_min_chars,
    )
    item.title = title
    item.body = body
    item.canonical_url = canonical_url
    item.url = canonical_url
    item.body_source = "expanded_article"
    item.expansion_status = "expanded"
    item.expansion_strategy = "playwright_browser"
    item.article_validated = article_validated
    item.source_kind = SourceKind.X_ARTICLE if article_validated else item.source_kind
    if extracted.get("published_at"):
        item.published_at = str(extracted["published_at"])
    item.metadata.update({k: str(v) for k, v in extracted.items() if k not in {"body", "title"}})
    item.tags = list(dict.fromkeys([*item.tags, *infer_tags(query=item.metadata.get("query", ""), body=f"{title}\n{body}")]))
    return item


def resolve_x_expansion_url(item: NormalizedItem) -> str:
    external_article_url = item.metadata.get("external_article_url", "").strip()
    if external_article_url:
        return external_article_url
    article_url = item.metadata.get("article_url", "").strip()
    if article_url:
        return article_url
    return item.canonical_url


def expand_external_article(item: NormalizedItem, url: str, settings: Settings) -> NormalizedItem | None:
    try:
        page = fetch_article_page(url)
    except Exception:
        return None
    title = page.title or item.title
    body = collapse_article_body(page.description, page.body)
    article_validated = validate_article_candidate(
        title=title,
        body=body,
        route_bucket=item.route_bucket,
        min_chars=settings.x_article_min_chars,
    )
    if not article_validated:
        return None
    item.title = title
    item.body = body
    item.url = item.metadata.get("status_url", item.url)
    item.canonical_url = page.canonical_url or url
    item.body_source = "expanded_article"
    item.expansion_status = "expanded"
    item.expansion_strategy = "linked_article_fetch"
    item.article_validated = True
    item.source_kind = SourceKind.X_ARTICLE
    if page.published_at:
        item.published_at = page.published_at
    item.tags = list(dict.fromkeys([*item.tags, *infer_tags(query=item.metadata.get("query", ""), body=f"{title}\n{body}")]))
    return item


def extract_x_article_with_playwright(url: str, settings: Settings) -> dict[str, str]:
    js_code = build_playwright_extractor(url, settings.playwright_timeout_ms)
    command = ["bash", str(settings.playwright_cli_path)]
    if settings.playwright_headed:
        command.append("--headed")
    command.extend(["run-code", js_code])
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        cwd=str(settings.base_dir),
        timeout=max(30, settings.playwright_timeout_ms // 1000 + 15),
    )
    return parse_playwright_result(completed.stdout)


def build_playwright_extractor(url: str, timeout_ms: int) -> str:
    return (
        "async page => {"
        f" await page.goto({json.dumps(url)}, {{ waitUntil: 'domcontentloaded', timeout: {timeout_ms} }});"
        " await page.waitForTimeout(1800);"
        " return await page.evaluate(() => {"
        "   const read = selector => Array.from(document.querySelectorAll(selector)).map(node => (node.innerText || '').trim()).filter(Boolean);"
        "   const chunks = ["
        "     ...read('article'),"
        "     ...read('main'),"
        "     ...read('[data-testid=\"tweetText\"]'),"
        "     ...read('div[role=\"article\"]'),"
        "     document.body?.innerText?.trim() || ''"
        "   ].filter(Boolean).sort((a, b) => b.length - a.length);"
        "   const title ="
        "     document.querySelector('meta[property=\"og:title\"]')?.content?.trim() ||"
        "     document.querySelector('article h1, main h1, h1')?.innerText?.trim() ||"
        "     document.title;"
        "   const description ="
        "     document.querySelector('meta[name=\"description\"]')?.content?.trim() ||"
        "     document.querySelector('meta[property=\"og:description\"]')?.content?.trim() || '';"
        "   const canonical = document.querySelector('link[rel=\"canonical\"]')?.href || location.href;"
        "   const published ="
        "     document.querySelector('meta[property=\"article:published_time\"]')?.content ||"
        "     document.querySelector('time')?.getAttribute('datetime') || '';"
        "   const body = [description, chunks[0] || ''].filter(Boolean).join('\\n\\n').trim();"
        "   return { title, body, canonical_url: canonical, published_at: published };"
        " });"
        " }"
    )


def parse_playwright_result(stdout: str) -> dict[str, str]:
    marker = "### Result"
    if marker not in stdout:
        raise RuntimeError("Playwright CLI did not return a structured result.")
    after = stdout.split(marker, 1)[1].strip()
    line = after.splitlines()[0].strip()
    return json.loads(line)


def normalize_x_expansion_title(title: str) -> str:
    stripped = title.strip()
    if ' on X: "' in stripped and stripped.endswith('" / X'):
        return stripped.split(' on X: "', 1)[1].rsplit('" / X', 1)[0].strip()
    return stripped


def clean_x_expansion_body(*, body: str, author: str, title: str) -> str:
    noise = {
        "Don’t miss what’s happening",
        "People on X are the first to know.",
        "Log in",
        "Sign up",
        "Article",
        "See new posts",
        "Conversation",
        author,
        f"@{author}",
        title,
    }
    cleaned_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in noise:
            continue
        if line.startswith("@"):
            continue
        if line.replace(".", "", 1).isdigit():
            continue
        cleaned_lines.append(line)
    while cleaned_lines and looks_like_x_metadata_line(cleaned_lines[0], title):
        cleaned_lines.pop(0)
    return "\n".join(cleaned_lines).strip()


def looks_like_x_metadata_line(line: str, title: str) -> bool:
    normalized = line.strip()
    compact = normalized.replace(".", "").replace(",", "").replace("K", "").replace("M", "")
    if compact.isdigit():
        return True
    if normalized.lower() == title.lower():
        return False
    if len(normalized) <= 24 and len(normalized.split()) <= 4 and not any(char in normalized for char in ".:?!"):
        return True
    return False


def extract_expanded_body(tweet: object) -> str:
    body = (getattr(tweet, "rawContent", "") or getattr(tweet, "content", "") or "").strip()
    card = getattr(tweet, "card", None)
    title = (getattr(card, "title", "") or "").strip()
    description = (getattr(card, "description", "") or "").strip()
    parts = [part for part in [title, description, body] if part]
    return "\n\n".join(deduplicate_parts(parts))


def extract_x_article_url(tweet: object) -> str | None:
    links = getattr(tweet, "links", None) or []
    for link in links:
        candidates = [
            str(getattr(link, "url", "") or ""),
            str(getattr(link, "expandedUrl", "") or ""),
            str(getattr(link, "expanded_url", "") or ""),
        ]
        for url in candidates:
            if "x.com/i/article/" in url:
                return url.replace("http://", "https://")
    return None


def extract_external_article_url(tweet: object) -> str | None:
    links = getattr(tweet, "links", None) or []
    card = getattr(tweet, "card", None)
    card_url = str(getattr(card, "url", "") or getattr(card, "expandedUrl", "") or getattr(card, "expanded_url", "") or "").strip()
    candidates = [card_url]
    for link in links:
        candidates.extend(
            [
                str(getattr(link, "expandedUrl", "") or ""),
                str(getattr(link, "expanded_url", "") or ""),
                str(getattr(link, "url", "") or ""),
            ]
        )
    for candidate in candidates:
        normalized = normalize_external_candidate(candidate)
        if normalized:
            return normalized
    return None


def deduplicate_parts(parts: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        normalized = " ".join(part.split()).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(part)
    return result


def is_reply_like(tweet: object) -> bool:
    body = (getattr(tweet, "rawContent", "") or "").strip()
    return bool(getattr(tweet, "inReplyToTweetId", None)) or body.startswith("@")


def infer_tags(query: str, body: str) -> list[str]:
    text = f"{query} {body}".lower()
    tags = []
    for candidate in ["agents", "evals", "rag", "inference", "deployment", "sandbox", "mcp", "skills", "benchmarks"]:
        if candidate in text:
            tags.append(candidate)
    for candidate in ["claude", "openai", "anthropic", "gemini", "langchain"]:
        if candidate in text:
            tags.append(candidate)
    return tags


def dedupe_items(items: list[NormalizedItem]) -> list[NormalizedItem]:
    deduped: dict[str, NormalizedItem] = {}
    for item in items:
        key = normalize_url_key(item.canonical_url) or item.external_id or item.dedupe_key
        deduped[key] = item
    return list(deduped.values())


def filter_seen_items(items: list[NormalizedItem], seen_lookup: SeenItemLookup | None) -> list[NormalizedItem]:
    if seen_lookup is None:
        return items
    return [item for item in items if not seen_lookup.matches(item)]


def passes_x_ingestion_gate(item: NormalizedItem, settings: Settings) -> bool:
    lowered = f"{item.title}\n{item.body}".lower()
    if item.author.lower() in settings.priority_authors:
        return True
    if item.author.lower() in settings.priority_companies:
        return True
    if any(topic in lowered for topic in settings.priority_topics):
        return True
    if item.view_count >= settings.x_min_views:
        return True
    if item.bookmark_count >= settings.x_min_bookmarks:
        return True
    if item.like_count >= settings.x_min_likes:
        return True
    return False


def with_since_filter(query: str, since_days: int) -> str:
    if since_days <= 0:
        return query
    lowered = query.lower()
    if " since:" in lowered or lowered.startswith("since:") or " until:" in lowered:
        return query
    since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).date().isoformat()
    return f"{query} since:{since_date}"


def normalize_external_candidate(url: str) -> str | None:
    stripped = url.strip().replace("http://", "https://")
    if not stripped or stripped.startswith("https://t.co/"):
        return None
    parsed = parse.urlparse(stripped)
    if parsed.scheme not in {"http", "https"}:
        return None
    domain = parsed.netloc.lower()
    if domain.endswith("x.com") or domain.endswith("twitter.com"):
        return None
    path = parsed.path.lower()
    if any(path.endswith(suffix) for suffix in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".pdf", ".zip"]):
        return None
    cleaned_path = parsed.path.rstrip("/") or "/"
    return parse.urlunparse((parsed.scheme.lower(), domain, cleaned_path, "", "", ""))


def normalize_url_key(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    parsed = parse.urlparse(stripped)
    if parsed.scheme not in {"http", "https"}:
        return stripped
    cleaned_path = parsed.path.rstrip("/") or "/"
    return parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), cleaned_path, "", "", ""))


def coerce_datetime(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
