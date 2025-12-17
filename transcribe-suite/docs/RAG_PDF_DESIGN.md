# RAG export (PDF) - design memo

This note outlines how to extend `rag-export` to PDF inputs while preserving deterministic, versioned artefacts.

## Goals

- Produce the same artefacts as for video/audio runs: `document.json`, `segments.jsonl`, `chunks.jsonl`, `chunks_for_llm.jsonl`, `quality.json`, `lexical.sqlite`, `README_RAG.md`.
- Keep byte-level determinism, provenance hashes, and config snapshots.
- Support citations without timestamps by introducing a generic `locator`.

## Inputs and resolver

1. `rag-export --input "docs/MyReport.pdf"` points to either the PDF itself or a prepared `work/<doc>` folder.
2. A pre-processing step (outside this scope) extracts text segments with page offsets, producing `pdf_segments.jsonl`.
3. The resolver prefers that file as the `segments` source; chunking, stats, and SQLite reuse the existing logic.

## Locator schema

All chunks will eventually expose a `locator` block so downstream systems can treat timecodes and page ranges uniformly:

```jsonc
{
  "chunk_id": "abc123",
  "start": 12.34,
  "end": 45.67,
  "locator": {
    "type": "time",      // "time" for media, "page" for PDF
    "start": 12.34,
    "end": 45.67,
    "unit": "seconds"    // "seconds" or "page"
  }
}
```

- Video/audio exports keep their current timestamps; `locator` simply mirrors them (`type=time`, `unit=seconds`).
- PDF exports reuse the same fields but `type=page`, `unit=page`, and `start`/`end` hold page numbers (allowing decimals for intra-page offsets).
- Citation templates (`text`, `markdown`, `url`) rely on `locator` to inject either time parameters (`?t=...`) or page parameters (`?page=...`).

## Manifest & provenance

- `document.json` keeps the same schema; the resolver only changes `sources` to point at the PDF-derived segments.
- `provenance` gains `sha256` hashes for the PDF and any intermediate `pdf_segments.jsonl`.
- `deterministic_mode`, `timestamps_policy`, and `config_effective` remain mandatory so re-generation stays reproducible.

## Chunking, quality, SQLite

- Chunk sizing still uses a word-count target (~300 words) with overlap; there is no PDF-specific logic besides locator formatting.
- `quality.json` keeps the coverage metric; on PDF inputs the timeline corresponds to page span (coverage = (last_page - first_page) / total_pages).
- `lexical.sqlite` is identical; FTS queries ignore the locator type so `rag query` works for both media and PDF.

## Backward compatibility

- `locator` is optional for older exports; once PDF support lands, video exports will also include it to ease migrations.
- Existing consumers that only read `start`/`end` continue to work because those fields stay populated.

## Next steps

1. Ship a PDF segment extractor (likely `tools/pdf_to_segments.py`) that writes deterministic JSONL + metrics.
2. Update chunk generation to attach `locator`.
3. Extend `rag doctor` to validate `locator` consistency (type, monotonicity, coverage).
4. Document the PDF workflow in the README and add fixtures + pytest coverage for a minimal PDF sample.
