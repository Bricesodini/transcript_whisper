# Phase lexicon RAG

Cette phase intermédiaire (« 1.5 ») sert à détecter automatiquement les variantes douteuses (mauvaise casse, accents cassés, noms propres mal transcrits) **avant** un export RAG. Elle ne repose que sur des heuristiques offline : aucun appel LLM n’est requis.

## Objectif

1. **Scanner** les textes issus d’ASR (`work/<doc>`) pour proposer un fichier `rag.glossary.suggested.yaml`.
2. **Valider** manuellement (édition, prune) puis appliquer les règles retenues dans `rag.glossary.yaml`.
3. **Tamponner** (`.lexicon_ok.json`) pour mémoriser quel fichier source a été pris en compte (hash SHA256). Si le texte change, le batch saura qu’un nouveau scan est nécessaire.

## Commandes principales

```powershell
# 1) Générer les suggestions pour tous les docs en staging ASR
bin\pipeline_lexicon_batch.bat --scan-only

# 2) Dans work\<doc>, ouvrir rag.glossary.suggested.yaml, conserver seulement les règles souhaitées
# 3) Optionnel : appliquer automatiquement (copie vers rag.glossary.yaml + stamp)
bin\pipeline_lexicon_batch.bat --apply

# 4) Export RAG une fois les glossaires validés
bin\pipeline_rag_batch.bat --version-tag nas_v1
```

### Paramètres `bin\pipeline_lexicon_batch.bat`

| Flag | Effet |
| ---- | ----- |
| `--scan-only` | (défaut) ne fait qu’un `rag lexicon scan`. |
| `--apply` | Enchaîne `rag lexicon apply` et met à jour `.lexicon_ok.json`. |
| `--force` | Re-scan même si `rag.glossary.yaml` + stamp up-to-date. |
| `--doc pattern` | N’exécute que les dossiers dont le nom contient `pattern`. |
| `--max-docs N` | Limite le nombre de documents traités (debug). |
| `-- …` | Tout ce qui suit `--` est transmis tel quel aux commandes `rag lexicon …`. |

### Fichiers produits

| Fichier | Rôle |
| ------- | ---- |
| `rag.glossary.suggested.yaml` | Propositions détectées par le scan (jamais appliquées automatiquement). |
| `rag.glossary.yaml` | Glossaire validé (merged par `rag-export` avec la config globale/doc). |
| `.lexicon_ok.json` | Stamp : doc_id, source utilisée (05_polished/04_cleaned/02_merged_raw), hash SHA256 et nombre de règles. |

Le batch **saute** un document si `rag.glossary.yaml` existe **et** que `.lexicon_ok.json` correspond encore au hash du fichier source (sauf `--force`). Modifier `05_polished.json` (ou la source choisie) suffit à détecter qu’un nouveau scan est nécessaire.

## Cycle recommandé

1. `pipeline_asr_batch.bat` → alimente `02_output_source\asr\...`.
2. `pipeline_lexicon_batch.bat --scan-only` → produit les suggestions.
3. Pour chaque doc : ouvrir `work\<doc>\rag.glossary.suggested.yaml`, corriger/compléter les entrées, supprimer les règles inutiles.
4. `pipeline_lexicon_batch.bat --apply` → copie les règles validées dans `rag.glossary.yaml` + écrit `.lexicon_ok.json` (horodatage, SHA, rules_count).
5. `pipeline_rag_batch.bat …` → génère `RAG-<doc>` en appliquant uniquement les glossaires validés.

> Si un `rag.glossary.suggested.yaml` existe sans `rag.glossary.yaml`, `rag doctor` émet un warning : vous n’avez pas validé la dernière passe lexicon.

## Validation manuelle

- Les règles sont de simples regex `pattern/replacement`. Gardez les expressions les plus spécifiques possible pour éviter les faux positifs.
- Les évidences (`evidence`) listent jusqu’à 2 extraits afin de comprendre le contexte.
- Si vous validez manuellement sans repasser par le batch, exécutez `bin\run.bat rag lexicon apply --input "work\<doc>"` pour mettre à jour le stamp.

## Questions fréquentes

**Puis-je éditer directement `rag.glossary.yaml` ?** Oui, mais pensez à lancer `rag lexicon apply` pour régénérer `.lexicon_ok.json` (sinon le batch rescannera en permanence).  
**Comment forcer un rescan complet ?** `bin\pipeline_lexicon_batch.bat --force`.  
**Que faire d’une suggestion que je refuse ?** Supprimez-la du fichier `rag.glossary.suggested.yaml` (ou laissez-la vide), puis validez uniquement les règles désirées dans `rag.glossary.yaml`.  
**Et si un LLM est branché plus tard ?** La même phase servira à superviser les suggestions LLM + heuristiques : `rag-export` ne consomme jamais les suggestions tant qu’elles ne sont pas promues dans `rag.glossary.yaml`.
