# signal_cards fixture notes

This directory contains frontend contract fixtures for local preview and file-mode fallback.

Production cards are generated on the server by `tools/materialize_signal_cards.py` from:

```text
/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl
/opt/signal-audit-tools/signal_llm_reviews.jsonl
```

Do not treat the committed `index.json`, `*.json`, or `fallback.js` files here as the live server data set. They are kept so the static frontend can be opened and tested before a server JSONL exists.
