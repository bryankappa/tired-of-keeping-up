from __future__ import annotations

from html import escape
import json
from urllib import parse, request

from x_signal_engine.models import ScoredItem


def send_telegram_message(bot_token: str, chat_id: str, message: str, parse_mode: str = "HTML") -> dict[str, object]:
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    encoded = parse.urlencode(payload).encode("utf-8")
    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    http_request = request.Request(endpoint, data=encoded, method="POST")
    with request.urlopen(http_request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def format_telegram_alert(scored: ScoredItem) -> dict[str, str]:
    title = escape(scored.item.title)
    author = escape(scored.item.author)
    summary = escape(scored.short_summary)
    action = escape(scored.concrete_takeaway)
    url = escape(scored.item.canonical_url)
    surfaced_by = (
        f"\nSurfaced by: @{escape(scored.item.discovered_by_author)}"
        if scored.item.discovered_by_author
        else ""
    )
    html = (
        "<b>High-Signal X Article</b>\n"
        f"<a href=\"{url}\">{title}</a>\n"
        f"Author: {author}{surfaced_by}\n"
        f"Score: {scored.total_score}\n"
        f"Why: {escape(scored.why_this_article)}\n"
        f"Summary: {summary}\n"
        f"Try: {action}"
    )
    plain_text = (
        "High-Signal X Article\n"
        f"{scored.item.title}\n"
        f"Author: {scored.item.author}"
        f"{f' | Surfaced by @{scored.item.discovered_by_author}' if scored.item.discovered_by_author else ''}\n"
        f"Score: {scored.total_score}\n"
        f"Why: {scored.why_this_article}\n"
        f"Summary: {scored.short_summary}\n"
        f"Try: {scored.concrete_takeaway}\n"
        f"Read: {scored.item.canonical_url}"
    )
    return {"html": html, "plain_text": plain_text}


def format_telegram_digest(items: list[ScoredItem]) -> dict[str, str]:
    selected = items
    html_lines = ["<b>Morning AI Engineering Digest</b>"]
    text_lines = ["Morning AI Engineering Digest"]
    for index, scored in enumerate(selected, start=1):
        title = escape(scored.item.title)
        why = escape(scored.short_summary)
        action = escape(scored.concrete_takeaway)
        source = escape(scored.item.author)
        url = escape(scored.item.canonical_url)
        html_lines.append(f"{index}. <a href=\"{url}\">{title}</a>")
        html_lines.append(f"Author: {source}")
        if scored.item.discovered_by_author:
            html_lines.append(f"Surfaced by: @{escape(scored.item.discovered_by_author)}")
        html_lines.append(f"Score: {scored.total_score}")
        html_lines.append(f"Summary: {why}")
        html_lines.append(f"Try: {action}")
        text_lines.append(f"{index}. {scored.item.title}")
        text_lines.append(f"Author: {scored.item.author}")
        if scored.item.discovered_by_author:
            text_lines.append(f"Surfaced by @{scored.item.discovered_by_author}")
        text_lines.append(f"Score: {scored.total_score}")
        text_lines.append(f"Summary: {scored.short_summary}")
        text_lines.append(f"Try: {scored.concrete_takeaway}")
        text_lines.append(f"Read: {scored.item.canonical_url}")
    return {"html": "\n".join(html_lines), "plain_text": "\n".join(text_lines)}
