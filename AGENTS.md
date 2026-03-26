# AGENTS.md

## Project
Build `x_signal_engine`, a focused Python system that discovers high-signal AI engineering content from X and official AI company sources, scores it for implementation value, sends the best items to Telegram, and appends accepted items to markdown notes.

## Primary goal
Reduce overwhelm. This project is not a generic social scraper. It is a signal refinery for AI engineering workflows.

## What counts as high signal
Prefer content with:
- concrete implementation steps
- commands, code, architecture, benchmarks, or failure analysis
- workflow improvements for coding agents, inference, RAG, evals, deployment, tools, or developer productivity
- technical specificity over general opinion
- novel patterns that can be tested within a few hours

Penalize:
- hype
- vague futurism
- generic tool roundups
- personal branding threads with little implementation detail
- repeated consensus takes with no new mechanism

## Source priority
Always give extra weight to:
- official posts from major AI labs and companies
- trusted technical creators
- content directly relevant to coding agents, inference, RAG, evals, deployment, or terminal workflows

## Ingestion rules
Ingest an item if any of the following are true:
- views >= 10000
- bookmarks >= 100
- author is in `priority_authors`
- source is in `priority_companies`
- content matches `priority_topics`

Do not assume engagement equals quality. Score all ingested items.

## Required outputs
For each accepted item, produce:
- normalized title
- author or source
- URL
- short summary
- why it matters
- one concrete action to try
- tags
- final score
- decision: `ignore` | `store` | `digest` | `alert` | `alert_and_experiment`

## Delivery rules
Telegram alerts are only for top-scoring items. Markdown files should be append-only and readable by a human. Keep summaries concise and implementation-focused.

## Engineering style
- Use Python.
- Use `uv`.
- Start with SQLite.
- Prefer simple, skimmable code.
- Keep files short.
- Use type hints.
- Log clearly.
- Add dry-run mode.
- Make failures obvious.
- Avoid unnecessary abstractions.
- Do not build a giant multi-agent framework in v1.

## Architecture preferences
Use separate modules for:
- ingestion
- normalization
- scoring
- storage
- markdown output
- Telegram output
- scheduled jobs

## LLM usage rules
Use LLMs for:
- scoring
- summarization
- experiment generation
- digest synthesis

Do not use LLMs for:
- basic dedupe
- simple thresholds
- raw data plumbing
- deterministic routing that should be code

## Scoring philosophy
Popularity is a weak signal. Authority is a useful signal. Implementation detail and actionability are the strongest signals.

## What to optimize for
Optimize for:
1. usefulness to the real workflow
2. low-noise alerts
3. markdown knowledge compounding over time
4. easy extension later

## v1 boundary
Build only:
- ingestion from curated X accounts and official source feeds or pages
- normalization and storage
- scoring
- Telegram alerts
- markdown append
- daily digest

Do not build:
- autonomous coding swarm
- browser fleet
- self-modifying agent loops
- complicated orchestration
- heavy distributed systems

## Task behavior
When asked to implement:
1. propose the minimal architecture
2. list exact files
3. implement the smallest useful vertical slice
4. explain tradeoffs briefly
5. keep code practical
