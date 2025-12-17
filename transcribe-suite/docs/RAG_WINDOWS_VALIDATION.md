# Windows / NAS validation report

Validation performed on Windows 11 (PowerShell) inside this repo to mimic long UNC-style paths, accents, and SQLite usage.

## Scenario

- Copied the sample work dir to `work\Démo NAS Éléphant` (contains spaces + accent).
- Invoked the CLI with the Win32 long-path prefix: `\\?\D:\02_dev\scripts\transcribe-suite\transcribe-suite\work\Démo NAS Éléphant`.
- Forced output into a dedicated tag (`--version-tag windows_nas`) to avoid clashing with local runs.

## Commands executed

```powershell
$env:PYTHONPATH="src"
python -m rag_export.cli --input "\\?\D:\...\work\Démo NAS Éléphant" --config config/rag.yaml --version-tag windows_nas --force
python -m rag_export.cli doctor --input "RAG/RAG-demo-nas-elephant_927403ae/windows_nas" --config config/rag.yaml
python -m rag_export.cli query  --input "RAG/RAG-demo-nas-elephant_927403ae/windows_nas" --config config/rag.yaml --query installation --top-k 3
@'
from pathlib import Path
Path('RAG/RAG-demo-nas-elephant_927403ae/windows_nas/segments.jsonl').read_text(encoding='utf-8')
Path('RAG/RAG-demo-nas-elephant_927403ae/windows_nas/chunks.jsonl').read_text(encoding='utf-8')
'@ | python
```

All commands returned `0`, `rag doctor` reported coverage OK, and `rag query` surfaced the single chunk with its citation (`Démo NAS Éléphant [00:00-00:26]`).

## Findings

- **Path resolution**: `InputResolver` handled both the accentuated folder and the `\\?\` prefix; doc_id slugged to `demo-nas-elephant_927403ae`.
- **UTF-8**: re-reading `segments.jsonl` / `chunks.jsonl` with `encoding="utf-8"` succeeded (no BOM, no decoding errors).
- **lexical.sqlite**: built successfully and answered the smoke query via `rag query` (score `-0.0000` with timestamps + citation).
- **doctor**: confirmed required files, coverage, and FTS5 availability with the same config used on macOS.

## Edge cases & mitigations

| Topic | Observation | Mitigation |
| --- | --- | --- |
| UNC share rights | The `\\?\` prefix works for local disks; real NAS shares (`\\server\share`) still rely on Windows credentials. | Documented in README: run `bin\run.bat rag ...` from a session that already has access to the share. |
| Long paths | Without the `\\?\` prefix PowerShell truncates >260 char paths. | Keep using the prefix or enable `LongPathsEnabled=1` in Windows policy. |
| SQLite locks | Running `rag query` while a share is read-only works because the command only opens the DB in read mode. | No change required; just avoid opening the DB in other apps with write locks. |

Outputs were deleted after the test; rerun the commands above to reproduce.
