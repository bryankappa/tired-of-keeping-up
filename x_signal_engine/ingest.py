from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from textwrap import dedent
from urllib import parse

import x_signal_engine.x_resolution as x_resolution
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
            canonical_author="Anthropic",
            route_bucket="official_company",
            discovered_via="sample_official",
            body_source="expanded_article",
            expansion_status="expanded",
            expansion_strategy="sample_fixture",
            resolution_status="expanded_article",
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
            canonical_author="simonw",
            route_bucket="trusted_creator",
            discovered_via="sample_x",
            body_source="expanded_article",
            expansion_status="expanded",
            expansion_strategy="sample_fixture",
            resolution_status="expanded_article",
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
        sources=sources,
        limit_per_query=limit_per_query,
        search_minutes=search_minutes,
    )
    unseen_x_candidates = filter_seen_items(x_candidates, seen_lookup)
    expanded_x_items = expand_x_candidates(unseen_x_candidates, settings)
    expanded_x_items = filter_seen_items(expanded_x_items, seen_lookup)
    return dedupe_items([*official_items, *expanded_x_items])


async def discover_live_x_candidates(
    settings: Settings,
    sources: list[SourceConfig],
    limit_per_query: int,
    search_minutes: int,
) -> list[NormalizedItem]:
    try:
        apply_twscrape_patch()
        from twscrape import API
    except ImportError as exc:
        raise RuntimeError("twscrape is not installed. Run `uv sync` first.") from exc

    if not settings.x_search_queries and not settings.curated_x_authors:
        raise RuntimeError("No X discovery configured. Set X_SEARCH_QUERIES or CURATED_X_AUTHORS in .env.")

    api = API(str(settings.twscrape_accounts_db_path), proxy=settings.tws_proxy)
    await ensure_account(api, settings)

    seen_ids: set[str] = set()
    items: list[NormalizedItem] = []
    deadline = time.monotonic() + search_minutes * 60 if search_minutes > 0 else None
    while True:
        search_candidates = await discover_search_x_candidates(api, settings, limit_per_query=limit_per_query)
        curated_candidates = await discover_curated_x_candidates(api, settings, sources=sources)
        for normalized in [*search_candidates, *curated_candidates]:
            if deadline is not None and time.monotonic() >= deadline:
                return items
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
    account_username = settings.tws_account_username or "x_signal_engine"
    existing = await api.pool.get_account(account_username)
    if settings.tws_account_cookies:
        if existing is None:
            await api.pool.add_account(
                account_username,
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

    if existing is None:
        await api.pool.add_account(
            settings.tws_account_username,
            settings.tws_account_password,
            settings.tws_account_email,
            settings.tws_account_email_password,
            proxy=settings.tws_proxy,
        )
    await api.pool.login_all()


async def discover_search_x_candidates(api: object, settings: Settings, *, limit_per_query: int) -> list[NormalizedItem]:
    items: list[NormalizedItem] = []
    for query in settings.x_search_queries:
        effective_query = with_since_filter(query, settings.x_query_since_days)
        async for tweet in api.search(effective_query, limit=limit_per_query):
            expanded_tweet = await expand_tweet(api, tweet)
            normalized = normalize_x_candidate(
                expanded_tweet,
                query=effective_query,
                settings=settings,
                source_name=f"X Search: {effective_query}",
                discovered_via="x_search",
            )
            if normalized is not None:
                items.append(normalized)
    return items


async def discover_curated_x_candidates(api: object, settings: Settings, *, sources: list[SourceConfig]) -> list[NormalizedItem]:
    handles = [source.author_handle for source in sources if source.kind == SourceKind.X_POST and source.author_handle]
    if settings.curated_x_authors:
        for handle in settings.curated_x_authors:
            if handle not in handles:
                handles.append(handle)
    if not handles:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, settings.x_query_since_days))
    items: list[NormalizedItem] = []
    for handle in handles:
        user = await api.user_by_login(handle)
        if user is None:
            continue
        async for tweet in api.user_tweets(user.id, limit=settings.curated_x_limit_per_author):
            tweet_date = getattr(tweet, "date", None)
            if tweet_date and hasattr(tweet_date, "astimezone") and tweet_date.astimezone(timezone.utc) < cutoff:
                break
            expanded_tweet = await expand_tweet(api, tweet)
            normalized = normalize_x_candidate(
                expanded_tweet,
                query=f"from:{handle}",
                settings=settings,
                source_name=f"X Timeline: @{handle}",
                discovered_via="x_curated_timeline",
            )
            if normalized is not None:
                items.append(normalized)
    return items


def normalize_x_candidate(
    tweet: object,
    *,
    query: str,
    settings: Settings,
    source_name: str,
    discovered_via: str,
) -> NormalizedItem | None:
    if is_reply_like(tweet) and getattr(tweet, "quotedTweet", None) is None and getattr(tweet, "retweetedTweet", None) is None:
        return None
    resolved = x_resolution.resolve_canonical_tweet(tweet)
    canonical_tweet = resolved.tweet
    body = extract_expanded_body(canonical_tweet)
    username = resolved.canonical_author or x_resolution.tweet_username(canonical_tweet)
    article_url = extract_x_article_url(canonical_tweet)
    external_article_url = extract_external_article_url(canonical_tweet)
    route_bucket = resolve_route_bucket(
        username=username,
        body=body,
        settings=settings,
        discovered_by=resolved.discovered_by_author,
    )
    preview_title = build_title(username=username, body=body)
    preview_validated = validate_article_candidate(
        title=preview_title,
        body=body,
        route_bucket=route_bucket,
        min_chars=max(200, settings.x_article_min_chars // 2),
    )
    article_hint = is_article_candidate(canonical_tweet, settings) or bool(article_url) or bool(external_article_url)
    is_article_like = article_hint or preview_validated
    if not is_article_like:
        return None
    if route_bucket == "reject" and not preview_validated and not article_hint:
        return None
    source_kind = SourceKind.X_ARTICLE if article_hint or preview_validated else SourceKind.X_POST
    external_id = str(getattr(canonical_tweet, "id", "") or getattr(tweet, "id", ""))
    status_url = resolved.canonical_status_url or x_resolution.build_status_url(canonical_tweet)
    canonical_url = external_article_url or article_url or status_url
    item = NormalizedItem(
        source_name=source_name,
        source_kind=source_kind,
        source_priority=resolve_priority(username, body, settings, discovered_by=resolved.discovered_by_author),
        external_id=external_id,
        url=status_url,
        canonical_url=canonical_url,
        title=preview_title,
        body=body,
        author=username or "unknown",
        published_at=coerce_datetime(getattr(canonical_tweet, "date", None)),
        canonical_author=username or "unknown",
        discovered_by_author=resolved.discovered_by_author,
        route_bucket=route_bucket if route_bucket != "reject" else "broad_discovery",
        discovered_via=discovered_via,
        body_source="preview_text",
        expansion_status="pending",
        expansion_strategy="twscrape_preview",
        resolution_status=resolved.resolution_reason,
        resolution_reason=resolved.resolution_reason,
        article_validated=preview_validated,
        view_count=int(getattr(canonical_tweet, "viewCount", 0) or getattr(tweet, "viewCount", 0) or 0),
        bookmark_count=int(
            getattr(canonical_tweet, "bookmarkedCount", 0)
            or getattr(canonical_tweet, "bookmarkCount", 0)
            or getattr(tweet, "bookmarkedCount", 0)
            or getattr(tweet, "bookmarkCount", 0)
            or 0
        ),
        like_count=int(getattr(canonical_tweet, "likeCount", 0) or getattr(tweet, "likeCount", 0) or 0),
        repost_count=int(getattr(canonical_tweet, "retweetCount", 0) or getattr(tweet, "retweetCount", 0) or 0),
        tags=infer_tags(query=query, body=body),
        metadata={
            "query": query,
            "status_url": status_url,
            "article_url": article_url or "",
            "external_article_url": external_article_url or "",
            "discovered_status_url": resolved.discovered_status_url,
            "quoted_tweet_id": resolved.quoted_tweet_id,
            "share_chain": resolved.share_chain,
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
    try:
        details = await api.tweet_details(int(tweet.id))
    except Exception:
        return tweet
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
    external_article_url = str(item.metadata.get("external_article_url", "") or "").strip()
    if external_article_url:
        external_result = expand_external_article(item, external_article_url, settings)
        if external_result is not None:
            return external_result
    if not settings.browser_expand_x:
        item.expansion_status = "preview_only"
        item.expansion_strategy = "browser_disabled"
        item.resolution_status = "preview_only"
        item.resolution_reason = "browser_disabled"
        return item
    if not settings.playwright_cli_path.exists():
        item.expansion_status = "failed"
        item.expansion_strategy = "missing_playwright_cli"
        item.resolution_status = "failed"
        item.resolution_reason = "missing_playwright_cli"
        return item
    try:
        status_url, direct_article_url = resolve_x_browser_targets(item)
        extracted = extract_x_article_with_playwright(
            status_url=status_url,
            direct_article_url=direct_article_url,
            settings=settings,
        )
    except Exception:
        item.expansion_status = "failed"
        item.expansion_strategy = "playwright_error"
        item.resolution_status = "failed"
        item.resolution_reason = "playwright_error"
        return item
    title = extracted.get("title") or item.title
    body = extracted.get("body") or item.body
    canonical_url = extracted.get("canonical_url") or item.canonical_url
    title = normalize_x_expansion_title(title)
    body = clean_x_expansion_body(body=body, author=item.author, title=title)
    expansion_mode = str(extracted.get("expansion_mode", "") or "browser")
    if looks_like_unsupported_x_article(title=title, body=body) or looks_like_status_page_capture(
        body=body,
        expansion_mode=expansion_mode,
        direct_article_url=direct_article_url,
        discovered_article_url=str(extracted.get("discovered_article_url", "") or ""),
        external_article_url=external_article_url,
    ):
        item.expansion_status = "unresolved"
        item.expansion_strategy = f"playwright_{expansion_mode}"
        item.resolution_status = "unresolved"
        item.resolution_reason = "unsupported_or_status_capture"
        item.article_validated = False
        item.metadata.update({k: v for k, v in extracted.items() if k not in {"body", "title"}})
        return item
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
    item.expansion_strategy = f"playwright_{expansion_mode}"
    item.resolution_status = "expanded_article"
    item.resolution_reason = "x_article_expanded"
    item.article_validated = article_validated
    item.source_kind = SourceKind.X_ARTICLE if article_validated else item.source_kind
    if extracted.get("published_at"):
        item.published_at = str(extracted["published_at"])
    item.metadata.update({k: v for k, v in extracted.items() if k not in {"body", "title"}})
    item.tags = list(dict.fromkeys([*item.tags, *infer_tags(query=str(item.metadata.get("query", "")), body=f"{title}\n{body}")]))
    return item


def resolve_x_browser_targets(item: NormalizedItem) -> tuple[str, str]:
    status_url = str(item.metadata.get("status_url", "") or "").strip() or item.url
    article_url = str(item.metadata.get("article_url", "") or "").strip()
    if not article_url and "/i/article/" in item.canonical_url:
        article_url = item.canonical_url
    if not status_url:
        status_url = article_url or item.canonical_url
    return status_url, article_url


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
    item.resolution_status = "expanded_article"
    item.resolution_reason = "linked_external_article"
    item.article_validated = True
    item.source_kind = SourceKind.X_ARTICLE
    if page.published_at:
        item.published_at = page.published_at
    item.tags = list(dict.fromkeys([*item.tags, *infer_tags(query=str(item.metadata.get("query", "")), body=f"{title}\n{body}")]))
    return item


def extract_x_article_with_playwright(
    *,
    status_url: str,
    direct_article_url: str,
    settings: Settings,
) -> dict[str, object]:
    js_code = build_playwright_extractor(
        status_url=status_url,
        direct_article_url=direct_article_url,
        browser_cookies=load_x_browser_cookies(settings),
        timeout_ms=settings.playwright_timeout_ms,
    )
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


def load_x_browser_cookies(settings: Settings) -> list[dict[str, object]]:
    cookie_map = parse_cookie_mapping(settings.tws_account_cookies)
    if not cookie_map:
        cookie_map = load_cookie_mapping_from_accounts_db(settings.twscrape_accounts_db_path)
    return [
        {
            "name": name,
            "value": value,
            "url": "https://x.com",
        }
        for name, value in cookie_map.items()
        if name and value
    ]


def parse_cookie_mapping(raw_value: str | None) -> dict[str, str]:
    if not raw_value:
        return {}
    decoded = raw_value.strip()
    try:
        decoded = base64.b64decode(decoded).decode()
    except Exception:
        pass
    try:
        parsed = json.loads(decoded)
    except json.JSONDecodeError:
        return parse_cookie_header(decoded)
    if isinstance(parsed, dict) and "cookies" in parsed:
        parsed = parsed["cookies"]
    if isinstance(parsed, list):
        return {
            str(entry.get("name", "")).strip(): str(entry.get("value", "")).strip()
            for entry in parsed
            if isinstance(entry, dict)
        }
    if isinstance(parsed, dict):
        return {
            str(name).strip(): str(value).strip()
            for name, value in parsed.items()
        }
    return {}


def parse_cookie_header(raw_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in raw_header.split(";"):
        segment = part.strip()
        if not segment or "=" not in segment:
            continue
        name, value = segment.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and value:
            cookies[name] = value
    return cookies


def load_cookie_mapping_from_accounts_db(db_path: object) -> dict[str, str]:
    path = db_path if hasattr(db_path, "exists") else None
    if path is None or not path.exists():
        return {}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        row = connection.execute(
            """
            SELECT cookies
            FROM accounts
            ORDER BY active DESC, COALESCE(last_used, '') DESC, username ASC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.Error:
        return {}
    finally:
        try:
            if connection is not None:
                connection.close()
        except Exception:
            pass
    if row is None or not row[0]:
        return {}
    try:
        parsed = json.loads(str(row[0]))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(name).strip(): str(value).strip()
        for name, value in parsed.items()
        if str(name).strip() and str(value).strip()
    }


def build_playwright_extractor(
    *,
    status_url: str,
    direct_article_url: str,
    browser_cookies: list[dict[str, object]],
    timeout_ms: int,
) -> str:
    return dedent(
        f"""
        async page => {{
          const startUrl = {json.dumps(status_url)};
          const directArticleUrl = {json.dumps(direct_article_url)};
          const browserCookies = {json.dumps(browser_cookies)};
          const timeoutMs = {timeout_ms};
          const expandPatterns = ['show more', 'read more', 'view article', 'open article', 'continue reading'];

          if (browserCookies.length > 0) {{
            await page.context().addCookies(browserCookies);
          }}

          const openUrl = async url => {{
            if (!url) return;
            await page.goto(url, {{ waitUntil: 'domcontentloaded', timeout: timeoutMs }});
            await page.waitForTimeout(1600);
          }};

          const scrollForContent = async () => {{
            for (let attempt = 0; attempt < 5; attempt += 1) {{
              await page.evaluate(() => window.scrollBy(0, Math.max(window.innerHeight, 700)));
              await page.waitForTimeout(350);
            }}
            await page.evaluate(() => window.scrollTo(0, 0));
            await page.waitForTimeout(200);
          }};

          const discoverArticleUrl = async () => {{
            return await page.evaluate(patterns => {{
              const absoluteUrl = value => {{
                if (!value) return '';
                try {{
                  return new URL(value, location.href).toString();
                }} catch {{
                  return '';
                }}
              }};
              const matchesPattern = value => patterns.some(pattern => value.includes(pattern));
              const candidates = Array.from(document.querySelectorAll('a[href], article a[href], main a[href]'));
              for (const node of candidates) {{
                const href = absoluteUrl(node.getAttribute('href') || node.href || '');
                if (!href) continue;
                const text = (node.innerText || node.textContent || '').trim().toLowerCase();
                if (href.includes('/i/article/')) return href;
                if (matchesPattern(text)) return href;
              }}
              return '';
            }}, expandPatterns);
          }};

          const clickExpansionControl = async () => {{
            return await page.evaluate(patterns => {{
              const isVisible = node => {{
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
              }};
              const candidates = Array.from(document.querySelectorAll('button, a, div[role="button"], div[role="link"]'));
              for (const node of candidates) {{
                if (!isVisible(node)) continue;
                const text = (node.innerText || node.textContent || '').trim().toLowerCase();
                if (!text) continue;
                if (patterns.some(pattern => text.includes(pattern))) {{
                  node.click();
                  return text;
                }}
              }}
              return '';
            }}, expandPatterns);
          }};

          await openUrl(startUrl);
          await scrollForContent();

          let expansionMode = page.url().includes('/i/article/') ? 'article_page' : 'status_page';
          let discoveredArticleUrl = await discoverArticleUrl();
          if (discoveredArticleUrl && discoveredArticleUrl !== page.url()) {{
            await openUrl(discoveredArticleUrl);
            await scrollForContent();
            expansionMode = 'discovered_article_link';
          }} else if (directArticleUrl && !page.url().includes('/i/article/')) {{
            await openUrl(directArticleUrl);
            await scrollForContent();
            expansionMode = 'metadata_article_link';
          }} else {{
            const clickedLabel = await clickExpansionControl();
            if (clickedLabel) {{
              await page.waitForTimeout(1200);
              await scrollForContent();
              expansionMode = 'clicked_expand';
              discoveredArticleUrl = await discoverArticleUrl();
              if (discoveredArticleUrl && discoveredArticleUrl !== page.url()) {{
                await openUrl(discoveredArticleUrl);
                await scrollForContent();
                expansionMode = 'clicked_article_link';
              }}
            }}
          }}

          return await page.evaluate(({{ expansionMode, discoveredArticleUrl }}) => {{
            const isVisible = node => {{
              const style = window.getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            }};
            const uniqueTexts = values => {{
              const seen = new Set();
              const result = [];
              for (const value of values) {{
                const text = (value || '').trim();
                const key = text.toLowerCase();
                if (!text || seen.has(key)) continue;
                seen.add(key);
                result.push(text);
              }}
              return result;
            }};
            const read = selector =>
              Array.from(document.querySelectorAll(selector))
                .filter(isVisible)
                .map(node => (node.innerText || '').trim())
                .filter(text => text.length >= 40);

            const chunks = uniqueTexts([
              ...read('article'),
              ...read('main'),
              ...read('[data-testid="tweetText"]'),
              ...read('div[role="article"]'),
              document.body?.innerText?.trim() || ''
            ]).sort((a, b) => b.length - a.length);

            const title =
              document.querySelector('meta[property="og:title"]')?.content?.trim() ||
              document.querySelector('article h1, main h1, h1')?.innerText?.trim() ||
              document.title;
            const description =
              document.querySelector('meta[name="description"]')?.content?.trim() ||
              document.querySelector('meta[property="og:description"]')?.content?.trim() || '';
            const canonical = document.querySelector('link[rel="canonical"]')?.href || location.href;
            const published =
              document.querySelector('meta[property="article:published_time"]')?.content ||
              document.querySelector('time')?.getAttribute('datetime') || '';
            const primary = chunks.find(text => text.length >= 120) || chunks[0] || '';
            const body = [description, primary]
              .filter(Boolean)
              .filter((value, index, values) => values.findIndex(other => other.toLowerCase() === value.toLowerCase()) === index)
              .join('\\n\\n')
              .trim();

            return {{
              title,
              body,
              canonical_url: canonical,
              published_at: published,
              current_url: location.href,
              discovered_article_url: discoveredArticleUrl || '',
              expansion_mode: expansionMode,
            }};
          }}, {{ expansionMode, discoveredArticleUrl }});
        }}
        """
    ).strip()


def parse_playwright_result(stdout: str) -> dict[str, object]:
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


def looks_like_unsupported_x_article(*, title: str, body: str) -> bool:
    lowered_title = title.strip().lower()
    lowered_body = " ".join(body.split()).lower()
    if lowered_title == "x" and "this page is not supported" in lowered_body:
        return True
    return "this page is not supported" in lowered_body or "please visit the author’s profile" in lowered_body


def looks_like_status_page_capture(
    *,
    body: str,
    expansion_mode: str,
    direct_article_url: str,
    discovered_article_url: str,
    external_article_url: str,
) -> bool:
    if direct_article_url or discovered_article_url or external_article_url:
        return False
    lowered = body.lower()
    if expansion_mode not in {"status_page", "clicked_expand"}:
        return False
    status_noise = ["read ", " replies", "\nquote\n", "\nviews", "·", "quote\n"]
    matches = sum(1 for token in status_noise if token in lowered)
    return matches >= 2


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
    return x_resolution.tweet_preview_body(tweet)


def extract_x_article_url(tweet: object) -> str | None:
    return x_resolution.extract_x_article_url(tweet)


def extract_external_article_url(tweet: object) -> str | None:
    return x_resolution.extract_external_article_url(tweet)


def deduplicate_parts(parts: list[str]) -> list[str]:
    return x_resolution.deduplicate_parts(parts)


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
    if item.discovered_by_author.lower() in settings.priority_authors:
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
    return x_resolution.normalize_external_candidate(url)


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
