# RAG Lexicon Workflow (Phase 1.5)

1. **ASR batch** (`bin\pipeline_asr_batch.bat`) produces `02_output_source\asr\<doc>\work\<doc>` and `TRANSCRIPT - <doc>`.
2. **Lexicon scan** (`bin\pipeline_lexicon_batch.bat --scan-only` or `/api/v1/run/lexicon-*`) analyses the clean/polish layers and generates `rag.glossary.suggested.yaml` (never applied automatically).
3. **Human validation** happens in the Control Room UI: inspect suggestions, edit/disable rules, preview regexes with timeout safeguards, then persist `rag.glossary.yaml`. Saving is atomic and produces timestamped backups + `.lexicon_ok.json` stamps (hash of the source file used during scan).
4. **Lexicon apply** (`bin\pipeline_lexicon_batch.bat --apply` or `/api/v1/run/lexicon-apply`) promotes the validated glossary and refreshes the stamp.
5. **RAG export** runs from the validated state (`rag.glossary.yaml` present + `.lexicon_ok.json` matching). `bin\pipeline_rag_batch.bat` (or `bin\run.bat rag ...`) is the only component allowed to create artifacts in `03_output_RAG`.

Guidelines:

- Suggested files are never applied automatically; only the validated glossary + stamp are honoured by `rag` exports.
- Doc IDs are resolved via the resolver; API calls only accept `doc` slugs (no raw paths).
- Profiles (`control_room/profiles.yaml`) define the default arguments per action (ASR/Lexicon/RAG) and are exposed via `/api/v1/profiles` so operators can pick the right preset before launching a job.
