from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path
from urllib import parse

from x_signal_engine.models import NormalizedItem, ScoredItem


@dataclass(slots=True)
class SeenItemLookup:
    dedupe_keys: set[str]
    external_ids: set[str]
    urls: set[str]

    def matches(self, item: NormalizedItem) -> bool:
        if item.dedupe_key in self.dedupe_keys:
            return True
        if item.external_id and item.external_id in self.external_ids:
            return True
        candidates = {
            normalize_url_key(item.url),
            normalize_url_key(item.canonical_url),
            normalize_url_key(str(item.metadata.get("status_url", ""))),
            normalize_url_key(str(item.metadata.get("article_url", ""))),
            normalize_url_key(str(item.metadata.get("external_article_url", ""))),
            normalize_url_key(str(item.metadata.get("discovered_status_url", ""))),
        }
        return any(candidate and candidate in self.urls for candidate in candidates)


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    dedupe_key TEXT PRIMARY KEY,
    external_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_priority TEXT NOT NULL,
    route_bucket TEXT NOT NULL DEFAULT 'broad_discovery',
    discovered_via TEXT NOT NULL DEFAULT 'unknown',
    body_source TEXT NOT NULL DEFAULT 'preview_text',
    expansion_status TEXT NOT NULL DEFAULT 'pending',
    expansion_strategy TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'preview',
    resolution_reason TEXT,
    article_validated INTEGER NOT NULL DEFAULT 0,
    author TEXT NOT NULL,
    canonical_author TEXT NOT NULL DEFAULT '',
    discovered_by_author TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    published_at TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    score_json TEXT
);
CREATE TABLE IF NOT EXISTS author_trust (
    author TEXT PRIMARY KEY,
    trust_score INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    last_feedback_at TEXT
);
CREATE TABLE IF NOT EXISTS item_feedback (
    dedupe_key TEXT PRIMARY KEY,
    feedback TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_outputs (
    dedupe_key TEXT NOT NULL,
    channel TEXT NOT NULL,
    date_bucket TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (dedupe_key, channel, date_bucket)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.executescript(SCHEMA)
    ensure_item_columns(connection)
    connection.commit()
    return connection


def upsert_item(connection: sqlite3.Connection, item: NormalizedItem, scored: ScoredItem | None = None) -> None:
    metrics = {
        "views": item.view_count,
        "bookmarks": item.bookmark_count,
        "likes": item.like_count,
        "reposts": item.repost_count,
    }
    score_json = scored.to_json() if scored else None
    connection.execute(
        """
        INSERT INTO items (
            dedupe_key, external_id, url, canonical_url, source_name, source_kind, source_priority, route_bucket,
            discovered_via, body_source, expansion_status, expansion_strategy, resolution_status, resolution_reason,
            article_validated, author, canonical_author, discovered_by_author, title, body, published_at,
            tags_json, metrics_json, metadata_json, score_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dedupe_key) DO UPDATE SET
            external_id=excluded.external_id,
            url=excluded.url,
            canonical_url=excluded.canonical_url,
            title=excluded.title,
            body=excluded.body,
            published_at=excluded.published_at,
            route_bucket=excluded.route_bucket,
            discovered_via=excluded.discovered_via,
            body_source=excluded.body_source,
            expansion_status=excluded.expansion_status,
            expansion_strategy=excluded.expansion_strategy,
            resolution_status=excluded.resolution_status,
            resolution_reason=excluded.resolution_reason,
            article_validated=excluded.article_validated,
            canonical_author=excluded.canonical_author,
            discovered_by_author=excluded.discovered_by_author,
            tags_json=excluded.tags_json,
            metrics_json=excluded.metrics_json,
            metadata_json=excluded.metadata_json,
            score_json=excluded.score_json
        """,
        (
            item.dedupe_key,
            item.external_id,
            item.url,
            item.canonical_url,
            item.source_name,
            item.source_kind.value,
            item.source_priority,
            item.route_bucket,
            item.discovered_via,
            item.body_source,
            item.expansion_status,
            item.expansion_strategy,
            item.resolution_status,
            item.resolution_reason,
            int(item.article_validated),
            item.author,
            item.canonical_author or item.author,
            item.discovered_by_author,
            item.title,
            item.body,
            item.published_at,
            json.dumps(item.tags),
            json.dumps(metrics),
            json.dumps(item.metadata),
            score_json,
        ),
    )
    connection.commit()


def count_items(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) FROM items").fetchone()
    return int(row[0]) if row else 0


def load_seen_item_lookup(connection: sqlite3.Connection) -> SeenItemLookup:
    rows = connection.execute(
        """
        SELECT dedupe_key, external_id, url, canonical_url, metadata_json
        FROM items
        """
    ).fetchall()
    dedupe_keys: set[str] = set()
    external_ids: set[str] = set()
    urls: set[str] = set()
    for dedupe_key, external_id, url, canonical_url, metadata_json in rows:
        if dedupe_key:
            dedupe_keys.add(str(dedupe_key))
        if external_id:
            external_ids.add(str(external_id))
        for candidate in [url, canonical_url]:
            normalized = normalize_url_key(str(candidate or ""))
            if normalized:
                urls.add(normalized)
        if metadata_json:
            try:
                parsed = json.loads(metadata_json)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                for key in ["status_url", "article_url", "external_article_url", "discovered_status_url"]:
                    normalized = normalize_url_key(str(parsed.get(key, "")))
                    if normalized:
                        urls.add(normalized)
    return SeenItemLookup(dedupe_keys=dedupe_keys, external_ids=external_ids, urls=urls)


def recent_items(connection: sqlite3.Connection, limit: int = 10) -> list[dict[str, str | None]]:
    rows = connection.execute(
        """
        SELECT dedupe_key, title, author, url, route_bucket, expansion_status, score_json, published_at
        FROM items
        ORDER BY published_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    result: list[dict[str, str | None]] = []
    for dedupe_key, title, author, url, route_bucket, expansion_status, score_json, published_at in rows:
        score = None
        verdict = None
        if score_json:
            parsed = json.loads(score_json)
            score = str(parsed.get("total_score"))
            verdict = parsed.get("verdict")
        result.append(
            {
                "dedupe_key": dedupe_key,
                "title": title,
                "author": author,
                "url": url,
                "route_bucket": route_bucket,
                "expansion_status": expansion_status,
                "score": score,
                "verdict": verdict,
                "published_at": published_at,
            }
        )
    return result


def get_author_trust(connection: sqlite3.Connection, author: str) -> int:
    row = connection.execute(
        "SELECT trust_score FROM author_trust WHERE lower(author) = lower(?)",
        (author,),
    ).fetchone()
    return int(row[0]) if row else 0


def upsert_item_feedback(connection: sqlite3.Connection, dedupe_key: str, feedback: str, note: str | None = None) -> None:
    connection.execute(
        """
        INSERT INTO item_feedback (dedupe_key, feedback, note, created_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(dedupe_key) DO UPDATE SET
            feedback=excluded.feedback,
            note=excluded.note,
            created_at=datetime('now')
        """,
        (dedupe_key, feedback, note),
    )
    connection.commit()


def apply_author_feedback(connection: sqlite3.Connection, author: str, feedback: str) -> None:
    current = connection.execute(
        "SELECT trust_score, wins, losses FROM author_trust WHERE lower(author) = lower(?)",
        (author,),
    ).fetchone()
    trust_score, wins, losses = (0, 0, 0) if current is None else (int(current[0]), int(current[1]), int(current[2]))
    if feedback == "up":
        trust_score += 1
        wins += 1
    elif feedback == "down":
        trust_score -= 1
        losses += 1
    connection.execute(
        """
        INSERT INTO author_trust (author, trust_score, wins, losses, last_feedback_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(author) DO UPDATE SET
            trust_score=excluded.trust_score,
            wins=excluded.wins,
            losses=excluded.losses,
            last_feedback_at=datetime('now')
        """,
        (author, trust_score, wins, losses),
    )
    connection.commit()


def find_item_by_dedupe_key(connection: sqlite3.Connection, dedupe_key: str) -> dict[str, str] | None:
    row = connection.execute(
        "SELECT dedupe_key, author, title FROM items WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    if not row:
        return None
    return {"dedupe_key": row[0], "author": row[1], "title": row[2]}


def recent_trust(connection: sqlite3.Connection, limit: int = 20) -> list[dict[str, int | str | None]]:
    rows = connection.execute(
        """
        SELECT author, trust_score, wins, losses, last_feedback_at
        FROM author_trust
        ORDER BY trust_score DESC, wins DESC, last_feedback_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "author": row[0],
            "trust_score": int(row[1]),
            "wins": int(row[2]),
            "losses": int(row[3]),
            "last_feedback_at": row[4],
        }
        for row in rows
    ]


def ensure_item_columns(connection: sqlite3.Connection) -> None:
    existing = {row[1] for row in connection.execute("PRAGMA table_info(items)").fetchall()}
    required_columns = {
        "external_id": "ALTER TABLE items ADD COLUMN external_id TEXT NOT NULL DEFAULT ''",
        "canonical_url": "ALTER TABLE items ADD COLUMN canonical_url TEXT NOT NULL DEFAULT ''",
        "route_bucket": "ALTER TABLE items ADD COLUMN route_bucket TEXT NOT NULL DEFAULT 'broad_discovery'",
        "discovered_via": "ALTER TABLE items ADD COLUMN discovered_via TEXT NOT NULL DEFAULT 'unknown'",
        "body_source": "ALTER TABLE items ADD COLUMN body_source TEXT NOT NULL DEFAULT 'preview_text'",
        "expansion_status": "ALTER TABLE items ADD COLUMN expansion_status TEXT NOT NULL DEFAULT 'pending'",
        "expansion_strategy": "ALTER TABLE items ADD COLUMN expansion_strategy TEXT",
        "resolution_status": "ALTER TABLE items ADD COLUMN resolution_status TEXT NOT NULL DEFAULT 'preview'",
        "resolution_reason": "ALTER TABLE items ADD COLUMN resolution_reason TEXT",
        "article_validated": "ALTER TABLE items ADD COLUMN article_validated INTEGER NOT NULL DEFAULT 0",
        "canonical_author": "ALTER TABLE items ADD COLUMN canonical_author TEXT NOT NULL DEFAULT ''",
        "discovered_by_author": "ALTER TABLE items ADD COLUMN discovered_by_author TEXT NOT NULL DEFAULT ''",
        "metadata_json": "ALTER TABLE items ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
    }
    for column, statement in required_columns.items():
        if column not in existing:
            connection.execute(statement)
    backfill_external_ids(connection)


def backfill_external_ids(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT dedupe_key, source_kind, url, canonical_url, external_id
        FROM items
        WHERE external_id = '' OR external_id IS NULL
        """
    ).fetchall()
    for dedupe_key, source_kind, url, canonical_url, external_id in rows:
        if external_id:
            continue
        if source_kind in {"x_post", "x_article"}:
            value = extract_x_external_id(str(dedupe_key or ""), str(url or ""), str(canonical_url or ""))
        else:
            value = str(canonical_url or url or "")
        if value:
            connection.execute("UPDATE items SET external_id = ? WHERE dedupe_key = ?", (value, dedupe_key))
    connection.commit()


def extract_x_external_id(dedupe_key: str, url: str, canonical_url: str) -> str:
    for candidate in [dedupe_key.split(":", 1)[1] if ":" in dedupe_key else "", url, canonical_url]:
        parsed = parse.urlparse(candidate)
        parts = [segment for segment in parsed.path.split("/") if segment]
        if "status" in parts:
            status_index = parts.index("status")
            if status_index + 1 < len(parts):
                return parts[status_index + 1]
        if candidate.isdigit():
            return candidate
    return ""


def normalize_url_key(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    parsed = parse.urlparse(stripped)
    if parsed.scheme not in {"http", "https"}:
        return stripped
    path = parsed.path.rstrip("/") or "/"
    return parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def has_output_delivery(connection: sqlite3.Connection, dedupe_key: str, channel: str, date_bucket: str = "") -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM item_outputs
        WHERE dedupe_key = ? AND channel = ? AND date_bucket = ?
        """,
        (dedupe_key, channel, date_bucket),
    ).fetchone()
    return row is not None


def record_output_delivery(connection: sqlite3.Connection, dedupe_key: str, channel: str, date_bucket: str = "") -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO item_outputs (dedupe_key, channel, date_bucket)
        VALUES (?, ?, ?)
        """,
        (dedupe_key, channel, date_bucket),
    )
    connection.commit()
