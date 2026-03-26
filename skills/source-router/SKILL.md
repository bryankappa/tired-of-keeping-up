---
name: source-router
description: Route sources by authority, trust, popularity threshold, and topical relevance before deeper processing.
---

# Purpose
Decide whether content should enter the expensive scoring path.

# Inputs
- source metadata
- author metadata
- engagement metrics
- topical match

# Decisions
- official lab or company
- trusted creator
- popularity threshold
- topic match

# Rules
- Official company sources bypass social thresholds.
- Trusted creators can bypass thresholds if relevance is high.
- Popularity alone is not enough for final acceptance.
