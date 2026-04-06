# tired-of-keeping-up

`x_signal_engine` is a Python system for finding AI engineering articles worth reading, where X is the discovery surface and expanded article text is the thing that gets ranked.

Current slice:
- broad X discovery via `twscrape`
- official company/newsroom ingestion
- source routing into `official_company`, `trusted_creator`, and `broad_discovery`
- external article expansion for linked blog posts shared on X
- browser-backed X expansion through the Playwright CLI, starting from the tweet status page and following built-in X Article entry points when present
- article validation before scoring
- OpenRouter-backed scoring with deterministic fallback
- storage-backed seen-item checkpointing so old items stop re-alerting and re-appending
- SQLite storage with feedback-based author trust
- top-3 Telegram morning digest
- markdown append outputs
- `uv`-managed CLI

## Run

```bash
uv run x-signal-engine --dry-run
```

Live discovery from X plus official sources:

```bash
uv run x-signal-engine --dry-run --live-x --limit-per-query 5
```

Live discovery with browser expansion diagnostics:

```bash
uv run x-signal-engine --dry-run --live-x --limit-per-query 5 --expansion-debug
```

Show recent stored items:

```bash
uv run x-signal-engine --show-items 10
```

## Configure

Copy `.env.example` into your preferred environment file or export the variables directly before running. The current slice expects:
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional:
- `TWS_ACCOUNT_USERNAME`
- `TWS_ACCOUNT_PASSWORD`
- `TWS_ACCOUNT_EMAIL`
- `TWS_ACCOUNT_EMAIL_PASSWORD`
- `TWS_ACCOUNT_COOKIES`
- `TWS_PROXY`
- `TWS_ACCOUNTS_DB_PATH`
- `X_SEARCH_QUERIES`
- `X_MIN_LIKES`
- `X_EXPAND_CANDIDATES_PER_QUERY`
- `X_QUERY_SINCE_DAYS`
- `X_SEARCH_MINUTES`
- `X_SEARCH_POLL_SECONDS`
- `PRIORITY_TOPICS`
- `OFFICIAL_SOURCE_LIMIT`
- `OFFICIAL_ARTICLE_MIN_CHARS`
- `BROWSER_EXPAND_X`
- `PLAYWRIGHT_CLI_PATH`
- `PLAYWRIGHT_HEADED`
- `PLAYWRIGHT_TIMEOUT_MS`
- `OPENCLAW_WEBHOOK_URL`
- `OPENCLAW_AUTH_TOKEN`

## What You Need To Provide

- An OpenRouter model name in `OPENROUTER_MODEL`.
- Your OpenRouter API key.
- A Telegram bot token and target chat ID.
- For X discovery with `twscrape`: preferably `TWS_ACCOUNT_COOKIES`, or a full login plus email verification credentials.
- For browser-backed X article expansion: a working Playwright CLI wrapper path. The default points at `~/.codex/skills/playwright/scripts/playwright_cli.sh`.
- Only if you want a relay layer instead of direct Telegram delivery: your OpenClaw webhook URL and auth token.

## Architecture

The pipeline is intentionally split into separate layers:
- discovery
- routing
- expansion
- scoring
- storage
- digest generation
- feedback/trust

Scoring is article-centric:
- X search results are candidate pointers, not final content.
- Official articles are fetched directly and normalized as canonical content.
- Shortlisted X candidates are expanded in a real browser before they are ranked whenever browser expansion is enabled.
- For X-native articles, the browser expansion now starts on the tweet status page, looks for the in-product article view you would normally click into, and falls back to the direct `x.com/i/article/...` URL only when needed.
- Telegram is selective: only the final top-3 digest is sent, and only when the editorial bar is met.

## Notes

The sample path exists so storage, scoring, notes, and digest formatting can be exercised without live network calls.

Live behavior now biases toward expanded long-form material:
- official company sources bypass popularity thresholds
- trusted creators get priority for expansion, not automatic acceptance
- broad-discovery X candidates must be article-like, not just relevant posts
- X candidates are locally gated by views, bookmarks, likes, or explicit priority matches
- linked external articles are fetched directly when a tweet points to a real writeup
- built-in X Articles now open from the tweet status page first, then follow the in-product article view or fall back to `x.com/i/article/...` when the page metadata is clearer than the DOM
- already-stored items are treated as seen and skipped before they can consume digest or markdown budget
- preview-only items are penalized relative to expanded canonical articles
- non-dry runs send only the final morning digest, not every high-scoring item

To sweep X for a longer window before scoring:

```bash
uv run x-signal-engine --dry-run --live-x --limit-per-query 5 --search-minutes 30
```
