---
name: telegram-format
description: Format compact Telegram alerts for high-signal AI engineering items.
---

# Purpose
Turn a scored item into a concise Telegram message with readable structure and safe fallback formatting.

# Inputs
- normalized title
- author or source
- url
- total score
- why it matters
- concrete takeaway
- verdict

# Output
- compact message body
- parse mode recommendation
- fallback plain-text version

# Rules
- Prefer Telegram HTML for compact structure and links.
- Keep alerts short enough to scan in seconds.
- Include a headline, why it matters, one action, score, and source.
- Fall back to plain text if formatting must be disabled.
