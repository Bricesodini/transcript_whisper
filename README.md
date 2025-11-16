# Transcribe Suite

> **Transcription locale de haute qualité**, compatible Apple Silicon (CPU, sans accélération GPU), avec **diarisation multi-locuteurs**, **alignement mot-à-mot**, **chapitrage intelligent** et **exports prêts pour RAG / montage**.  
> Une pipeline complète, **offline**, pensée pour produire un texte **lisible, structuré et exploitable** – pas seulement des sous-titres.

---

## 🚀 TL;DR (Quickstart – 30s)

```bash
cd transcribe-suite
bin/setup.sh               # crée .venv + installe les deps
source .venv/bin/activate
export PYANNOTE_TOKEN="hf_xxxxxxxxxxxxxxxxx"  # token HF (read)
bin/run.sh --input "/chemin/vers/media.mp4" --lang auto --export txt,md,json,srt,vtt
bin/run.sh dry-run --input "/chemin/vers/media.mp4" --lang auto

# Vérification environnement (versions figées)
source .venv/bin/activate
bin/env_check.sh
```

**Sorties** dans un dossier `TRANSCRIPT - <NomDuFichier>` créé à côté du média :

- `.md` (sections/titres/résumés, Obsidian-ready)
- `.txt` (lecture fluide)
- `.json` (RAG-ready : sections → citations → timecodes)
- `.chapters.json` (chapitrage autonome)
- `.srt` / `.vtt` (sous-titres broadcast / web)
- `.low_confidence.csv` (audit mots < seuil de confiance)
- `.clean.jsonl` / `.clean.txt` (texte human vs machine, prêt pour RAG/finetune)
- `.chunks.jsonl` + `.chunks.meta.json` (blocs 200–400 tokens avec overlap contrôlé)
- `.quotes.jsonl` (extractions liées aux sections/chunks)
- `.low_confidence.jsonl` (file d'attente pour relecture ciblée)
- `.metrics.json` (tableau machine-readable pour log/graphes)
- `.clean.final.txt` / `.final.md` / `.qa.json` (via le post-traitement optionnel ci-dessous)

👉 Référence complète du mode stable : `docs/STABLE_BASE.md` (versions, flags autorisés, procédures de reprise).

---

## 🧹 Post-traitement & QA éditoriale

Un script dédié (`tools/postprocess_transcript.py`) applique la chaîne de polish générique décrite plus haut :

1. **Diagnostic des assets** : vérifie la cohérence `clean.txt` / `metrics.json` / `low_confidence.jsonl`.
2. **Normalisation** : nettoyage des timestamps/balises, homogénéisation typographique + application du glossaire.
3. **Gestion low-confidence** : alignement automatique des entrées `low_confidence.jsonl`, marquage ⚠️ inline et annexe des phrases non localisées.
4. **Assemblage final / Markdown** : regroupement en paragraphes, conservation optionnelle des locuteurs, rendu Markdown basé sur `.chapters.json` (ou fallback structuré + citations).
5. **QA JSON** : récapitulatif des phrases modifiées, drapeaux de relecture, incohérences détectées.

```bash
cd transcribe-suite
source .venv/bin/activate
python tools/postprocess_transcript.py \
  --export-dir "exports/TRANSCRIPT - Mon Talk" \
  --doc-id "Mon Talk" \
  --config configs/postprocess.default.yaml
```

👉 Paramètres clés dans `configs/postprocess.default.yaml` (profil `default`, suffixes de sortie, options de normalisation, règles low-conf, QA). Ajoute ton glossaire / overrides en dupliquant ce fichier ou en passant `--profile`.

Sorties supplémentaires (à côté des artefacts existants) :

- `<doc>.clean.normalized.txt` — version clean 1:1 vs source, normalisée.
- `<doc>.clean.final.txt` — texte lisible (paragraphes, locuteurs optionnels, marqueurs ⚠️).
- `<doc>.final.md` — structuré `# / ##` + bloc “Citations clés”.
- `<doc>.qa.json` — rapport machine-readable (lignes modifiées, flags, issues).

---

## ✨ Pourquoi Transcribe Suite ?

| Capacité                                  | Transcribe Suite | Whisper CLI | MacWhisper    | SaaS (AssemblyAI/Descript) |
| ----------------------------------------- | ---------------- | ----------- | ------------- | -------------------------- |
| Transcription (Whisper large-v3)          | ✅ haute qualité | ✅          | ✅            | ✅                         |
| **Diarisation** (pyannote.audio)          | ✅ robuste       | ❌          | ✅ simplifiée | ✅                         |
| **Alignement mot-à-mot** (WhisperX)       | ✅               | ❌          | ❌            | ✅                         |
| **Chapitrage intelligent** (2–8 min)      | ✅               | ❌          | ❌            | ✅                         |
| **Lecture fluide** (polish typographique) | ✅               | ❌          | partiel       | ✅                         |
| **Exports RAG-ready** (JSON structuré)    | ✅               | ❌          | ❌            | partiel                    |
| **Local / offline**                       | ✅               | ✅          | ✅            | ❌                         |
| Apple Silicon (CPU)                       | ✅ supporté      | partiel     | ✅            | N/A                        |

**En bref** : au lieu d’un texte brut, vous obtenez un **document de travail** (chapitres, résumés, citations, timecodes) utilisable **immédiatement** pour analyse, synthèse, écriture, ou montage.

---

## 🧱 Pipeline

`preproc → segment → asr-parallel → merge → diarize → align → refine → clean → polish → structure → export`

- **preproc** : normalisation `ffmpeg` (mono, 16 kHz, loudnorm, débruitage léger, VAD court)
- **segment** : découpes glissantes `75s` + overlap `8s`, manifest + state JSON pour reprise
- **asr-parallel** : Faster-Whisper large-v3 (mode auto CPU, sans GPU Metal) sur N workers (≤10) via queue, JSONL par segment
- **merge** : fusion déterministe des overlaps (Levenshtein + logprob) → `02_merged_raw.json`
- **diarize** : Pyannote (RTTM export)
- **align** : WhisperX (word-level timestamps) sur l'audio complet `audio_16k.wav`
- **refine** : re-ASR local sur segments à faible confiance
- **clean/polish** : suppression fillers, typo FR, respiration de phrase
- **structure** : chapitrage heuristique, citations, résumés → export `.chapters.json`
- **export** : `.txt`, `.md`, `.json`, `.srt`, `.vtt` (UTF-8) + copie presse-papiers

### Modes & reprises

```bash
bin/run.sh --input "media.mp4" --config configs/base_stable.yaml         # run complet
bin/run.sh post --input "media.mp4" --config ... --only=chunk,audit      # rejoue uniquement certaines étapes post
bin/run.sh resume --input "media.mp4" --config ...                       # relance clean→export sans repasser par ASR/align
bin/run.sh --input "media.mp4" ... --dry-run                             # s'arrête après l'audit (utiliser --no-audit si besoin)
```

Options clés :

| Flag | Effet |
| --- | --- |
| `--log-level {debug,info,warning,error}` | Ajuste la verbosité console (les fichiers restent en DEBUG). |
| `--only=clean,chunk` | Force uniquement certaines sous-étapes lors d'un `post`/`resume`. |
| `--dry-run` | Exécute toutes les étapes nécessaires (incluant audit/metrics) mais saute les exports finaux. |
| `--no-audit` | Désactive l'écriture de `*.audit.md` (utile pour les runs batch). |

### Étapes suggérées & points de contrôle

**Pré-run recommandé**

- `source .venv/bin/activate` puis `bin/env_check.sh` pour valider Python, ffmpeg et wheels pin.
- `export PYANNOTE_TOKEN="hf_xxx"` (et presets `ASR_THREADS` / `POST_THREADS` si nécessaires).
- `bin/run.sh dry-run --input "...mp4"` pour vérifier l’arborescence cible, les exports et l’état des artefacts existants.

**Déroulé stage par stage**

1. **Prétraitement & segmentation (`prepare`)**  
   Commande : `bin/run.sh prepare --input "...mp4"`  
   Artefacts : `work/<media>/audio_16k.wav`, `00_segments/*.wav`, `manifest.csv`, `manifest_state.json`.  
   Contrôle : `manifest_state.json` affiche `PENDING/DONE/FAILED` pour chaque segment ; relancez avec `--force` pour régénérer.

2. **ASR parallèle (`asr`)**  
   Commande : `bin/run.sh asr --input "...mp4"` (ou incluse dans `run`).  
   Artefacts : `01_asr_jsonl/seg_*.jsonl`, `logs/asr_worker_*.log`, métriques dans `logs/metrics.json`.  
   Contrôle : surveillez les `failed_segments` remontés dans les logs ; `resume --only-failed` rejoue uniquement ceux en erreur.

3. **Fusion déterministe (`merge`)**  
   Commande : `bin/run.sh merge --input "...mp4"` lorsque vous souhaitez recalculer `02_merged_raw.json` sans relancer l’ASR.  
   Artefacts : `02_merged_raw.json`, `logs/merge.log`.  
   Contrôle : vérifier que le champ `language` concorde avec `--lang`/détection automatique et que le compteur de segments correspond au manifest.

4. **Diarisation Pyannote (`stage_diarization`)**  
   Déclenchée automatiquement par `bin/run.sh align`/`run`.  
   Artefacts : `diarization.rttm`, `cache/pyannote_*`, éventuel masque `speech_segments.json`.  
   Contrôle : adapter `--mode`, `--num-speakers`, `--diarization-*` en fonction des logs si la séparation des voix est insuffisante.

5. **Alignement WhisperX (`align`)**  
   Commande : `bin/run.sh align --input "...mp4"` (inclut la diarisation si nécessaire).  
   Artefacts : `03_aligned_whisperx.json`, `logs/align.log`, audio préparé `audio_16k.wav`.  
   Contrôle : ajuster `--align-workers`, `--align-batch`, `--speech-only` en fonction du temps d’exécution et des warnings WhisperX.

6. **Post-traitement éditorial (`post`)**  
   Commande : `bin/run.sh post --input "...mp4"` pour rejouer `refine → clean → polish → structure`.  
   Artefacts : `refine/`, `04_cleaned.json`, `05_polished.json`, `structure.json`, `logs/post.log`.  
   Contrôle : `refine` ne tourne que si des segments sous le seuil `--low-confidence-threshold` sont détectés ; modifiez le seuil ou forcez avec `--force`.

7. **Exports finaux (`export`)**  
   Commande : `bin/run.sh export --input "...mp4" --export txt,md,...`.  
   Artefacts : dossier `TRANSCRIPT - <media>/` (formats demandés, `.chapters.json`, `.low_confidence.csv`).  
   Contrôle : en mode strict, `_verify_artifacts` confirme la présence exacte de `.md/.json/.vtt`; `run_manifest.json` (dans `work/.../logs`) récapitule hash, durées, versions.

---

## 🧭 Référence CLI centralisée

### Structure de base

- `bin/run.sh <commande> --input "/chemin/vers/media.ext" [options]`
- `bin/run.sh` injecte automatiquement `--config config/config.yaml`. Si vous appelez `src/pipeline.py` directement, ajoutez `--config`.
- Le token pyannote (`PYANNOTE_TOKEN`) et les presets thread (`ASR_THREADS`, `POST_THREADS`) doivent être exportés avant l’appel si nécessaires.

```bash
bin/run.sh run \
  --input "/Volumes/Interviews/talkshow.mp4" \
  --lang auto \
  --profile talkshow \
  --export txt,md,json,srt,vtt
```

### Commandes disponibles

| Commande | Ce qui est exécuté | Quand l’utiliser |
| -------- | ------------------ | ---------------- |
| `run` (défaut) | Chaîne complète `preproc → export`. | Traitement standard d’un média. |
| `prepare` | Prétraitement + segmentation + manifest/state. | Préparer en amont ou diagnostiquer un input douteux. |
| `asr` | Uniquement Faster-Whisper sur les segments générés. | Rejouer l’ASR après un réglage compute/offline. |
| `merge` | Fusion des JSONL ASR + génération `02_merged_raw.json`. | Corriger un merge ou inspecter des overlaps. |
| `align` | Alignement WhisperX mot-à-mot (audio complet). | Refaire l’alignement après tweaking threads/batch. |
| `post` | `clean → polish → structure`. | Travailler la qualité éditoriale sans relancer l’ASR. |
| `export` | Génération des formats finaux depuis les artefacts post. | Recréer des exports (formats supplémentaires, patch). |
| `resume` | Pipeline complet mais en reprenant tout artefact déjà `DONE`. | Après crash / coupure ; combine avec `--only-failed`. |
| `dry-run` | Aucun traitement : affiche l’arborescence cible + paramètres résolus. | Vérifier les chemins/exports avant un run lourd. |

### Arguments essentiels

| Option | Rôle | Notes / exemples |
| ------ | ---- | ---------------- |
| `command` | Choix de la commande ci-dessus (`run` par défaut). | `bin/run.sh align --input ...` |
| `--input` (obligatoire) | Média audio/vidéo à transcrire. | Accepte `~/`, chemins relatifs ou un fichier déjà déposé dans `inputs/`. |
| `--lang` | Force la langue ASR (`fr`, `en`, `auto`). | Détecte automatiquement sinon ; forcer `fr` accélère l’ASR. |
| `--profile` | Charge un profil YAML (`default`, `talkshow`, `conference`, custom). | Permet d’appliquer des presets exports/chapitrage. |
| `--export` | Liste CSV des formats (`txt,md,json,srt,vtt`). | En mode strict seuls `md,json,vtt` sont autorisés. |
| `--initial-prompt` | Injecte un prompt au démarrage de l’ASR. | Utile pour donner des listes de noms propres. |
| `--mode` | `mono` ou `multi` influence la diarisation par défaut. | `multi` ouvre plus le nombre de locuteurs + `speech-mask`. |
| `--skip-diarization` | Court-circuite Pyannote et les étapes dépendantes. | Pour mesurer uniquement l’ASR ou en cas d’absence de token. |

### Contrôle d’exécution & sécurité

| Option | Ce que ça fait | Usage recommandé |
| ------ | -------------- | ---------------- |
| `--force` | Rejoue une commande même si les artefacts existent. | À utiliser après une modification de config/poids. |
| `--only-failed` | Combine avec `resume`/`asr` pour ne rejouer que les segments `FAILED`. | Gagnez du temps après un incident ponctuel. |
| `--strict` / `--no-strict` | Active (défaut) ou désactive la conformité « stable base ». | Gardez `--strict` pour des livrables figés. |
| `--fail-fast` / `--no-fail-fast` | Stop immédiat au premier segment en échec (défaut : on stop). | Passez en `--no-fail-fast` en phase d’exploration. |
| `--no-partial-export` / `--allow-partial-export` | Empêche (défaut) ou autorise les exports si une étape échoue. | Autorisez ponctuellement pour du debug rapide. |
| `--keep-build` | Conserve `work/<media>` après succès. | Analyse post-mortem ou réutilisation d’artefacts. |
| `--verbose` | Active les logs DEBUG dans la console + fichiers. | Debug fin, vérification de tokens, etc. |

### Qualité, diarisation & QA

| Option | Description | Exemple d’utilisation |
| ------ | ----------- | --------------------- |
| `--diarization-max-speakers` | Override du `max_speakers` Pyannote. | `--diarization-max-speakers 4` pour une table ronde. |
| `--diarization-min-speaker-turn` | Durée mini (s) entre deux tours pour lisser la diarisation. | `--diarization-min-speaker-turn 1.2` pour éviter le zapping. |
| `--diarization-monologue` | Raccourci `max_speakers=1`, `min_turn=1.3`. | Dictées, cours magistraux. |
| `--num-speakers` | Hint direct du nombre de voix attendues (Pyannote). | `--num-speakers 2` si vous connaissez la scène. |
| `--speech-mask` / `--no-speech-mask` | Applique (défaut profil multi) un masque speech aux étapes post-ASR. | `--speech-mask` pour ignorer le bruit hors diarisation. |
| `--speech-only` / `--no-speech-only` | Limite ou non l’alignement WhisperX aux segments speech. | `--speech-only` accélère l’alignement sur longs silences. |
| `--low-confidence-threshold` | Seuil de confiance pour marquer les mots suspects. | `--low-confidence-threshold 0.35`. |
| `--low-confidence-out` | Chemin CSV pour exporter ces mots. | `--low-confidence-out audit.csv`. |
| `--chapters-min-duration` | Durée soft minimale d’un chapitre (s). | `--chapters-min-duration 150` pour forcer des blocs courts. |

### Performance & ressources

| Option | Description | Exemple |
| ------ | ----------- | ------- |
| `--asr-workers` | Nombre maximal de workers Faster-Whisper parallèles (<= segments). | `--asr-workers 6` couplé à `ASR_THREADS`. |
| `--compute-type` | Force `int8`, `float16`, `auto` pour Faster-Whisper. | `--compute-type int8` recommandé sur Apple Silicon. |
| `--chunk-length` | Durée (s) des morceaux traités par Faster-Whisper. | `--chunk-length 20` pour long média stable. |
| `--vad` / `--no-vad` | Active/désactive le VAD interne Faster-Whisper. | `--vad` pour couper le bruit d’ambiance permanent. |
| `--condition-off` | Désactive `condition_on_previous_text`. | Évite les dérives sur podcasts très longs. |
| `--align-workers` | `num_workers` WhisperX. | `--align-workers 4` si beaucoup de cœurs. |
| `--align-batch` | `batch_size` WhisperX. | `--align-batch 24` sur M3 Max. |
| `--diar-device` | Choix du device Pyannote (`cpu`, `cuda`, `mps`). | `--diar-device cpu` (défaut) ; `mps` possible si torch Metal. |
| `--seg-batch` / `--emb-batch` | Batch sizes segmentation/embeddings Pyannote. | `--seg-batch 12 --emb-batch 12` pour CPU rapides. |
| `--export-parallel` / `--export-serial` | Détermine si les exports tournent en multi-thread (défaut config). | `--export-serial` si disque lent / collisions I/O. |

> Astuce : `bin/run.sh dry-run ... --verbose` récapitule tous les paramètres effectifs (profil + overrides) avant d’allumer les modèles. Servez-vous-en pour documenter une recette partagée.

## 🗂️ Arborescence de travail

```
transcribe-suite/
├─ inputs/VIDEO.ext                      # optionnel, équivalent --input
├─ work/VIDEO/
│  ├─ audio_16k.wav                      # prétraité 16 kHz mono
│  ├─ manifest.csv + manifest_state.json # suivi segments PENDING/DONE/FAILED
│  ├─ 00_segments/seg_00000__from_0__to_75000.wav
│  ├─ 01_asr_jsonl/seg_00000.jsonl       # 1 objet JSON par segment
│  ├─ 02_merged_raw.json
│  ├─ 03_aligned_whisperx.json
│  ├─ 04_cleaned.json
│  ├─ 05_polished.json
│  ├─ structure.json
│  ├─ logs/ (run.log, asr_worker_*.log, merge.log, align.log, metrics.json)
│  └─ cache/, refine/, diarization.rttm…

media_parent/
├─ VIDEO.ext
└─ TRANSCRIPT - VIDEO/
   ├─ VIDEO.txt / .md / .json / .srt / .vtt
   ├─ VIDEO.chapters.json
   └─ VIDEO.low_confidence.csv
```

Toutes les sorties finales sont donc adjacentes au média traité, dans un dossier `TRANSCRIPT - <Nom>`, ce qui évite les duplications dans `transcribe-suite/exports`.

La **reprise** est automatique : si un fichier JSONL existe ou qu'un segment est marqué `DONE` dans `manifest_state.json`, il est sauté. Chaque worker écrit ses logs (avec PID) pour faciliter le debug.

---

## 📦 Installation

**Prérequis**

- macOS + `ffmpeg` (`brew install ffmpeg`)
- ffmpeg 6.x–8.x (Homebrew) + ffprobe (même plage)
- Python 3.9+
- Apple Silicon recommandé (CPU performant, sans dépendance GPU)
- Token Hugging Face (pyannote) → `export PYANNOTE_TOKEN="hf_xxx"`

**Bootstrap**

```bash
cd transcribe-suite
bin/setup.sh
source .venv/bin/activate
```

**Installation rapide (requirements.lock)**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.lock
```

> Les versions sont figées dans `requirements.lock` pour garantir la reproductibilité (mêmes wheels ctranslate2/pyannote). Préfère toujours ce lock avant un run critique.

**Vérification environnement (`bin/env_check.sh`)**

```bash
source .venv/bin/activate
bin/env_check.sh
```

- vérifie `python`, `pip`, `ffmpeg`, `ctranslate2`, `faster-whisper`, `pyannote.audio`, `whisperx`.
- tolère un warning `torchaudio` sur Apple Silicon (Homebrew ne shippe pas les wheels Metal) : il est ignoré car la pipeline n'importe pas torchaudio, seules les bindings `soundfile` / `ffmpeg` sont utilisés.

### Stable Base

(extrait mis à jour)

- faster-whisper 1.2.1 (CPU Apple Accelerate)
- torch / torchaudio 2.8.0
- pyannote.audio 3.4.0
- onnxruntime 1.23.2

> **Accélération Metal (expérimentale et optionnelle)**  
> `brew install ctranslate2` puis :  
> `pip install --no-binary faster-whisper faster-whisper`  
> Non packagé par défaut : privilégie la voie CPU si tu ne veux pas depanner Metal. Sans ctranslate2 Metal, Faster-Whisper bascule automatiquement sur CPU (voir logs). Les versions exactes sont loguées dans `run_manifest.json`.

---

## 🖥️ Utilisation (CLI / Shortcuts / Drag-Drop)

Référence détaillée des commandes/arguments : voir la section **🧭 Référence CLI centralisée** ci-dessus.

**CLI**

```bash
bin/run.sh \
  --input "/chemin/vers/podcast.mp4" \
  --lang auto \
  --profile talkshow \
  --export txt,md,json,srt,vtt

# Commandes unitaires
bin/run.sh prepare --input "/chemin/vers/podcast.mp4"
bin/run.sh asr --input "/chemin/vers/podcast.mp4"
bin/run.sh merge --input "/chemin/vers/podcast.mp4"
bin/run.sh align --input "/chemin/vers/podcast.mp4"
bin/run.sh post --input "/chemin/vers/podcast.mp4"
bin/run.sh export --input "/chemin/vers/podcast.mp4"
bin/run.sh resume --input "/chemin/vers/podcast.mp4"
bin/run.sh dry-run --input "/chemin/vers/podcast.mp4"
```

### Apple Shortcuts

```bash
cd /Users/bricesodini/01_ai-stack/scripts/transcript_whisper/transcribe-suite \
  && source .venv/bin/activate \
  && NO_TK=1 bin/run.sh run --input "$@"
```

> Entrée Shortcuts = « en arguments ». Pour du multi-voix, ajoute `--mode multi --speech-mask --diar-device cpu --num-speakers auto`.

---

## 🔁 Reprise & mode strict

- `manifest_state.json` garde l’état `PENDING / DONE / FAILED` par segment.  
  Relance ciblée :

```bash
bin/run.sh resume --input "/chemin/vers/podcast.mp4" --only-failed
bin/run.sh asr --input "/chemin/vers/podcast.mp4" --only-failed
```

- `--only-failed` rejoue uniquement les segments marqués FAILED (utile après crash réseau/énergie).
- `--no-partial-export` est activé par défaut en mode strict : aucun export n’est écrit tant que toutes les étapes réussissent. Besoin d’un export partiel pour du debug ? ajouter `--allow-partial-export`.
- `--fail-fast` stoppe le pipeline au premier segment en échec pour éviter des artefacts corrompus ; utiliser `--no-fail-fast` pour continuer coûte que coûte.
- Profil `stable` (et `docs/STABLE_BASE.md`) verrouille la config : seules les overrides suivantes sont autorisées sans rompre la conformité :
  - `--diarization-monologue` ou `--diarization-max-speakers / --diarization-min-speaker-turn`
  - `--low-confidence-threshold` / `--low-confidence-out chemin.csv`
  - `--chapters-min-duration` pour soft-trimmer des chapitres
  - `--export md,json,vtt` (ensemble figé par le mode strict)

---

## ⚡ Optimisations ASR (CPU/Faster-Whisper)

**1. Threads & BLAS**

Avant un run `bin/run.sh asr|run`, fixe les threads pour éviter les combats BLAS :

```bash
export ASR_THREADS=$(python - <<'PY'
import os; print(max(8, (os.cpu_count() or 8) - 2))
PY
)
export OMP_NUM_THREADS=$ASR_THREADS
export OPENBLAS_NUM_THREADS=$ASR_THREADS
export VECLIB_MAXIMUM_THREADS=$ASR_THREADS
export NUMEXPR_NUM_THREADS=$ASR_THREADS
export CTRANSLATE2_NUM_THREADS=$ASR_THREADS

# équivalent :
source transcribe-suite/bin/asr_env.sh
```

**2. Paramètres Faster-Whisper recommandés (CPU “rapide mais stable”)**

| Paramètre                       | Valeur conseillée                                       |
| --------------------------------| ------------------------------------------------------- |
| `compute_type`                  | `int8` (CPU Apple Silicon)                              |
| `beam_size`, `best_of`          | `1` (ou `beam_size=2` si qualité++ et CPU dispo)        |
| `temperature`                   | `0.0` + fallback interne                               |
| `vad_filter`                    | `true`                                                  |
| `chunk_length_s`                | `20` (15–30 selon médias très longs)                    |
| `condition_on_previous_text`    | `false` (évite les dérives longues)                     |
| `num_workers`                   | `min(8, ASR_THREADS)`                                   |
| `task`                          | `transcribe`                                            |
| `language`                      | Forcer `fr` si connu (épargne l’auto-detect coûteuse)   |

Dans `config/config.yaml` tu peux refléter ces réglages (section `asr`).  
En CLI :

```bash
NO_TK=1 ASR_THREADS=10 bin/run.sh run \
  --input "/chemin/audio.wav" \
  --lang fr \
  --profile stable \
  --export md,json,vtt \
  --force
```

Le runner utilisera alors `ASR_THREADS` pour `CTRANSLATE2_NUM_THREADS` et les bindings Faster-Whisper respectent `num_workers`.

---

## ⚡ Optimisations post-ASR (Align / Diar / Export)

**1. Threads dédiés (ALIGN / DIAR / EXPORT)**

```bash
export POST_THREADS=$(python - <<'PY'
import os; print(max(6, (os.cpu_count() or 8)-1))
PY
)
export OMP_NUM_THREADS=$POST_THREADS
export OPENBLAS_NUM_THREADS=$POST_THREADS
export VECLIB_MAXIMUM_THREADS=$POST_THREADS
export NUMEXPR_NUM_THREADS=$POST_THREADS

# équivalent :
source transcribe-suite/bin/post_env.sh
```

Le runner bascule automatiquement sur ce preset avant `align`, `diarize`, `post`, `export` et applique `torch.set_num_threads`.

**2. ALIGN WhisperX**

```bash
bin/run.sh align \
  --align-workers 4 \
  --align-batch 16 \
  --speech-only
```

- `--align-workers` ajuste `num_workers` transmis à WhisperX (auto-fallback si non supporté).
- `--align-batch` contrôle `batch_size` (15–32 recommandé).
- `--speech-only` n’aligne que les segments recouverts par la diarisation (skip silence).

**3. DIAR Pyannote**

```bash
bin/run.sh diarize \
  --diar-device cpu \
  --seg-batch 12 \
  --emb-batch 12 \
  --num-speakers 2 \
  --speech-mask
```

- `--diar-device` force CPU/MPS/CUDA.
- `--seg-batch` / `--emb-batch` reconfigurent les batchs internes.
- `--num-speakers` renseigne le clustering (accélère la stabilisation).
- `--speech-mask` restreint les segments finals aux zones “speech” (basées sur les merged JSON).

**4. EXPORTS en parallèle**

```bash
bin/run.sh export --export-parallel --export md,json,vtt,jsonl
```

- Chaque format est écrit dans un thread séparé (`POST_THREADS` plafonne le pool).
- `jsonl` produit un flux segment-par-segment (utilisable pour pipeline RAG).
- `--export-parallel/--export-serial` disponibles sur toutes les commandes.

Checklist rapide :

1. ASR ➜ `source bin/asr_env.sh`, `--compute-type int8`, `--chunk-length 20`, `--asr-workers 8`.
2. ALIGN ➜ `source bin/post_env.sh`, `--align-workers 4`, `--align-batch 16`, `--speech-only`.
3. DIAR ➜ `--diar-device cpu`, `--seg-batch 12`, `--emb-batch 12`, `--num-speakers 2`, `--speech-mask`.
4. EXPORT ➜ `--export-parallel`, `--export md,json,vtt,jsonl`.

---

## ⚙️ Configuration

Fichier : `config/config.yaml`. Extrait :

```yaml
paths:
  inputs_dir: inputs
  work_dir: work
  exports_dir: exports
  logs_dir: logs

defaults:
  lang: auto
  model: large-v3
  export_formats: [txt, md, json, srt, vtt]

preproc:
  target_sr: 16000
  channels: 1
  loudnorm: true
  vad:
    enabled: true
    silence_duration: 0.5
    silence_threshold: -40

segmenter:
  segment_length: 75.0
  overlap: 8.0
  manifest_name: manifest.csv

asr:
  device: auto           # auto | metal | cpu (Metal non packagé par défaut)
  compute_type: auto     # ajuste automatiquement (CPU Apple Silicon par défaut)
  batch_size: 24
  beam_size: 1
  best_of: 1
  temperature: 0.0
  temperature_fallback: 0.2
  condition_on_previous_text: false
  no_speech_threshold: 0.6
  max_workers: 10
  max_retries: 2

languages:
  fr: { fillers: ["euh", "heu", "tu vois", "en fait", "bah"] }
  en: { fillers: ["uh", "um", "like", "you know", "actually"] }

diarization:
  model: pyannote/speaker-diarization-3.1
  authorization_env: PYANNOTE_TOKEN
  merge_single_speaker: true
  max_speakers: 2
  min_speaker_turn: 1.2

cleaning:
  min_segment_duration: 1.2
  max_segment_gap: 2.0
  remove_fillers: true
  capitalize_sentence_start: true
  min_word_confidence: 0.15
  merge_short_segments:
    enabled: true
    max_duration: 0.8
    max_gap: 0.5

structure:
  target_section_duration: 180
  max_section_duration: 480
  min_pause_gap: 6.0
  soft_min_duration: null
  trim_section_titles: true
  title_case: sentence
  enable_titles: false

polish:
  enabled: true
  sentence_case: true
  max_sentence_words: 18
  join_short_segments_ms: 650
  acronym_whitelist: ["IA"]
  fr_nbsp_before: [":", ";", "»", "!", "?"]
  fr_nbsp_after: ["«"]
  enable_nbsp: true
  normalize_list_markers: true
  list_bullet_symbol: "•"
  normalize_ellipses: true
  normalize_quotes: true
  ensure_terminal_punct: true
  replacements:
    - ["chat gpt", "ChatGPT"]
  lexicon:
    - pattern: "\\bchat\\s*gpt\\b"
      replacement: "ChatGPT"
    - pattern: "\\bi[\\.\\s]*a\\b"
      replacement: "IA"

export:
  low_confidence:
    threshold: 0.5
    csv_threshold: 0.35
    csv_enabled: true
    csv_output: null
    formats:
      txt:
        template: "**[{word}?]**"
      md:
        template: "**[{word}?]**"

refine:
  enabled: true
  low_conf_threshold: 0.5
  min_low_conf_ratio: 0.1
  padding: 0.25
  max_segment_duration: 25.0
```

Le module *polish* applique ces réglages pour imposer la typographie française (guillemets « » + espaces insécables avant `; : ? !`) et convertir automatiquement les listes `- item` en puces `• item`.

**Monitoring & reprise**

- `manifest_state.json` trace `PENDING / IN_PROGRESS / DONE / FAILED` ainsi que le nombre de retries (limités à 2).
- `logs/metrics.json` conserve les stats de la passe ASR (durée, workers utilisés, segments traités/ignorés/échoués).
- Chaque worker Faster-Whisper écrit un log dédié (`logs/asr_worker_<pid>.log`) pour les analyses de stabilité.

L'étape *refine* relance Whisper localement sur les segments où plus de 10 % des mots sont sous 0,50 de confiance (VAD plus permissif), puis remplace uniquement ces segments en conservant les timecodes globaux.

### Tests

```bash
cd transcribe-suite
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

- `tests/unit` couvre polish/clean/export sans charger les modèles lourds.
- Utilise `pytest -k export` pour vérifier l'encodage UTF-8.

---

---

## 📤 Exports

| Format  | Usage                                           |
| ------- | ----------------------------------------------- |
| `.md`   | Notes Obsidian, chapitres + résumés + citations |
| `.clean_txt` | Variante linéaire sans “Citations clés” (diffusion brute) |
| `.txt`  | Lecture fluide (voix-off/podcast)               |
| `.json` | RAG-ready (sections → citations → timecodes)    |
| `.jsonl` | Flux segment-par-segment (RAG / ingestion streaming) |
| `.srt`  | Sous-titres broadcast (Resolve/Premiere)        |
| `.vtt`  | Sous-titres web (FCP/Resolve)                   |
| `.low_confidence.csv` | Audit QA (mot, timecode, score < seuil) |

`export.low_confidence` marque automatiquement les mots <0,50 en `**[mot?]**` (format personnalisable par export) afin de cibler la relecture sans toucher au texte source.

Tous les fichiers sont écrits en **UTF-8** (sans BOM) avec fins de ligne **Unix**.

---

## 🔎 Tests rapides recommandés

| Cas                  | Attendu                                        |
| -------------------- | ---------------------------------------------- |
| Podcast FR posé      | Phrases fluides, chapitrage cohérent           |
| Conférence EN        | Titres courts pertinents, JSON RAG exploitable |
| Talkshow (≥2 voix)   | RTTM stable, attribution ≥90%                  |
| Smartphone bruyant   | Préproc audible, pas de fuite de bruit         |
| Vidéo YouTube rapide | Polish gère la respiration des phrases         |

---

## 🔐 Sécurité & secrets

- **Pas de tokens en clair** : stocke `PYANNOTE_TOKEN` / `OPENAI_API_KEY` dans `.env.local` (non versionné) ou dans ton shell, jamais dans un script.  
- `NO_TK=1` force les scripts (`bin/run.sh`, `bin/audit_before_commit.sh`) à ne jamais logguer les variables secrètes et à exécuter pyannote en mode “auth déjà présent dans l’env”.  
- `bin/audit_before_commit.sh` scanne l’arbre + l’index Git avec masquage automatique (`***REDACTED***`) et rappelle d’ajouter `work/`, `exports/`, `models/`, `.venv/`, `.cache/` dans `.gitignore`.
- Les logs applicatifs ne contiennent que des IDs tronqués (hash piste audio), pas d’input brut ni de token. Pense à purger `logs/` avant partage externe.

---

## ❓ FAQ

**Q : Faut-il un GPU Nvidia ?**  
Non. Toute la pipeline tourne sur CPU (Apple Silicon ou Intel), sans dépendance GPU Metal/Nvidia.

**Q : Pourquoi du local ?**  
Contrôle, confidentialité, reproductibilité. Et un texte **structuré** prêt à penser/travailler.

**Q : Puis-je désactiver la diarisation ?**  
Oui : `--skip-diarization` (mode rapide / machine légère).

**Q : Et l’anglais ?**  
`--lang auto` gère FR/EN. D’autres langues peuvent être ajoutées dans `config.yaml`.

---

## 🧰 Dépannage (Troubleshooting)

- **“unsupported device metal” / “int8_float16 not supported”**  
  → Installer ctranslate2 (`brew install ctranslate2`) puis  
  `pip install --no-binary faster-whisper faster-whisper`  
  ou forcer `asr.compute_type=int8` / `asr.device=auto`.

- **Caractères `?` entre chaque lettre**  
  → Encodage erroné (UTF-16). Tous les exports sont forcés en UTF-8.  
  Convertir un ancien fichier :  
  `iconv -f UTF-16LE -t UTF-8 ancien.txt > nouveau.txt`

- **Pyannote: auth/token**  
  → Accepter les modèles sur HF, exporter `PYANNOTE_TOKEN`, lancer une première fois pour cache.

Les logs détaillés sont dans `transcribe-suite/logs/`.

---

## 📝 Changelog (étape courante)

- Verrouillage du profil `stable` (exports `md/json/vtt`, `detect_language=false`, `requirements.lock` imposé).
- QA low-confidence renforcée : CSV obligatoire, flag `--only-failed` pour relancer uniquement les segments douteux, `bin/run.sh resume` strict.
- Diarisation mono par défaut (`--diarization-monologue`) pour la base stable, overrides documentées ci-dessus.
- Script `bin/audit_before_commit.sh` pour les scans secrets, artefacts, diff deps avant commit.

---

## 🔒 Licence & usage

Local / offline. Les modèles Hugging Face utilisés (pyannote, WhisperX) sont soumis à leurs licences respectives.

---

## 🙌 Remerciements / Crédits

- [OpenAI Whisper], [ctranslate2 / faster-whisper], [pyannote.audio], [WhisperX]
- Contributions et idées de la communauté STT & diarisation.
