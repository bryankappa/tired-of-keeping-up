---
name: x-ingest
description: Ingest X posts and articles from curated creators, normalize them, and survive source breakage.
---

# Purpose
Pull candidate items from curated X sources and normalize them into a stable internal item shape.

# Responsibilities
- pull from curated creators
- extract post or article text
- detect duplicates
- checkpoint last seen items
- fall back when X layout changes

# Rules
- Expect scraping breakage, throttling, and account risk.
- Prefer stable adapters and checkpoints over brittle one-shot extraction.
- Treat official company posts as first-class inputs.
