from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Iterable
from urllib import parse, request

from x_signal_engine.config import Settings
from x_signal_engine.models import NormalizedItem, SourceConfig, SourceKind
from x_signal_engine.routing import validate_article_candidate


USER_AGENT = "x_signal_engine/0.2 (+official-source-ingest)"


@dataclass(slots=True)
class LinkCandidate:
    href: str
    text: str


@dataclass(slots=True)
class ArticlePage:
    title: str
    description: str
    canonical_url: str | None
    published_at: str | None
    text_chunks: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n\n".join(chunk for chunk in self.text_chunks if chunk).strip()


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[LinkCandidate] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr_map = dict(attrs)
        self._href = attr_map.get("href")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._href:
            return
        text = collapse_whitespace("".join(self._text_parts))
        self.links.append(LinkCandidate(href=self._href, text=text))
        self._href = None
        self._text_parts = []


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.page = ArticlePage(title="", description="", canonical_url=None, published_at=None)
        self._title_parts: list[str] = []
        self._capture_stack: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
            return
        if tag == "title":
            self._capture_stack.append("title")
            return
        if tag == "meta":
            name = attr_map.get("name", "").lower()
            prop = attr_map.get("property", "").lower()
            content = collapse_whitespace(attr_map.get("content", ""))
            if not content:
                return
            if prop == "og:title" and not self.page.title:
                self.page.title = content
            elif name == "description" or prop == "og:description":
                if not self.page.description:
                    self.page.description = content
            elif prop == "article:published_time" or name == "article:published_time":
                self.page.published_at = content
            return
        if tag == "link" and attr_map.get("rel", "").lower() == "canonical":
            href = attr_map.get("href", "").strip()
            if href:
                self.page.canonical_url = href
            return
        if tag in {"article", "main", "section", "p", "li", "pre", "code", "h1", "h2", "h3"}:
            self._capture_stack.append(tag)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = collapse_whitespace(data)
        if not text:
            return
        if self._capture_stack and self._capture_stack[-1] == "title":
            self._title_parts.append(text)
            return
        if self._capture_stack:
            self.page.text_chunks.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if not self._capture_stack:
            return
        if self._capture_stack[-1] == tag:
            self._capture_stack.pop()
        if tag == "title" and self._title_parts and not self.page.title:
            self.page.title = collapse_whitespace(" ".join(self._title_parts))


def discover_official_articles(sources: Iterable[SourceConfig], settings: Settings) -> list[NormalizedItem]:
    items: list[NormalizedItem] = []
    seen_urls: set[str] = set()
    for source in sources:
        if source.kind not in {SourceKind.OFFICIAL_BLOG, SourceKind.OFFICIAL_NEWSROOM}:
            continue
        try:
            html = fetch_text(source.url)
        except Exception:
            continue
        link_candidates = extract_article_links(base_url=source.url, html=html, limit=min(source.discover_limit, settings.official_source_limit))
        for link in link_candidates:
            if link in seen_urls:
                continue
            seen_urls.add(link)
            item = fetch_official_article(source=source, url=link, settings=settings)
            if item is not None:
                items.append(item)
    return items


def fetch_official_article(source: SourceConfig, url: str, settings: Settings) -> NormalizedItem | None:
    try:
        page = fetch_article_page(url)
    except Exception:
        return None
    canonical_url = page.canonical_url or url
    title = page.title or source.name
    body = collapse_article_body(page.description, page.body)
    if looks_like_landing_page(source.url, canonical_url, title, body):
        return None
    article_validated = validate_article_candidate(
        title=title,
        body=body,
        route_bucket="official_company",
        min_chars=settings.official_article_min_chars,
    )
    if not article_validated:
        return None
    return NormalizedItem(
        source_name=source.name,
        source_kind=SourceKind.OFFICIAL_ARTICLE,
        source_priority=source.priority,
        external_id=canonical_url,
        url=url,
        canonical_url=canonical_url,
        title=title,
        body=body,
        author=source.name,
        published_at=page.published_at or now_iso(),
        canonical_author=source.name,
        route_bucket="official_company",
        discovered_via="official_html",
        body_source="expanded_article",
        expansion_status="expanded",
        expansion_strategy="official_page_fetch",
        resolution_status="expanded_article",
        resolution_reason="official_source_fetch",
        article_validated=article_validated,
        tags=infer_tags(f"{title}\n{body}"),
        metadata={"origin": source.url},
    )


def fetch_article_page(url: str) -> ArticlePage:
    html = fetch_text(url)
    parser = ArticleParser()
    parser.feed(html)
    return parser.page


def fetch_text(url: str) -> str:
    http_request = request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with request.urlopen(http_request, timeout=30) as response:
        encoding = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(encoding, errors="replace")


def extract_article_links(base_url: str, html: str, limit: int) -> list[str]:
    collector = LinkCollector()
    collector.feed(html)
    base = parse.urlparse(base_url)
    base_path = base.path.rstrip("/")
    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in collector.links:
        href = candidate.href.strip()
        if not href or href.startswith("#"):
            continue
        resolved = parse.urljoin(base_url, href)
        parsed = parse.urlparse(resolved)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc != base.netloc:
            continue
        candidate_path = parsed.path.rstrip("/")
        if base_path and not candidate_path.startswith(base_path):
            continue
        if not looks_like_article_path(parsed.path, candidate.text):
            continue
        cleaned = parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
        if cleaned == base_url.rstrip("/") or cleaned in seen:
            continue
        seen.add(cleaned)
        candidates.append(cleaned)
        if len(candidates) >= limit:
            break
    return candidates


def looks_like_article_path(path: str, anchor_text: str) -> bool:
    lowered_path = path.lower()
    lowered_text = anchor_text.lower()
    if any(part in lowered_path for part in ["/careers", "/jobs", "/privacy", "/terms", "/about", "/contact"]):
        return False
    if lowered_path.count("/") < 2:
        return False
    slug = lowered_path.rstrip("/").split("/")[-1]
    if slug in {"news", "research", "product", "products", "blog", "posts", "models", "index"}:
        return False
    article_words = ["news", "blog", "research", "post", "article", "release", "system"]
    return any(word in lowered_path for word in article_words) or len(lowered_text.split()) >= 6


def looks_like_landing_page(source_url: str, canonical_url: str, title: str, body: str) -> bool:
    source_path = parse.urlparse(source_url).path.rstrip("/")
    article_path = parse.urlparse(canonical_url).path.rstrip("/")
    path_segments = [segment for segment in article_path.split("/") if segment]
    title_lower = title.lower()
    body_lower = body.lower()
    if article_path == source_path:
        return True
    if path_segments and path_segments[-1] == "index":
        return True
    if any(
        phrase in title_lower
        for phrase in [
            "newsroom |",
            "| recent news",
            "| research |",
            "| product |",
            "models —",
            "models |",
            "research | openai",
        ]
    ):
        return True
    if any(phrase in body_lower for phrase in ["browse all", "latest news", "recent news", "view all", "all announcements"]):
        return True
    return False


def collapse_article_body(description: str, body: str) -> str:
    parts = [collapse_whitespace(description), collapse_whitespace(body)]
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(part)
    return "\n\n".join(deduped).strip()


def infer_tags(text: str) -> list[str]:
    lowered = text.lower()
    tags: list[str] = []
    for candidate in ["agents", "evals", "deployment", "mcp", "context engineering", "benchmarks", "rag", "sandbox", "skills"]:
        if candidate in lowered:
            tags.append(candidate.replace(" ", "_"))
    return tags


def collapse_whitespace(value: str) -> str:
    return " ".join(unescape(value).split())


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
