# Changelog

## [Unreleased]

- Introduced schema v1.0.0 for `clean.jsonl`, `chunks.jsonl`, `chunks.meta.json`, `quotes.jsonl`, and `low_confidence.jsonl`, plus `metrics.json`.
- Added `TextNormalizer` to emit paired `text_human` / `text_machine` across clean/polish/structure/chunk stages.
- Stage pipeline now caches via input hashes, supports `--only` / `--dry-run` / `resume`, and emits low-confidence JSONL queues + audit metrics table/sparkline.
- Added `tools/migrate_schema.py` as schema-upgrade entrypoint.
