from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json

from x_signal_engine.config import load_settings
from x_signal_engine.ingest import ingest_live_x, sample_ingest
from x_signal_engine.markdown_output import append_markdown
from x_signal_engine.models import NormalizedItem
from x_signal_engine.scoring import load_score_prompt, rerank_digest_candidates, score_item
from x_signal_engine.sources import hardcoded_sources
from x_signal_engine.storage import (
    apply_author_feedback,
    connect,
    count_items,
    find_item_by_dedupe_key,
    load_seen_item_lookup,
    has_output_delivery,
    record_output_delivery,
    recent_items,
    recent_trust,
    upsert_item,
    upsert_item_feedback,
)
from x_signal_engine.telegram import format_telegram_alert, format_telegram_digest, send_telegram_message
from x_signal_engine.routing import is_digest_worthy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the x_signal_engine vertical slice.")
    parser.add_argument("--dry-run", action="store_true", help="Print outputs without sending alerts.")
    parser.add_argument("--live-x", action="store_true", help="Use live discovery from X plus official source ingestion.")
    parser.add_argument(
        "--expansion-debug",
        action="store_true",
        help="Include per-item expansion diagnostics for X/browser article extraction.",
    )
    parser.add_argument("--limit-per-query", type=int, default=20, help="Max items to request per X query.")
    parser.add_argument("--search-minutes", type=int, help="Keep sweeping X for up to N minutes before scoring.")
    parser.add_argument("--show-items", type=int, metavar="N", help="Show the most recent N stored items and exit.")
    parser.add_argument("--feedback-item", metavar="DEDUPE_KEY", help="Record feedback for a stored item.")
    parser.add_argument("--feedback", choices=["up", "down"], help="Feedback value for --feedback-item.")
    parser.add_argument("--show-trust", type=int, metavar="N", help="Show the top N author trust records and exit.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = load_settings()
    connection = connect(settings.db_path)
    if args.feedback_item:
        if not args.feedback:
            raise SystemExit("--feedback is required when using --feedback-item")
        item = find_item_by_dedupe_key(connection, args.feedback_item)
        if item is None:
            raise SystemExit(f"Unknown dedupe_key: {args.feedback_item}")
        upsert_item_feedback(connection, args.feedback_item, args.feedback)
        apply_author_feedback(connection, item["author"], args.feedback)
        print(json.dumps({"updated": item, "feedback": args.feedback}, indent=2))
        return
    if args.show_trust:
        print(json.dumps({"trust": recent_trust(connection, limit=args.show_trust)}, indent=2))
        return
    if args.show_items:
        print(json.dumps({"items": recent_items(connection, limit=args.show_items)}, indent=2))
        return

    sources = hardcoded_sources(settings)
    existing_item_count = count_items(connection)
    seen_lookup = load_seen_item_lookup(connection)
    search_minutes = args.search_minutes if args.search_minutes is not None else 0
    items = (
        ingest_live_x(
            settings,
            sources=sources,
            limit_per_query=args.limit_per_query,
            seen_lookup=seen_lookup,
            search_minutes=search_minutes,
        )
        if args.live_x
        else sample_ingest(sources)
    )
    items = [item for item in items if not seen_lookup.matches(item)]
    score_prompt = load_score_prompt(settings.score_prompt_path)

    digest_payload: dict[str, str] | None = None
    telegram_results: list[dict[str, object]] = []
    scored_preview: list[dict[str, object]] = []
    digest_candidates = []
    for item in items:
        scored = score_item(item, score_prompt, settings=settings)
        if not args.dry_run:
            upsert_item(connection, item, scored)
        scored_preview.append(
            {
                "title": scored.item.title,
                "author": scored.item.author,
                "canonical_author": scored.item.canonical_author,
                "discovered_by_author": scored.item.discovered_by_author,
                "score": scored.total_score,
                "verdict": scored.verdict.value,
                "source_kind": scored.item.source_kind.value,
                "article_validated": scored.item.article_validated,
                "route_bucket": scored.item.route_bucket,
                "body_source": scored.item.body_source,
                "resolution_status": scored.item.resolution_status,
                "url": scored.item.canonical_url,
            }
        )
        if not args.dry_run:
            append_markdown(
                connection,
                scored,
                high_signal_path=settings.high_signal_feed_path,
                workflow_path=settings.workflow_upgrades_path,
                experiments_path=settings.experiments_path,
            )
            if (
                settings.telegram_bot_token
                and settings.telegram_chat_id
                and is_immediate_alert_candidate(scored)
                and not has_output_delivery(connection, scored.item.dedupe_key, "telegram_alert")
            ):
                alert_payload = format_telegram_alert(scored)
                telegram_results.append(
                    {
                        "channel": "telegram_alert",
                        "result": send_telegram_message(
                            bot_token=settings.telegram_bot_token,
                            chat_id=settings.telegram_chat_id,
                            message=alert_payload["html"],
                            parse_mode=settings.telegram_parse_mode,
                        ),
                    }
                )
                record_output_delivery(connection, scored.item.dedupe_key, "telegram_alert")
        if is_digest_worthy(scored):
            digest_candidates.append(scored)
    digest_candidates = rerank_digest_candidates(digest_candidates, settings)
    if digest_candidates:
        digest_payload = format_telegram_digest(digest_candidates)
    if not args.dry_run and digest_payload and settings.telegram_bot_token and settings.telegram_chat_id:
        digest_bucket = datetime.now(timezone.utc).date().isoformat()
        unsent_digest_items = [
            scored
            for scored in digest_candidates
            if not has_output_delivery(connection, scored.item.dedupe_key, "telegram_digest", digest_bucket)
        ]
        if unsent_digest_items:
            telegram_results.append(
                {
                    "channel": "telegram_digest",
                    "result": send_telegram_message(
                        bot_token=settings.telegram_bot_token,
                        chat_id=settings.telegram_chat_id,
                        message=format_telegram_digest(unsent_digest_items)["html"],
                        parse_mode=settings.telegram_parse_mode,
                    ),
                }
            )
            for scored in unsent_digest_items:
                record_output_delivery(connection, scored.item.dedupe_key, "telegram_digest", digest_bucket)

    output = {
        "dry_run": args.dry_run,
        "live_x": args.live_x,
        "search_minutes": search_minutes,
        "items_processed": len(items),
        "existing_item_count": existing_item_count,
        "article_candidates": sum(1 for item in items if item.source_kind.value in {"x_article", "official_article"}),
        "validated_articles": sum(1 for item in items if item.article_validated),
        "stored_items": count_items(connection),
        "scored_preview": scored_preview[:10],
        "digest_preview": digest_payload,
        "telegram_results": telegram_results,
        "db_path": str(settings.db_path),
        "markdown_paths": [
            str(settings.high_signal_feed_path),
            str(settings.workflow_upgrades_path),
            str(settings.experiments_path),
        ],
        "missing_config": missing_config(settings),
    }
    if args.expansion_debug:
        output["expansion_debug"] = build_expansion_debug(items)
    print(json.dumps(output, indent=2))


def build_expansion_debug(items: list[NormalizedItem]) -> list[dict[str, object]]:
    debug_rows: list[dict[str, object]] = []
    for item in items:
        debug_rows.append(
            {
                "title": item.title,
                "author": item.author,
                "source_kind": item.source_kind.value,
                "canonical_author": item.canonical_author,
                "discovered_by_author": item.discovered_by_author,
                "body_source": item.body_source,
                "article_validated": item.article_validated,
                "expansion_status": item.expansion_status,
                "expansion_strategy": item.expansion_strategy,
                "expansion_mode": item.metadata.get("expansion_mode", ""),
                "resolution_status": item.resolution_status,
                "resolution_reason": item.resolution_reason,
                "status_url": item.metadata.get("status_url", item.url),
                "discovered_status_url": item.metadata.get("discovered_status_url", ""),
                "article_url": item.metadata.get("article_url", ""),
                "external_article_url": item.metadata.get("external_article_url", ""),
                "discovered_article_url": item.metadata.get("discovered_article_url", ""),
                "current_url": item.metadata.get("current_url", item.canonical_url),
                "canonical_url": item.canonical_url,
                "body_chars": len(item.body),
            }
        )
    return debug_rows


def missing_config(settings: object) -> list[str]:
    missing: list[str] = []
    for field_name in [
        "openrouter_api_key",
        "openrouter_model",
        "telegram_bot_token",
        "telegram_chat_id",
    ]:
        if not getattr(settings, field_name):
            missing.append(field_name)
    return missing


def is_immediate_alert_candidate(scored: object) -> bool:
    return (
        getattr(scored, "verdict", None) is not None
        and scored.item.discovered_via.startswith("x_")
        and scored.item.body_source == "expanded_article"
        and scored.verdict.value in {"alert", "alert_and_experiment"}
    )


if __name__ == "__main__":
    main()
