# Transcribe Control Room

FastAPI + React control plane used to supervise the NAS pipeline (`\\bricesodini\Savoirs\03_data_pipeline`). All ASR / Lexicon / RAG jobs are still executed by the Transcribe Suite scripts; the control room only orchestrates them (whitelist of batch commands) and exposes telemetry to any browser on the LAN.

## Architecture

```
control_room/
├── backend/
│   ├── app.py          # FastAPI routes + dependency wiring (/api/v1/*)
│   ├── commands.py     # Whitelisted batch commands + dry-run previews
│   ├── docs.py         # Document state machine + glossary helpers
│   ├── errors.py       # Failure taxonomy (UNC, token, mojibake…)
│   ├── profiles.py     # YAML presets for ASR / Lexicon / RAG profiles
│   ├── preview.py      # Regex preview with timeout/diff
│   ├── resolver.py     # Secure doc_id → work_dir resolution
│   ├── runner.py       # Job queue, locks, SQLite persistence
│   └── requirements.txt
└── frontend/           # React + Vite dashboard
    ├── package.json
    └── src/ (Dashboard / Docs / Jobs pages)
```

Job metadata are persisted in `control_room/backend/jobs.db` (SQLite) and logs in `control_room/backend/job_logs/`. All commands run inside the Transcribe Suite venv via `bin\run.bat` or the batch pipelines (`bin\pipeline_*.bat`).

## Backend setup

1. Copy `.env.example` to `.env.local` (or export the variables) and adjust:
   - `DATA_PIPELINE_ROOT`, `TS_REPO_ROOT`, `TS_VENV_DIR`, `TS_RUN_BAT_PATH`
   - `CONTROL_ROOM_API_KEY`, `CONTROL_ROOM_MAX_WORKERS`, etc.
2. Activate the existing Transcribe Suite virtualenv (`.venv`).  
3. Install the backend deps:
   ```powershell
   cd D:\02_dev\scripts\transcribe-suite
   .venv\Scripts\activate
   pip install -r control_room\backend\requirements.txt
   ```
4. Optional LAN API key:
   ```powershell
   setx CONTROL_ROOM_API_KEY "super-secret"
   ```
   When defined, every `/api/*` request must include `X-API-KEY`, and WebSockets must send the same header (no query token).

### Launch

Use the helper batch to keep the binding logic consistent with the Transcribe Suite venv:

```powershell
bin\control_room_start.bat          # binds to 127.0.0.1:8787 (default)
bin\control_room_start.bat --lan    # binds to 0.0.0.0:8787
```

The script injects `TS_VENV_DIR` and `DATA_PIPELINE_ROOT` so that every subordinate command runs inside the correct environment. For a manual launch: `uvicorn control_room.backend.app:app --host 127.0.0.1 --port 8787`.

## Workflow nominal

1. **ASR batch** (`bin\pipeline_asr_batch.bat`) : produit `02_output_source\asr\<doc>\work\<doc>` + `TRANSCRIPT - <doc>`.
2. **Lexicon scan** (`bin\pipeline_lexicon_batch.bat --scan-only` ou `/api/v1/run/lexicon-scan`) : génère `rag.glossary.suggested.yaml` sans jamais l’appliquer automatiquement.
3. **Revue humaine** (UI Control Room) : éditer/désactiver les règles, prévisualiser les regex, vérifier l’ETag.
4. **Lexicon apply** (`bin\pipeline_lexicon_batch.bat --apply` ou `/api/v1/run/lexicon-apply`) : écrit `rag.glossary.yaml` + `.lexicon_ok.json` de façon atomique avec backup.
5. **RAG export + doctor + query** (`bin\pipeline_rag_batch.bat` ou `/api/v1/run/rag-*`) : consomme uniquement les glossaires validés/stampés pour produire `03_output_RAG\RAG-<doc>` puis exécuter doctor/query.

## Frontend setup

```bash
cd control_room/frontend
npm install
npm run dev      # Vite dev server on 5173, proxying /api to 8787

# Production build (served by FastAPI from /static)
npm run build
```

To access the UI from a Mac on the LAN: open `http://<windows-host>:8787/` (after starting the backend with `--lan` or via a reverse proxy). Windows firewall may need an inbound rule for TCP port 8787.

## Features

- **Versioned API** – `/api/v1/*` responses are wrapped with `{"api_version":"v1", ...}` and every job action exposes a `/dry-run` twin that returns the resolved command (argv + cwd + selected profile) without scheduling it.
- **Dashboard** – counters by doc state (ASR_READY / LEXICON_* / RAG_*), quick actions (ASR batch, lexicon scan/apply) and the 10 latest jobs with duration + failure hints.
- **Documents** – scans `02_output_source\asr\*`, computes the document state machine, surfaces stamps / rag versions and exposes scan/apply/RAG/export doctor/query actions with profile selectors.
- **Document detail** – tabbed inspector (sources, glossary editor with schema validation + atomic saves/backups, preview with timeout/diff/counter, runs + rag query shortcuts).
- **Jobs & logs** – queue with per-doc locks, max worker throttling, cancel endpoint, live WebSocket logs, normalized failure taxonomy (UNC, token missing, mojibake, doc locked, etc.) and actionable hints.
- **Profiles** – `control_room/profiles.yaml` drives the ASR / Lexicon / RAG presets returned by `/api/v1/profiles` and surfaced in the UI.
- **Glossary QA** – YAML schema validation, regex compile checks, maximum size guardrail and timestamped backups before overwriting `rag.glossary.yaml`.

Security guardrails:

- Only whitelisted commands may run (batch pipelines or `bin\run.bat rag ...`). Requests accept doc_id + whitelisted flags, never raw paths; work_dir resolution is centralized.
- Actions requiring writes use per-doc locks plus the global semaphore (`CONTROL_ROOM_MAX_WORKERS`). Read-only jobs (rag doctor/query) skip doc locks but still obey worker throttling.
- WebSockets require the same `X-API-KEY` header as REST; there are no query tokens. Regex previews run in a sandbox with explicit timeouts.

## Tests

1. Installez les dépendances backend + dev :  
   `pip install -r control_room/backend/requirements.txt -r transcribe-suite/requirements-dev.txt`
2. Lancez toute la suite fondations via une seule commande :  
   `pytest tests/control_room`

## Environment variables

| Variable | Purpose |
| --- | --- |
| `DATA_PIPELINE_ROOT` | NAS root (default `\\bricesodini\Savoirs\03_data_pipeline`) |
| `TS_REPO_ROOT`, `TS_VENV_DIR`, `TS_RUN_BAT_PATH` | Locate the Transcribe Suite checkout / venv / `run.bat` |
| `CONTROL_ROOM_API_KEY` | Optional shared secret for REST + WS |
| `CONTROL_ROOM_MAX_WORKERS` | Max concurrent jobs (semaphore) |
| `CONTROL_ROOM_PREVIEW_TIMEOUT_MS` | Regex preview timeout budget |
| `CONTROL_ROOM_PROFILES` | Path to `profiles.yaml` (ASR/Lexicon/RAG presets) |
| `VITE_API_BASE`, `VITE_WS_BASE` | Frontend overrides when colocated behind another proxy |
| `VITE_API_KEY` | Inject the API key into the dev frontend (`VITE_API_KEY=secret npm run dev`) |

## Notes

- All commands (ASR / Lexicon / RAG) are executed inside the Transcribe Suite venv; the control room never runs foreign binaries.
- Failure reasons are normalized (token missing, UNC unreachable, mojibake, etc.) and surfaced in job responses so the UI can suggest remediation steps.
- Logs are downloadable (`/api/jobs/{id}/log/file`) and streamed via WebSocket (`/ws/jobs/{id}`) for quick triage.

Happy transcribing!
