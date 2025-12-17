"""Lexicon scan and apply helpers for glossary suggestions."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from utils import PipelineError, setup_logger

from . import PROJECT_ROOT
from .doc_id import resolve_doc_id
from .generation import compute_file_sha256
from .glossary import write_glossary_file
from .resolver import InputResolver, ResolvedPaths
from .targets import resolve_rag_directory
from .text_processing import detect_mojibake, fix_mojibake

WORD_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9'’\-]*")
STAMP_FILENAME = ".lexicon_ok.json"
STOPWORDS = {
    "le",
    "la",
    "les",
    "des",
    "de",
    "du",
    "nous",
    "nos",
    "vous",
    "avec",
    "dans",
    "pour",
    "sur",
    "alors",
    "que",
    "ce",
    "ces",
    "un",
    "une",
    "son",
    "sa",
    "bien",
    "plus",
    "et",
    "en",
    "notre",
    "leur",
    "leurs",
    "est",
    "test",
    "des",
    "ceci",
    "cela",
    "mais",
    "encore",
    "avant",
    "apres",
    "après",
    "chez",
    "dont",
}


@dataclass
class LexiconScanOptions:
    input_path: Path
    min_count: int = 2
    top_k: int = 200
    output_path: Optional[Path] = None
    doc_id_override: Optional[str] = None
    version_tag: Optional[str] = None


@dataclass
class LexiconApplyOptions:
    input_path: Path
    source_path: Optional[Path] = None
    target_path: Optional[Path] = None
    keep_top: Optional[int] = None
    doc_id_override: Optional[str] = None
    version_tag: Optional[str] = None


@dataclass
class DocumentContext:
    doc_id: str
    doc_title: str
    work_dir: Path
    texts: List[str]
    rag_dir: Optional[Path] = None
    resolved: Optional[ResolvedPaths] = None


@dataclass
class SequenceStat:
    count: int = 0
    forms: Counter = field(default_factory=Counter)
    examples: List[str] = field(default_factory=list)

    def add(self, form: str, sentence: str) -> None:
        self.count += 1
        self.forms[form] += 1
        if len(self.examples) < 3:
            cleaned = sentence.strip()
            if cleaned and cleaned not in self.examples:
                self.examples.append(cleaned)


@dataclass
class Suggestion:
    source: str
    target: str
    pattern: str
    replacement: str
    confidence: float
    evidence: List[str]


class LexiconScanner:
    """Produce glossary suggestions based on heuristic text analysis."""

    def __init__(self, options: LexiconScanOptions, config_bundle, *, log_level: str = "info"):
        self.options = options
        self.config_bundle = config_bundle
        self.config = dict(config_bundle.effective)
        self.logger = setup_logger(self._log_dir(), self._run_name(), log_level=log_level)

    def _log_dir(self) -> Path:
        log_cfg = (self.config.get("logging") or {}).get("log_dir")
        if log_cfg:
            candidate = Path(log_cfg)
            if not candidate.is_absolute():
                candidate = (PROJECT_ROOT / candidate).resolve()
        else:
            candidate = (PROJECT_ROOT / "logs" / "rag").resolve()
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def _run_name(self) -> str:
        return "rag_lexicon_scan"

    def run(self) -> Path:
        context = resolve_document_context(
            self.options.input_path,
            self.config_bundle,
            self.logger,
            doc_id_override=self.options.doc_id_override,
            version_tag=self.options.version_tag,
        )
        if not context.texts:
            raise PipelineError("Aucun texte disponible pour le scan du glossaire.")
        analyzer = HeuristicLexiconAnalyzer(context.texts, min_count=max(1, self.options.min_count))
        suggestions = analyzer.build_suggestions(top_k=max(1, self.options.top_k))
        output_path = self._resolve_output_path(context)
        payload = {
            "version": 1,
            "doc_id": context.doc_id,
            "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat(),
            "rules": [
                {
                    "pattern": suggestion.pattern,
                    "replacement": suggestion.replacement,
                    "confidence": round(suggestion.confidence, 3),
                    "evidence": suggestion.evidence,
                }
                for suggestion in suggestions
            ],
            "stats": {
                "scanned_tokens": analyzer.scanned_tokens,
                "unique_candidates": len(suggestions),
            },
        }
        write_glossary_file(output_path, payload)
        self.logger.info(
            "Lexicon scan OK: doc_id=%s suggestions=%d -> %s",
            context.doc_id,
            len(suggestions),
            output_path,
        )
        return output_path

    def _resolve_output_path(self, context: DocumentContext) -> Path:
        if self.options.output_path:
            return self.options.output_path
        return context.work_dir / "rag.glossary.suggested.yaml"


class LexiconApplyCommand:
    """Apply previously validated glossary suggestions."""

    def __init__(self, options: LexiconApplyOptions, config_bundle, *, log_level: str = "info"):
        self.options = options
        self.config_bundle = config_bundle
        self.logger = setup_logger(self._log_dir(), "rag_lexicon_apply", log_level=log_level)

    def _log_dir(self) -> Path:
        config = dict(self.config_bundle.effective)
        log_cfg = (config.get("logging") or {}).get("log_dir")
        if log_cfg:
            candidate = Path(log_cfg)
            if not candidate.is_absolute():
                candidate = (PROJECT_ROOT / candidate).resolve()
        else:
            candidate = (PROJECT_ROOT / "logs" / "rag").resolve()
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def run(self) -> Path:
        context = resolve_document_context(
            self.options.input_path,
            self.config_bundle,
            self.logger,
            doc_id_override=self.options.doc_id_override,
            version_tag=self.options.version_tag,
        )
        source_path = self.options.source_path or (context.work_dir / "rag.glossary.suggested.yaml")
        target_path = self.options.target_path or (context.work_dir / "rag.glossary.yaml")
        if not source_path.exists():
            raise PipelineError(f"Suggestion glossaire introuvable: {source_path}")
        payload = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
        rules = payload.get("rules") or []
        if self.options.keep_top is not None:
            rules = rules[: max(0, int(self.options.keep_top))]
        if not rules:
            raise PipelineError("Aucune règle disponibile dans le glossaire proposé.")
        final_payload = {
            "version": payload.get("version", 1),
            "doc_id": context.doc_id,
            "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat(),
            "source": str(source_path),
            "rules": [
                {"pattern": str(rule["pattern"]), "replacement": str(rule["replacement"])}
                for rule in rules
                if rule.get("pattern") and rule.get("replacement") is not None
            ],
        }
        if not final_payload["rules"]:
            raise PipelineError("Aucune règle valide à appliquer.")
        write_glossary_file(target_path, final_payload)
        rules_count = len(final_payload["rules"])
        self._write_stamp(context, rules_count)
        self.logger.info("Glossaire validé écrit dans %s (%d règles).", target_path, rules_count)
        return target_path

    def _write_stamp(self, context: DocumentContext, rules_count: int) -> None:
        source_path = select_source_for_stamp(context)
        if not source_path:
            self.logger.warning("Source introuvable pour .lexicon_ok.json dans %s", context.work_dir)
            return
        source_sha = compute_file_sha256(source_path)
        if not source_sha:
            self.logger.warning("SHA256 introuvable pour %s", source_path)
            return
        write_lexicon_stamp(
            context.work_dir,
            doc_id=context.doc_id,
            source_path=source_path,
            source_sha=source_sha,
            rules_count=rules_count,
        )


class HeuristicLexiconAnalyzer:
    """Detect unusual word variants using simple heuristics."""

    def __init__(self, texts: Sequence[str], *, min_count: int = 2):
        self.texts = list(texts)
        self.min_count = max(1, min_count)
        self.sequence_stats: Dict[Tuple[str, ...], SequenceStat] = {}
        self.scanned_tokens: int = 0
        self.token_counts: Counter = Counter()
        self.rare_threshold = max(2, self.min_count + 1)

    def build_suggestions(self, *, top_k: int) -> List[Suggestion]:
        self._collect_sequences()
        candidates = {key: stat for key, stat in self.sequence_stats.items() if stat.count >= self.min_count}
        if not candidates:
            return []
        sorted_keys = sorted(candidates.keys(), key=lambda key: (-candidates[key].count, key))
        suggestions: List[Suggestion] = []
        seen_patterns = set()
        suggestions.extend(self._build_mojibake_suggestions(candidates, seen_patterns, top_k))
        if len(suggestions) >= top_k:
            return suggestions[:top_k]
        for key in sorted_keys:
            source_stat = candidates[key]
            if not source_stat.forms:
                continue
            source_form = source_stat.forms.most_common(1)[0][0]
            if not self._is_candidate_sequence(key, source_form):
                continue
            if not self._is_interesting_sequence(key):
                continue
            target_key, similarity = self._best_match(key, candidates)
            if not target_key:
                continue
            target_stat = candidates[target_key]
            if target_stat.count < source_stat.count:
                continue
            target_form = target_stat.forms.most_common(1)[0][0]
            if not self._is_candidate_sequence(target_key, target_form):
                continue
            if target_key == key:
                continue
            replacement = target_form
            if replacement.strip().lower() == source_form.strip().lower():
                continue
            pattern = build_pattern(key)
            if not pattern or pattern in seen_patterns:
                continue
            confidence = self._confidence(similarity, source_stat.count, target_stat.count, source_form, replacement)
            evidence = source_stat.examples[:2]
            suggestions.append(
                Suggestion(
                    source=" ".join(key),
                    target=" ".join(target_key),
                    pattern=pattern,
                    replacement=replacement,
                    confidence=confidence,
                    evidence=evidence,
                )
            )
            seen_patterns.add(pattern)
            if len(suggestions) >= top_k:
                break
        return suggestions

    def _collect_sequences(self) -> None:
        for text in self.texts:
            if not text:
                continue
            sentences = split_sentences(text)
            for sentence in sentences:
                tokens = self._extract_tokens(sentence)
                if not tokens:
                    continue
                norm_sequence = [token["normalized"] for token in tokens]
                original_sequence = [token["original"] for token in tokens]
                self._register_sequences(norm_sequence, original_sequence, sentence)

    def _extract_tokens(self, sentence: str) -> List[Dict[str, str]]:
        tokens: List[Dict[str, str]] = []
        for match in WORD_PATTERN.finditer(sentence):
            original = match.group(0)
            normalized = fix_mojibake(original).lower()
            tokens.append({"original": original, "normalized": normalized})
            self.scanned_tokens += 1
        return tokens

    def _register_sequences(
        self,
        normalized_tokens: List[str],
        original_tokens: List[str],
        sentence: str,
    ) -> None:
        length = len(normalized_tokens)
        max_n = min(3, length)
        for n in range(1, max_n + 1):
            for idx in range(0, length - n + 1):
                window_norm = tuple(normalized_tokens[idx : idx + n])
                window_orig = " ".join(original_tokens[idx : idx + n])
                stat = self.sequence_stats.setdefault(window_norm, SequenceStat())
                stat.add(window_orig, sentence)

    def _best_match(self, key: Tuple[str, ...], candidates: Dict[Tuple[str, ...], SequenceStat]):
        source_stat = candidates[key]
        best_key = None
        best_ratio = 0.0
        for other_key, other_stat in candidates.items():
            if other_key == key or other_stat.count < source_stat.count:
                continue
            if len(other_key) != len(key):
                continue
            ratio = SequenceMatcher(None, " ".join(key), " ".join(other_key)).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_key = other_key
        if best_ratio < 0.8:
            return None, 0.0
        return best_key, best_ratio

    def _confidence(
        self,
        similarity: float,
        source_count: int,
        target_count: int,
        source_form: str,
        replacement: str,
    ) -> float:
        modifier = 1.0
        if detect_mojibake(source_form):
            modifier += 0.2
        if replacement.istitle() and not source_form.istitle():
            modifier += 0.1
        freq_ratio = min(1.0, target_count / max(1, source_count))
        score = similarity * 0.7 + freq_ratio * 0.3
        return round(min(1.0, score * modifier), 3)

    def _is_candidate_sequence(self, key: Tuple[str, ...], form: str) -> bool:
        letters = [ch for ch in form if ch.isalpha()]
        if len(letters) < 4:
            return False
        if len(key) == 1 and key[0] in STOPWORDS:
            return False
        return True

    def _is_interesting_sequence(self, key: Tuple[str, ...]) -> bool:
        for token in key:
            if token in STOPWORDS:
                continue
            if self.token_counts.get(token, 0) <= self.rare_threshold:
                return True
        return False

    def _build_mojibake_suggestions(
        self,
        candidates: Dict[Tuple[str, ...], SequenceStat],
        seen_patterns: set,
        top_k: int,
    ) -> List[Suggestion]:
        results: List[Suggestion] = []
        for key, stat in candidates.items():
            for form, count in stat.forms.items():
                if count < self.min_count:
                    continue
                if not detect_mojibake(form):
                    continue
                literal_key = literal_key_for_form(form)
                if not literal_key:
                    continue
                if not self._is_candidate_sequence(literal_key, form):
                    continue
                pattern = build_pattern(literal_key)
                if not pattern or pattern in seen_patterns:
                    continue
                replacement = fix_mojibake(form)
                if replacement == form or not replacement.strip():
                    continue
                evidence = stat.examples[:2]
                suggestion = Suggestion(
                    source=" ".join(literal_key),
                    target=" ".join(literal_key_for_form(replacement) or literal_key),
                    pattern=pattern,
                    replacement=replacement,
                    confidence=0.85,
                    evidence=evidence,
                )
                results.append(suggestion)
                seen_patterns.add(pattern)
                if len(results) >= top_k:
                    return results
        return results


def split_sentences(text: str) -> List[str]:
    cleaned = fix_mojibake(text)
    parts = re.split(r"(?<=[.!?\n])\s+", cleaned)
    return [part for part in parts if part.strip()]


def build_pattern(key: Tuple[str, ...]) -> str:
    tokens = sanitize_tokens(key)
    if not tokens:
        return ""
    pieces = [re.escape(part) for part in tokens]
    if len(pieces) == 1:
        body = pieces[0]
    else:
        body = r"\s+".join(pieces)
    return r"\b" + body + r"\b"


def sanitize_tokens(tokens: Sequence[str]) -> Tuple[str, ...]:
    filtered = []
    for token in tokens:
        token = token.strip().lower()
        if not token:
            continue
        if any(ch.isalpha() for ch in token):
            filtered.append(token)
    if filtered:
        return tuple(filtered)
    normalized = [token.strip().lower() for token in tokens if token.strip()]
    if normalized:
        return (normalized[0],)
    return tuple()


def literal_key_for_form(form: str) -> Tuple[str, ...]:
    tokens = [match.lower() for match in WORD_PATTERN.findall(form)]
    return tuple(tokens)


def select_source_for_stamp(context: DocumentContext) -> Optional[Path]:
    work = context.work_dir
    if not work:
        return None
    candidates = [
        work / "05_polished.json",
        work / "04_cleaned.json",
        work / "02_merged_raw.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def write_lexicon_stamp(
    work_dir: Path,
    *,
    doc_id: str,
    source_path: Optional[Path],
    source_sha: Optional[str],
    rules_count: int,
) -> None:
    stamp_path = work_dir / STAMP_FILENAME
    payload = {
        "doc": doc_id,
        "source_file": source_path.name if source_path else None,
        "source_sha256": source_sha,
        "rules_count": rules_count,
        "updated_at_utc": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    stamp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_document_context(
    input_path: Path,
    config_bundle,
    logger,
    *,
    doc_id_override: Optional[str],
    version_tag: Optional[str],
) -> DocumentContext:
    input_path = input_path.expanduser().resolve()
    if not pieces:
        return ""
    if len(pieces) == 1:
        body = pieces[0]
    else:
        body = r"\s+".join(pieces)
    return r"\b" + body + r"\b"


def resolve_document_context(
    input_path: Path,
    config_bundle,
    logger,
    *,
    doc_id_override: Optional[str],
    version_tag: Optional[str],
) -> DocumentContext:
    input_path = input_path.expanduser().resolve()
    resolver = InputResolver(PROJECT_ROOT, logger)
    config_effective = config_bundle.effective if isinstance(config_bundle.effective, dict) else {}
    doc_cfg = config_effective.get("doc_id", {})
    try:
        resolved = resolver.resolve(input_path)
        doc_id = resolve_doc_id(
            resolved.doc_title,
            str(resolved.media_path or resolved.work_dir),
            doc_cfg,
            doc_id_override,
        )
        texts = collect_texts_from_work(resolved)
        return DocumentContext(
            doc_id=doc_id,
            doc_title=resolved.doc_title,
            work_dir=resolved.work_dir,
            texts=texts,
            resolved=resolved,
            rag_dir=None,
        )
    except PipelineError:
        rag_dir = resolve_rag_directory(
            input_path,
            version_tag=version_tag,
            doc_id_override=doc_id_override,
            config_bundle=config_bundle,
            logger=logger,
        )
        manifest_path = rag_dir / "document.json"
        if not manifest_path.exists():
            raise PipelineError(f"document.json introuvable dans {rag_dir}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        doc_id = doc_id_override or manifest.get("doc_id")
        if not doc_id:
            raise PipelineError("doc_id introuvable dans manifest.")
        doc_title = manifest.get("doc_title") or doc_id
        work_dir = resolve_work_dir_from_manifest(manifest) or rag_dir
        texts = collect_texts_from_rag_dir(rag_dir)
        return DocumentContext(
            doc_id=doc_id,
            doc_title=doc_title,
            work_dir=work_dir,
            texts=texts,
            rag_dir=rag_dir,
            resolved=None,
        )


def collect_texts_from_work(resolved: ResolvedPaths) -> List[str]:
    texts: List[str] = []
    if resolved.clean_txt_path and resolved.clean_txt_path.exists():
        texts.append(resolved.clean_txt_path.read_text(encoding="utf-8"))
    elif resolved.chunks_path and resolved.chunks_path.exists():
        texts.extend(read_chunks_text(resolved.chunks_path))
    else:
        texts.extend(read_segments_text(resolved.polished_path))
    return texts


def collect_texts_from_rag_dir(rag_dir: Path) -> List[str]:
    texts: List[str] = []
    segments_path = rag_dir / "segments.jsonl"
    if segments_path.exists():
        texts.extend(read_segments_jsonl(segments_path))
    chunks_path = rag_dir / "chunks.jsonl"
    if chunks_path.exists():
        texts.extend(read_chunks_text(chunks_path))
    return texts


def read_chunks_text(path: Path) -> List[str]:
    texts: List[str] = []
    if not path.exists():
        return texts
    if path.suffix == ".jsonl":
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            content = record.get("text")
            if content:
                texts.append(content)
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("chunks") if isinstance(payload, dict) else payload
        if isinstance(records, list):
            for record in records:
                content = (record or {}).get("text")
                if content:
                    texts.append(content)
    return texts


def read_segments_text(path: Path) -> List[str]:
    texts: List[str] = []
    payload = json.loads(path.read_text(encoding="utf-8"))
    for segment in payload.get("segments", []):
        text = segment.get("text_human") or segment.get("text")
        if text:
            texts.append(text)
    return texts


def read_segments_jsonl(path: Path) -> List[str]:
    texts: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        content = record.get("text")
        if content:
            texts.append(content)
    return texts


def resolve_work_dir_from_manifest(manifest: Dict[str, Any]) -> Optional[Path]:
    provenance = manifest.get("provenance") or {}
    segments = provenance.get("segments") or {}
    path_str = segments.get("path")
    if not path_str:
        return None
    candidate = (PROJECT_ROOT / path_str).resolve()
    if candidate.exists():
        return candidate.parent
    return None
