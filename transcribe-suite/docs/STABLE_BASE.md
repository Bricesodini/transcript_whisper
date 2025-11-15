# Stable Base

Cette page décrit l’empreinte verrouillée utilisée pour rejouer la pipeline **à l’identique** dans 6 mois. Toute évolution future part de ce socle.

## Versions figées

| Composant            | Version | Remarques |
|----------------------|---------|-----------|
| Python               | 3.11.x  | vérifié via `bin/env_check.sh` |
| faster-whisper       | 1.2.1   | backend Apple Accelerate (MPS facultatif) |
| whisperx             | 3.7.4   | modèle align FR |
| pyannote.audio       | 3.4.0   | checkpoint HF pin |
| torch / torchaudio   | 2.8.0   | build arm64 |
| onnxruntime          | 1.23.2  | dépendance WhisperX |
| ctranslate2          | 4.6.1   | compile CPU Apple |
| ffmpeg / ffprobe     | 6.x–8.x | compat Homebrew macOS, version exacte consignée |

Le fichier `requirements.lock` capture exactement ces versions. Le script `bin/env_check.sh` échoue si la machine active diffère (Python, libs Python, ffmpeg).

## Paramètres pipeline (configs/base_stable.yaml)

- Segmentation: fenêtres 75 s, overlap 8 s, mono 16 kHz.
- Prétraitement: loudnorm + mono, VAD désactivée.
- ASR: Faster-Whisper `large-v3`, `beam_size=1`, `best_of=1`, `temperature={0.0,0.2}`, `no_speech_threshold=0.6`, `max_workers=8`, `device=auto`.
- Diarisation par défaut: mode monologue (`max_speakers=1`, `min_speaker_turn=1.3`).
- Alignement: WhisperX forcé en `fr`.
- Exports stricts: `.md`, `.json`, `.vtt` + `.low_confidence.csv`.
- Aucun fallback automatique: chaque clé doit être présente, sinon la CLI lève `PipelineError`.

## Flags autorisés

| Flag | Effet |
|------|-------|
| `--mode multi` | Active la diarisation multi-speakers (tu peux compléter avec `--num-speakers` ou `--diarization-max-speakers`). |
| `--diarization-max-speakers / --diarization-min-speaker-turn` | Overrides ponctuels. |
| `--low-confidence-threshold`, `--low-confidence-out` | QA confiance personnalisée. |
| `--chapters-min-duration` | Force un découpage doux (en secondes). |
| `--only-failed` | Lors d’un `resume`, ne relance que les segments en statut FAILED. |
| `--allow-partial-export` | Déverrouille la protection contre les exports partiels (déconseillé). |

Tous les autres paramètres sont verrouillés par la config `configs/base_stable.yaml`.

## Mode strict (activé par défaut)

- `--strict --fail-fast --no-partial-export` appliqués si non précisés.
- Premier segment en échec → arrêt immédiat (pas d’export). L’état `FAILED` est inscrit dans `manifest_state.json`.
- Reprise via `bin/run.sh resume --only-failed --config configs/base_stable.yaml --input ...`.

## Vérifier un run

Chaque exécution génère :

- `work/<video>/logs/metrics.json` (entrées `asr` + `pipeline`).
- `work/<video>/logs/run_manifest.json` avec :
  - hash SHA-256 du média d’entrée
  - durées totales + par étape
  - stats ASR (processed / skipped / retries / workers)
  - versions des libs (python, torch, whisper, ffmpeg)
  - statut final (`ok` ou `failed`).
- Le dossier `TRANSCRIPT - <video>` (créé à côté du média) doit contenir **exactement** : `.md`, `.json`, `.vtt`, `.chapters.json`, `.low_confidence.csv`.
- `work/<video>/` doit contenir `audio_16k.wav`, `manifest.csv`, `02_merged_raw.json`, `03_aligned_whisperx.json`, `04_cleaned.json`, `05_polished.json`.

Le script `bin/env_check.sh` doit être vert **avant** toute exécution :

```bash
source .venv/bin/activate
bin/env_check.sh
```

## Redémarrer après un échec

1. Inspecter `work/<video>/logs/run_manifest.json` pour identifier l’étape en erreur.
2. Rejouer uniquement l’ASR défaillant :
   ```bash
   bin/run.sh resume --only-failed \
     --config configs/base_stable.yaml \
     --input "/chemin/vers/media.mp4"
   ```
3. Vérifier les metrics et relancer `bin/run.sh run ...` si nécessaire.

Aucune suppression automatique n’est effectuée : les JSONL existants sont réutilisés, sauf pour les segments explicitement `FAILED`.

## Exemples (valides)

1. **Monologue FR**
   ```bash
   bin/run.sh run --config configs/base_stable.yaml \
     --input "/data/podcasts/episode.mp4"
   ```
2. **Reprise ciblée**
   ```bash
   bin/run.sh resume --only-failed --config configs/base_stable.yaml \
     --input "/data/podcasts/episode.mp4"
   ```
3. **QA confiance dédiée**
   ```bash
   bin/run.sh run --config configs/base_stable.yaml \
     --input "/data/podcasts/episode.mp4" \
     --low-confidence-threshold 0.30 \
     --low-confidence-out exports/episode.qa.csv
   ```

Respecter ce document garantit des sorties déterministes, reproductibles et auditables sur toute machine conforme.
