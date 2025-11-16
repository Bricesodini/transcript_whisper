import argparse
import datetime as dt
import hashlib
import json
import logging
import platform
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from audit import AuditReporter
from align import Aligner
from asr import ASRProcessor
from clean import Cleaner
from chunker import Chunker
from diarize import Diarizer
from export import Exporter
from glossary import GlossaryManager
from merger import DeterministicMerger
from polish import Polisher
from preproc import Preprocessor
from refine import SegmentRefiner
from segmenter import Segmenter
from structure import Structurer
from utils import (
    PipelineError,
    apply_thread_env,
    compute_post_threads,
    load_config,
    normalize_media_path,
    prepare_paths,
    read_json,
    select_profile,
    setup_logger,
    stable_id,
    stage_timer,
    write_json,
)

COMMANDS = ("run", "prepare", "asr", "merge", "align", "post", "export", "resume", "dry-run")
SCHEMA_VERSION = "1.0.0"

ROOT_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT_DIR / "tools"
if TOOLS_DIR.exists():
    tools_str = str(TOOLS_DIR)
    if tools_str not in sys.path:
        sys.path.append(tools_str)
try:
    from update_arte_outputs import refresh_arte_outputs as outputs_polisher
except Exception:  # pragma: no cover - optional dependency
    outputs_polisher = None


def parse_args():
    parser = argparse.ArgumentParser(description="Suite de transcription modulaire (Transcribe Suite).")
    parser.set_defaults(strict=None, fail_fast=None, no_partial_export=None)
    parser.add_argument(
        "command",
        nargs="?",
        choices=COMMANDS,
        default="run",
        help="Commande à exécuter (défaut: run)",
    )
    parser.add_argument("--input", required=True, help="Chemin du média audio/vidéo à traiter")
    parser.add_argument("--config", required=True, help="Fichier de configuration YAML")
    parser.add_argument("--lang", default=None, help="Langue forcée (fr, en, auto)")
    parser.add_argument("--profile", default=None, help="Profil de configuration (default, talkshow, conference)")
    parser.add_argument("--export", default=None, help="Formats à exporter (csv de txt,md,json,srt,vtt)")
    parser.add_argument("--initial-prompt", default=None, help="Prompt initial pour l'ASR")
    parser.add_argument("--skip-diarization", action="store_true", help="Désactive Pyannote (debug/seulement ASR)")
    parser.add_argument("--keep-build", action="store_true", help="Ne pas supprimer les artefacts temporaires")
    parser.add_argument("--verbose", action="store_true", help="Afficher les logs détaillés (équivalent --log-level debug)")
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"], default="info", help="Niveau de log console")
    parser.add_argument("--force", action="store_true", help="Rejouer la commande même si les artefacts existent")
    parser.add_argument("--strict", dest="strict", action="store_true", help="Active le mode strict")
    parser.add_argument("--no-strict", dest="strict", action="store_false", help="Désactive le mode strict")
    parser.add_argument("--fail-fast", dest="fail_fast", action="store_true", help="Arrêt au premier segment en échec")
    parser.add_argument("--no-fail-fast", dest="fail_fast", action="store_false", help="Continue malgré les segments en échec")
    parser.add_argument("--no-partial-export", dest="no_partial_export", action="store_true", help="Interdit les exports si une étape échoue")
    parser.add_argument("--allow-partial-export", dest="no_partial_export", action="store_false", help="Autorise des exports partiels")
    parser.add_argument("--only-failed", action="store_true", help="Rejoue uniquement les segments FAILED lors d'un resume/asr")
    parser.add_argument("--mode", choices=["mono", "multi"], default="mono", help="Profil diarisation (mono par défaut)")
    parser.add_argument("--diarization-max-speakers", type=int, dest="diarization_max_speakers", help="Override diarization max_speakers")
    parser.add_argument("--diarization-min-speaker-turn", type=float, dest="diarization_min_speaker_turn", help="Override diarization min_speaker_turn")
    parser.add_argument("--diarization-monologue", action="store_true", help="Force max_speakers=1,min_turn=1.3")
    parser.add_argument("--low-confidence-threshold", type=float, dest="low_conf_threshold", help="Override low-confidence threshold")
    parser.add_argument("--low-confidence-out", dest="low_conf_out", help="Chemin CSV low-confidence")
    parser.add_argument("--chapters-min-duration", type=float, dest="chapters_min_duration", help="Soft min duration (s) pour forcer les chapitres")
    parser.add_argument("--compute-type", dest="compute_type", help="Override faster-whisper compute_type (int8/float16/auto)")
    parser.add_argument("--chunk-length", dest="chunk_length", type=float, help="Override chunk_length (s) pour Faster-Whisper")
    parser.add_argument("--asr-workers", dest="asr_workers", type=int, help="Override max_workers ASR parallèles")
    parser.add_argument("--vad", dest="vad_filter", action="store_true", help="Force le VAD interne Faster-Whisper")
    parser.add_argument("--no-vad", dest="vad_filter", action="store_false", help="Désactive le VAD interne Faster-Whisper")
    parser.add_argument("--condition-off", dest="condition_off", action="store_true", help="Désactive condition_on_previous_text pour l'ASR")
    parser.add_argument("--align-workers", dest="align_workers", type=int, help="Workers WhisperX align (num_workers)")
    parser.add_argument("--align-batch", dest="align_batch", type=int, help="Batch size WhisperX align")
    parser.add_argument("--speech-only", dest="speech_only", action="store_true", help="Aligne uniquement les segments marqués speech")
    parser.add_argument("--no-speech-only", dest="speech_only", action="store_false", help="Désactive le filtrage speech-only")
    parser.add_argument("--diar-device", dest="diar_device", help="Device Pyannote (cpu/cuda/mps)")
    parser.add_argument("--seg-batch", dest="seg_batch", type=int, help="Batch size segmentation Pyannote")
    parser.add_argument("--emb-batch", dest="emb_batch", type=int, help="Batch size embedding Pyannote")
    parser.add_argument("--num-speakers", dest="num_speakers", type=int, help="Nombre de speakers attendu (hint)")
    parser.add_argument("--speech-mask", dest="speech_mask", action="store_true", help="Applique un masque speech aux étapes post-ASR")
    parser.add_argument("--no-speech-mask", dest="speech_mask", action="store_false", help="Désactive le masque speech")
    parser.add_argument("--export-parallel", dest="export_parallel", action="store_true", help="Exports finaux en parallèle")
    parser.add_argument("--export-serial", dest="export_parallel", action="store_false", help="Exports finaux en série")
    parser.add_argument("--dry-run", action="store_true", help="Exécute les étapes jusqu'à l'audit sans exporter")
    parser.add_argument("--no-audit", action="store_true", help="Désactive la génération de l'audit")
    parser.add_argument("--only", help="Liste d'étapes à exécuter (clean,polish,structure,chunk,audit,export)")
    parser.add_argument(
        "--polish-outputs",
        dest="polish_outputs",
        action="store_true",
        help="Applique le polish final sur les exports (confiances, JSONL enrichis, txt/md nettoyés).",
    )
    parser.set_defaults(vad_filter=None)
    parser.set_defaults(speech_only=None, speech_mask=None, export_parallel=None)
    parser.set_defaults(polish_outputs=False)
    args = parser.parse_args()
    if args.strict is None:
        args.strict = True
    if args.fail_fast is None:
        args.fail_fast = True
    if args.no_partial_export is None:
        args.no_partial_export = True
    return args


class PipelineRunner:
    def __init__(self, args):
        self.args = args
        raw_input = normalize_media_path(args.input)
        if not raw_input:
            raise PipelineError("Chemin d'entrée invalide ou vide.")
        self.media_path = Path(raw_input).expanduser().resolve()
        if not self.media_path.exists():
            raise PipelineError(f"Fichier introuvable: {self.media_path}")

        self.config_path = Path(args.config).expanduser().resolve()
        config = load_config(self.config_path)
        if args.profile:
            config = select_profile(config, args.profile)
        self.config = config
        self.mode = args.mode or "mono"
        self.strict = bool(args.strict)
        self.fail_fast = bool(args.fail_fast)
        self.no_partial_export = bool(args.no_partial_export)
        self.only_failed = bool(args.only_failed)
        self.command = args.command or "run"
        self.log_level = "debug" if args.verbose else (args.log_level or "info")
        self.dry_run = bool(args.dry_run)
        self.no_audit = bool(args.no_audit)
        self.only_stages = self._parse_only(args.only)
        self.allowed_exports = {"md", "json", "vtt"}
        self._apply_overrides(args)
        if self.strict:
            self._validate_config_or_raise()
        self.paths = prepare_paths(self.config_path.parent.parent, self.config)

        self.work_root = self.paths["work_dir"]
        self.work_dir = self.work_root / self.media_path.stem
        self.work_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("00_segments", "01_asr_jsonl", "logs", "cache"):
            (self.work_dir / sub).mkdir(parents=True, exist_ok=True)
        self.audio_path = self.work_dir / "audio_16k.wav"
        manifest_name = config.get("segmenter", {}).get("manifest_name", "manifest.csv")
        self.manifest_path = self.work_dir / manifest_name
        self.state_path = self.work_dir / "manifest_state.json"

        transcript_dir = f"TRANSCRIPT - {self.media_path.stem}"
        self.out_dir = self.media_path.parent / transcript_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.global_log_dir = self.paths.get("logs_dir", self.work_dir / "logs")
        self.global_log_dir.mkdir(parents=True, exist_ok=True)
        self.local_log_dir = self.work_dir / "logs"
        run_name = f"{self.media_path.stem}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.logger = setup_logger(self.global_log_dir, run_name, log_level=self.log_level)
        local_log_path = self.local_log_dir / f"{run_name}.log"
        if self.global_log_dir.resolve() != self.local_log_dir.resolve():
            local_log_path.parent.mkdir(parents=True, exist_ok=True)
            extra_handler = logging.FileHandler(local_log_path, encoding="utf-8")
            extra_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
            extra_handler.setLevel(logging.DEBUG)
            self.logger.addHandler(extra_handler)

        self.requested_lang = args.lang or config.get("defaults", {}).get("lang", "auto")
        self.detect_lang = config.get("defaults", {}).get("detect_language", True)
        self.export_formats: Optional[List[str]] = (
            [fmt.strip() for fmt in args.export.split(",") if fmt.strip()] if args.export else None
        )
        if self.strict and self.export_formats:
            invalid = [fmt for fmt in self.export_formats if fmt not in self.allowed_exports]
            if invalid:
                raise PipelineError(f"Formats non autorisés en mode strict: {', '.join(invalid)}")
        if self.strict and not self.export_formats:
            self.export_formats = sorted(self.allowed_exports)
        self.export_parallel = bool(config.get("export", {}).get("parallel", False))
        self.force = bool(args.force)
        self.initial_prompt = args.initial_prompt
        self.skip_diarization = bool(args.skip_diarization)
        self.keep_build = bool(args.keep_build)
        self._run_start = time.time()
        self.run_stats: Dict[str, Any] = {"stages": {}, "success": None}
        self.asr_metrics: Optional[Dict[str, Any]] = None
        self.input_hash = self._compute_input_hash(self.media_path)

        self.preferred_align_lang = self.requested_lang if self.requested_lang and self.requested_lang != "auto" else "fr"
        self._models_prewarmed = False
        self.post_threads = compute_post_threads()
        self._post_env_applied = False
        self.outputs_cfg = config.get("outputs", {})
        self.numbers_cfg = config.get("numbers", {})
        self.typography_cfg = config.get("typography", {})
        self.post_state_path = self.work_dir / "post_state.json"
        self.polish_outputs_enabled = bool(getattr(args, "polish_outputs", False))
        self.post_state = self._load_post_state()
        self.config_signature = {
            "outputs": self.outputs_cfg,
            "numbers": self.numbers_cfg,
            "typography": self.typography_cfg,
        }
        self.config_hash = self._hash_dict(self.config_signature)
        self.config_changed = self.post_state.get("config_hash") != self.config_hash

        config = self.config
        self.preproc = Preprocessor(config, self.logger)
        self.segmenter = Segmenter(config, self.logger)
        self.asr = ASRProcessor(config, self.logger)
        self.merger = DeterministicMerger(config, self.logger)
        self.glossary = GlossaryManager(config.get("glossary", {}), self.logger, config_root=self.config_path.parent)
        self.cleaner = Cleaner(config, self.logger, glossary=self.glossary)
        self.polisher = Polisher(
            config.get("polish", {}),
            self.logger,
            glossary=self.glossary,
            numbers_cfg=config.get("numbers", {}),
            typography_cfg=config.get("typography", {}),
        )
        self.structurer = Structurer(config, self.logger)
        self.chunker = Chunker(config.get("chunking", {}), self.logger)
        self.audit = AuditReporter(config.get("audit", {}), self.logger)
        self.exporter = Exporter(config, self.logger)
        self.aligner = Aligner(config, self.logger)
        self.diarizer = None if self.skip_diarization else Diarizer(config, self.logger)
        self.refiner = SegmentRefiner(config, self.logger, self.asr)
        confidence_cfg = config.get("cleaning", {}).get("confidence", {})
        self.low_conf_threshold = float(confidence_cfg.get("sentence_threshold") or confidence_cfg.get("segment_threshold") or 0.55)
        self.low_conf_p05_threshold = float(confidence_cfg.get("p05_threshold", self.low_conf_threshold - 0.1))
        self.artifacts_info: Dict[str, Dict[str, Any]] = {}

        self.seg_info: Optional[Dict[str, Path]] = None
        self.asr_info: Optional[Dict[str, Any]] = None
        self.merged_info: Optional[Dict[str, Any]] = None
        self.diarization_result: Optional[Dict[str, Any]] = None
        self.align_info: Optional[Dict[str, Any]] = None
        self.post_info: Optional[Dict[str, Any]] = None

        self.logger.info("=== Transcribe Suite ===")
        self.logger.info("Commande: %s", self.command)
        self.logger.info("Media: %s", self.media_path)
        self.logger.info("Langue demandée: %s", self.requested_lang)
        self.logger.info("Profil: %s", args.profile or config.get("defaults", {}).get("profile", "default"))

    def _parse_only(self, raw: Optional[str]) -> Optional[List[str]]:
        if not raw:
            return None
        allowed = {"clean", "polish", "structure", "chunk", "audit", "export"}
        expanded: List[str] = []
        for part in raw.split(","):
            token = part.strip().lower()
            if not token:
                continue
            if token not in allowed:
                raise PipelineError(f"Stage inconnu pour --only: {token}")
            expanded.append(token)
        return expanded or None

    def _apply_overrides(self, args) -> None:
        config = self.config
        diar_cfg = config.setdefault("diarization", {})
        if self.mode == "mono" or getattr(args, "diarization_monologue", False):
            diar_cfg["max_speakers"] = 1
            diar_cfg["min_speaker_turn"] = max(float(diar_cfg.get("min_speaker_turn", 0.0) or 0.0), 1.3)
        if self.mode == "multi" and args.diarization_max_speakers is None and "max_speakers" not in diar_cfg:
            diar_cfg["max_speakers"] = 2
        if args.diarization_max_speakers is not None:
            diar_cfg["max_speakers"] = max(1, int(args.diarization_max_speakers))
        if args.diarization_min_speaker_turn is not None:
            diar_cfg["min_speaker_turn"] = float(args.diarization_min_speaker_turn)

        export_cfg = config.setdefault("export", {})
        low_cfg = export_cfg.setdefault("low_confidence", {})
        if args.low_conf_threshold is not None:
            threshold_value = float(args.low_conf_threshold)
            low_cfg["threshold"] = threshold_value
            low_cfg["csv_threshold"] = threshold_value
        if args.low_conf_out:
            low_cfg["csv_output"] = args.low_conf_out

        if args.chapters_min_duration is not None:
            struct_cfg = config.setdefault("structure", {})
            struct_cfg["soft_min_duration"] = float(args.chapters_min_duration)

        asr_cfg = config.setdefault("asr", {})
        if args.compute_type:
            asr_cfg["compute_type"] = args.compute_type
        if args.chunk_length is not None:
            asr_cfg["chunk_length"] = float(args.chunk_length)
        if args.asr_workers is not None:
            asr_cfg["max_workers"] = max(1, int(args.asr_workers))
        if args.vad_filter is not None:
            asr_cfg["vad_filter"] = bool(args.vad_filter)
        if getattr(args, "condition_off", False):
            asr_cfg["condition_on_previous_text"] = False
        if args.export_parallel is not None:
            export_cfg["parallel"] = bool(args.export_parallel)

        align_cfg = config.setdefault("align", {})
        if args.align_workers is not None:
            align_cfg["workers"] = max(1, int(args.align_workers))
        if args.align_batch is not None:
            align_cfg["batch_size"] = max(1, int(args.align_batch))
        if args.speech_only is not None:
            align_cfg["speech_only"] = bool(args.speech_only)

        diar_cfg = config.setdefault("diarization", {})
        if args.diar_device:
            diar_cfg["device"] = args.diar_device
        if args.seg_batch is not None:
            diar_cfg["segmentation_batch"] = max(1, int(args.seg_batch))
        if args.emb_batch is not None:
            diar_cfg["embedding_batch"] = max(1, int(args.emb_batch))
        if args.num_speakers is not None:
            diar_cfg["max_speakers"] = max(1, int(args.num_speakers))
        if args.speech_mask is not None:
            diar_cfg["speech_mask"] = bool(args.speech_mask)

    def _ensure_post_env(self) -> None:
        apply_thread_env("POST_THREADS", self.post_threads)

    def _validate_config_or_raise(self) -> None:
        required_sections = {
            "paths": ["work_dir", "exports_dir", "logs_dir"],
            "defaults": ["lang", "model", "export_formats"],
            "segmenter": ["segment_length", "overlap", "manifest_name"],
            "asr": ["device", "beam_size", "no_speech_threshold", "max_workers"],
            "diarization": ["max_speakers", "min_speaker_turn"],
            "align": ["language"],
            "export": ["low_confidence"],
        }
        missing = []
        for section, keys in required_sections.items():
            if section not in self.config:
                missing.append(section)
                continue
            for key in keys:
                value = self.config[section].get(key)
                if value in (None, ""):
                    raise PipelineError(f"Configuration stricte: '{section}.{key}' manquant")
        if missing:
            raise PipelineError(f"Configuration stricte: sections manquantes: {', '.join(missing)}")
        defaults = self.config.get("defaults", {})
        if defaults.get("detect_language", False):
            raise PipelineError("Mode strict: detect_language doit être désactivé")
        formats = defaults.get("export_formats") or []
        if set(formats) != self.allowed_exports:
            raise PipelineError("Mode strict: export_formats doit être [md,json,vtt]")
        low_cfg = self.config.get("export", {}).get("low_confidence", {})
        if not low_cfg.get("csv_enabled"):
            raise PipelineError("Mode strict: low_confidence.csv doit être activé")

    # ---- helpers ----
    def prewarm_dependencies(self) -> None:
        if self._models_prewarmed:
            return
        self.logger.info("Pré-chargement des modèles (ASR + WhisperX)")
        self.asr.ensure_model_cached()
        self.aligner.prewarm(self.preferred_align_lang)
        self._models_prewarmed = True

    def ensure_audio_ready(self) -> Path:
        if self.audio_path.exists() and not self.force:
            return self.audio_path
        with stage_timer(self.logger, "Prétraitement audio 16 kHz"):
            self.audio_path = self.preproc.run(self.media_path, self.work_dir, force=self.force)
        return self.audio_path

    def ensure_segmentation(self) -> Dict[str, Path]:
        if self.seg_info and self.manifest_path.exists() and not self.force:
            return self.seg_info
        audio_path = self.ensure_audio_ready()
        with stage_timer(self.logger, "Segmentation 75s + overlap"):
            self.seg_info = self.segmenter.run(audio_path, self.work_dir, force=self.force)
        return self.seg_info

    # ---- stages ----
    def stage_prepare(self) -> Dict[str, Path]:
        stage_start = time.time()
        self.logger.info("▶ PREPARE")
        result = self.ensure_segmentation()
        self._mark_stage_duration("prepare", stage_start)
        return result

    def stage_asr(self) -> Dict[str, Any]:
        stage_start = time.time()
        self.logger.info("▶ ASR")
        seg_info = self.ensure_segmentation()
        self.prewarm_dependencies()
        with stage_timer(self.logger, "ASR parallèle Faster-Whisper"):
            self.asr_info = self.asr.run(
                manifest_path=seg_info["manifest"],
                work_dir=self.work_dir,
                requested_lang=self.requested_lang,
                detect_lang=self.detect_lang,
                initial_prompt=self.initial_prompt,
                force=self.force,
                fail_fast=self.fail_fast,
                only_failed=self.only_failed,
            )
        self._mark_stage_duration("asr", stage_start)
        self.asr_metrics = self.asr_info.get("metrics")
        failed_segments = self.asr_info.get("failed_segments", [])
        if failed_segments:
            msg = (
                "Segments ASR en échec: "
                + ", ".join(str(idx) for idx in failed_segments)
                + " ➜ relance 'resume --only-failed'"
            )
            if self.no_partial_export:
                raise PipelineError(msg)
            self.logger.warning(msg)
        return self.asr_info

    def stage_merge(self) -> Dict[str, Any]:
        stage_start = time.time()
        self.logger.info("▶ MERGE")
        asr_result = self.asr_info or self.stage_asr()
        with stage_timer(self.logger, "Fusion déterministe des segments"):
            self.merged_info = self.merger.run(
                manifest_path=asr_result["manifest"],
                jsonl_dir=asr_result["jsonl_dir"],
                work_dir=self.work_dir,
                language=asr_result["language"],
                force=self.force,
            )
        self._mark_stage_duration("merge", stage_start)
        return self.merged_info

    def stage_diarization(self) -> Dict[str, Any]:
        stage_start = time.time()
        self._ensure_post_env()
        if self.skip_diarization or self.diarizer is None:
            if not self.diarization_result:
                self.logger.warning("Diarisation désactivée (option --skip-diarization)")
                self.diarization_result = {"segments": []}
            self._mark_stage_duration("diarize", stage_start)
            return self.diarization_result
        if self.diarization_result and not self.force:
            self._mark_stage_duration("diarize", stage_start)
            return self.diarization_result
        audio_path = self.ensure_audio_ready()
        merged = self.merged_info or self.stage_merge()
        speech_segments = None
        if getattr(self.diarizer, "speech_mask_enabled", False) and merged:
            speech_segments = merged.get("segments")
        with stage_timer(self.logger, "Diarisation Pyannote"):
            self.diarization_result = self.diarizer.run(audio_path, self.work_dir, speech_segments=speech_segments)
        self._mark_stage_duration("diarize", stage_start)
        return self.diarization_result

    def stage_align(self) -> Dict[str, Any]:
        stage_start = time.time()
        self.logger.info("▶ ALIGN")
        self._ensure_post_env()
        merged = self.merged_info or self.stage_merge()
        diarization_result = self.stage_diarization()
        audio_path = self.ensure_audio_ready()
        target_language = self.asr_info.get("language") if self.asr_info else self.requested_lang
        language = target_language if target_language not in (None, "", "auto") else self.preferred_align_lang
        self.aligner.prewarm(language)
        asr_like = {"language": language, "segments": merged["segments"]}
        with stage_timer(self.logger, "Alignement WhisperX"):
            self.align_info = self.aligner.run(
                audio_path,
                asr_like,
                diarization_result,
                self.work_dir,
                force=self.force,
            )
        self._mark_stage_duration("align", stage_start)
        return self.align_info

    def stage_post(self) -> Dict[str, Any]:
        stage_start = time.time()
        self.logger.info("▶ POST")
        self._ensure_post_env()
        align_result = self.align_info or self.stage_align()
        segments_for_clean = align_result["segments"]
        audio_path = self.ensure_audio_ready()
        if self.refiner.enabled and segments_for_clean:
            with stage_timer(self.logger, "Re-ASR ciblé"):
                segments_for_clean = self.refiner.run(audio_path, segments_for_clean, align_result["language"], self.work_dir)
        language = align_result["language"]
        doc_id = self.media_path.stem

        cleaned_segments, clean_report = self._ensure_clean_stage(segments_for_clean, language)
        polished_segments, polish_report = self._ensure_polish_stage(cleaned_segments, language)
        structure_data = self._ensure_structure_stage(polished_segments, language, doc_id)
        chunks = self._ensure_chunk_stage(structure_data, language, doc_id)
        self._annotate_sentences_with_chunks(structure_data, chunks)
        sentences = self._flatten_sentences(structure_data)

        clean_artifacts = self._write_clean_artifacts(sentences, doc_id, language)
        chunk_artifacts = self._write_chunk_artifacts(structure_data, chunks, doc_id, language)
        low_conf_entries = self._collect_low_conf_spans(structure_data, chunks, language)
        low_conf_path = self._write_low_conf_jsonl(low_conf_entries, doc_id)
        metrics_payload = self._build_quality_metrics(
            sentences,
            chunks,
            clean_report,
            polish_report,
            low_conf_entries,
            structure_data,
        )
        self._write_metrics_file(metrics_payload, clean_artifacts, chunk_artifacts, low_conf_path)

        audit_path = None
        low_conf_examples = low_conf_entries[: self.audit.max_examples] if low_conf_entries else []
        if self.audit.enabled and not self.no_audit:
            audit_text = self.audit.render(
                media_name=doc_id,
                language=language,
                clean_report=clean_report,
                polish_report=polish_report,
                structure=structure_data,
                chunks=chunks,
                metrics=metrics_payload,
                low_conf_entries=low_conf_examples,
                low_conf_path=low_conf_path,
                glossary_conflicts=self.glossary.conflicts_summary(),
            )
            audit_path = self.out_dir / f"{doc_id}.audit.md"
            audit_path.write_text(audit_text, encoding="utf-8")
            self.logger.info("Audit ➜ %s", audit_path)
        elif self.no_audit:
            self.logger.info("Audit désactivé (--no-audit)")

        self.post_info = {
            "cleaned": cleaned_segments,
            "polished": polished_segments,
            "structure": structure_data,
            "chunks": chunks,
            "clean_report": clean_report,
            "polish_report": polish_report,
            "metrics": metrics_payload,
            "low_conf_path": low_conf_path,
            "clean_artifacts": clean_artifacts,
            "chunk_artifacts": chunk_artifacts,
            "audit_path": audit_path,
        }
        self._save_post_state()
        self._maybe_polish_outputs()
        self._mark_stage_duration("post", stage_start)
        return self.post_info

    def stage_export(self) -> Dict[str, Path]:
        stage_start = time.time()
        self.logger.info("▶ EXPORT")
        self._ensure_post_env()
        post_info = self.post_info or self._load_post_from_disk()
        align_info = self.align_info or self._load_align_from_disk()
        with stage_timer(self.logger, "Exports finaux"):
            artifacts = self.exporter.run(
                base_name=self.media_path.stem,
                out_dir=self.out_dir,
                segments=post_info["polished"],
                structure=post_info["structure"],
                aligned_path=align_info["path"],
                formats=self.export_formats,
                parallel=self.export_parallel,
            )
        for fmt, path in artifacts.items():
            self.logger.info("  %s -> %s", fmt.upper(), path)
        self._mark_stage_duration("export", stage_start)
        return artifacts

    # ---- helpers for cached data ----
    def _load_post_from_disk(self) -> Dict[str, Any]:
        polished_path = self.work_dir / "05_polished.json"
        structure_path = self.work_dir / "structure.json"
        cleaned_path = self.work_dir / "04_cleaned.json"
        chunks_cache = self.work_dir / "chunks.json"
        if not polished_path.exists() or not structure_path.exists() or not cleaned_path.exists():
            raise PipelineError("Post-traitement introuvable: lance d'abord la commande 'post'.")
        polished_payload = self._ensure_schema_payload(read_json(polished_path), polished_path)
        structure_payload = self._ensure_schema_payload(read_json(structure_path), structure_path)
        cleaned_payload = self._ensure_schema_payload(read_json(cleaned_path), cleaned_path)
        chunk_payload = (
            self._ensure_schema_payload(read_json(chunks_cache), chunks_cache).get("chunks", [])
            if chunks_cache.exists()
            else []
        )
        metrics_path = self.out_dir / f"{self.media_path.stem}.metrics.json"
        metrics_payload = read_json(metrics_path) if metrics_path.exists() else {}
        low_conf_path = self.out_dir / f"{self.media_path.stem}.low_confidence.jsonl"
        self.post_info = {
            "cleaned": cleaned_payload.get("segments", []),
            "polished": polished_payload.get("segments", []),
            "structure": structure_payload,
            "chunks": chunk_payload,
            "clean_report": cleaned_payload.get("report", {}),
            "polish_report": polished_payload.get("report", {}),
            "metrics": metrics_payload,
            "low_conf_path": low_conf_path if low_conf_path.exists() else None,
        }
        return self.post_info

    def _load_align_from_disk(self) -> Dict[str, Any]:
        aligned_path = self.work_dir / "03_aligned_whisperx.json"
        if not aligned_path.exists():
            raise PipelineError("Alignement introuvable. Lance la commande 'align'.")
        payload = read_json(aligned_path)
        self.align_info = {
            "segments": payload.get("segments", []),
            "language": payload.get("language", self.requested_lang),
            "path": aligned_path,
        }
        return self.align_info

    def _maybe_polish_outputs(self) -> None:
        if not self.polish_outputs_enabled:
            return
        if outputs_polisher is None:
            self.logger.warning("Option --polish-outputs ignorée (module indisponible).")
            return
        if self.dry_run:
            self.logger.info("Option --polish-outputs ignorée en mode --dry-run.")
            return
        try:
            summary = outputs_polisher(
                self.work_dir,
                self.out_dir,
                doc_id=self.media_path.stem,
                low_threshold=self.low_conf_threshold,
                chunk_low_threshold=getattr(self.chunker, "low_span_threshold", 0.1),
                logger=self.logger,
            )
            if summary:
                self.logger.info(
                    "Polish des exports terminé (%d phrases / %d chunks / %d paragraphes).",
                    summary.get("clean_entries", 0),
                    summary.get("chunk_entries", 0),
                    summary.get("paragraphs", 0),
                )
        except Exception:
            self.logger.exception("Échec du polish final (--polish-outputs).")

    def _should_run_post_stage(self, stage_name: str) -> bool:
        if self.force:
            return True
        if self.only_stages:
            return stage_name in self.only_stages
        if self.command == "resume":
            return self.config_changed
        return True

    def _ensure_clean_stage(self, segments: List[Dict[str, Any]], language: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        cache_path = self.work_dir / "04_cleaned.json"
        input_hash = self._hash_segments(segments)
        if not self.force and cache_path.exists():
            payload = self._ensure_schema_payload(read_json(cache_path), cache_path)
            meta = payload.get("meta", {})
            if not self._should_run_post_stage("clean") and meta.get("input_hash") == input_hash:
                return payload.get("segments", []), payload.get("report", {})
        with stage_timer(self.logger, "Nettoyage linguistique"):
            cleaned_segments = self.cleaner.run(segments, language)
            report = self.cleaner.report()
            payload = {
                "schema_version": SCHEMA_VERSION,
                "language": language,
                "segments": cleaned_segments,
                "report": report,
                "meta": {"input_hash": input_hash},
            }
            write_json(cache_path, payload, indent=2)
        return cleaned_segments, report

    def _ensure_polish_stage(self, segments: List[Dict[str, Any]], language: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        cache_path = self.work_dir / "05_polished.json"
        input_hash = self._hash_segments(segments)
        if not self.force and cache_path.exists():
            payload = self._ensure_schema_payload(read_json(cache_path), cache_path)
            meta = payload.get("meta", {})
            if not self._should_run_post_stage("polish") and meta.get("input_hash") == input_hash:
                return payload.get("segments", []), payload.get("report", {})
        with stage_timer(self.logger, "Polish lecture"):
            polished_segments = self.polisher.run(segments, lang=language)
            report = self.polisher.report()
            payload = {
                "schema_version": SCHEMA_VERSION,
                "language": language,
                "segments": polished_segments,
                "report": report,
                "meta": {"input_hash": input_hash},
            }
            write_json(cache_path, payload, indent=2)
        return polished_segments, report

    def _ensure_structure_stage(self, segments: List[Dict[str, Any]], language: str, doc_id: str) -> Dict[str, Any]:
        cache_path = self.work_dir / "structure.json"
        input_hash = self._hash_segments(segments)
        if not self.force and cache_path.exists():
            payload = self._ensure_schema_payload(read_json(cache_path), cache_path)
            meta = payload.get("meta", {})
            if not self._should_run_post_stage("structure") and meta.get("input_hash") == input_hash:
                return payload
        with stage_timer(self.logger, "Structuration et chapitrage"):
            structure_data = self.structurer.run(segments, language, doc_id, self.low_conf_threshold)
            payload = dict(structure_data)
            payload["meta"] = {"input_hash": input_hash}
            payload["schema_version"] = SCHEMA_VERSION
            write_json(cache_path, payload, indent=2)
            chapters_path = self.out_dir / f"{doc_id}.chapters.json"
            write_json(
                chapters_path,
                {"schema_version": SCHEMA_VERSION, "language": language, "sections": structure_data.get("sections", [])},
                indent=2,
            )
            self.logger.info("Chapitres ➜ %s", chapters_path)
        return payload

    def _ensure_chunk_stage(self, structure: Dict[str, Any], language: str, doc_id: str) -> List[Dict[str, Any]]:
        cache_path = self.work_dir / "chunks.json"
        if not self.chunker.enabled:
            return []
        structure_hash = hashlib.sha1(json.dumps(structure, sort_keys=True).encode("utf-8")).hexdigest()
        if not self.force and cache_path.exists():
            payload = self._ensure_schema_payload(read_json(cache_path), cache_path)
            meta = payload.get("meta", {})
            if not self._should_run_post_stage("chunk") and meta.get("input_hash") == structure_hash:
                return payload.get("chunks", [])
        with stage_timer(self.logger, "Chunking LLM"):
            chunks = self.chunker.run(structure, language, doc_id)
            write_json(
                cache_path,
                {"schema_version": SCHEMA_VERSION, "chunks": chunks, "meta": {"input_hash": structure_hash}},
                indent=2,
            )
        return chunks

    def _annotate_sentences_with_chunks(self, structure: Dict[str, Any], chunks: List[Dict[str, Any]]) -> None:
        if not structure or not chunks:
            return
        lookup = [(chunk["id"], chunk["start"], chunk["end"]) for chunk in chunks]
        for section in structure.get("sections", []):
            for sentence in section.get("sentences", []):
                chunk_id = self._chunk_id_for_timespan(lookup, sentence.get("start"), sentence.get("end"))
                if chunk_id:
                    sentence["chunk_id"] = chunk_id

    def _chunk_id_for_timespan(self, lookup: List[Tuple[str, float, float]], start: float, end: float) -> Optional[str]:
        for chunk_id, chunk_start, chunk_end in lookup:
            if chunk_start - 0.1 <= (start or chunk_start) and (end or start) <= chunk_end + 0.1:
                return chunk_id
        return lookup[-1][0] if lookup else None

    def _hash_segments(self, segments: List[Dict[str, Any]]) -> str:
        canonical = []
        for segment in segments or []:
            canonical.append(
                {
                    "start": round(float(segment.get("start", 0.0) or 0.0), 3),
                    "end": round(float(segment.get("end", segment.get("start", 0.0)) or 0.0), 3),
                    "speaker": segment.get("speaker"),
                    "text": segment.get("text") or segment.get("text_human") or "",
                }
            )
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha1(blob).hexdigest()

    def _hash_dict(self, payload: Dict[str, Any]) -> str:
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha1(blob).hexdigest()

    def _load_post_state(self) -> Dict[str, Any]:
        if not self.post_state_path.exists():
            return {}
        try:
            return read_json(self.post_state_path)
        except Exception:
            return {}

    def _save_post_state(self) -> None:
        payload = {
            "config_hash": self.config_hash,
            "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        write_json(self.post_state_path, payload, indent=2)

    def _ensure_schema_payload(self, payload: Dict[str, Any], path: Path) -> Dict[str, Any]:
        version = payload.get("schema_version")
        if version == SCHEMA_VERSION:
            return payload
        if version is None:
            self.logger.warning("Schema absent pour %s → marquage en v%s", path.name, SCHEMA_VERSION)
        else:
            self.logger.warning("Schema v%s détecté pour %s → conversion v%s", version, path.name, SCHEMA_VERSION)
        payload["schema_version"] = SCHEMA_VERSION
        try:
            write_json(path, payload, indent=2)
        except Exception:
            pass
        return payload

    def _flatten_sentences(self, structure: Dict[str, Any]) -> List[Dict[str, Any]]:
        sentences: List[Dict[str, Any]] = []
        for section in structure.get("sections", []):
            for sentence in section.get("sentences", []):
                entry = dict(sentence)
                entry.setdefault("section_id", section.get("section_id"))
                sentences.append(entry)
        return sentences

    def _write_clean_artifacts(self, sentences: List[Dict[str, Any]], doc_id: str, language: str) -> Dict[str, Dict[str, Any]]:
        entries = [self._build_clean_entry(sentence, doc_id, language) for sentence in sentences]
        clean_jsonl_path = self.out_dir / f"{doc_id}.clean.jsonl"
        self._write_jsonl_file(clean_jsonl_path, entries)
        txt_mode = str(self.outputs_cfg.get("clean_txt_mode", "human")).lower()
        clean_txt_path = self.out_dir / f"{doc_id}.clean.txt"
        self._write_clean_text(clean_txt_path, sentences, txt_mode)
        self.logger.info("Clean ➜ %s / %s", clean_jsonl_path, clean_txt_path)
        return {
            "clean_jsonl": {"path": clean_jsonl_path, "bytes": clean_jsonl_path.stat().st_size if clean_jsonl_path.exists() else 0},
            "clean_txt": {"path": clean_txt_path, "bytes": clean_txt_path.stat().st_size if clean_txt_path.exists() else 0},
        }

    def _build_clean_entry(self, sentence: Dict[str, Any], doc_id: str, language: str) -> Dict[str, Any]:
        speaker = sentence.get("speaker")
        start = sentence.get("start", 0.0)
        end = sentence.get("end", start)
        chunk_id = sentence.get("chunk_id")
        section_id = sentence.get("section_id")
        text_human = sentence.get("text", "").strip()
        text_machine = sentence.get("text_machine") or text_human
        export_default = str(self.outputs_cfg.get("clean_jsonl_payload", "both")).lower()
        entry = {
            "schema_version": SCHEMA_VERSION,
            "id": stable_id(doc_id, start, end, speaker),
            "source": self.media_path.name,
            "unit": "sentence",
            "section_id": section_id,
            "chunk_id": chunk_id,
            "speaker": speaker,
            "ts_start": start,
            "ts_end": end,
            "text_human": text_human,
            "text_machine": text_machine,
            "export_default": export_default,
            "confidence_mean": sentence.get("confidence_mean"),
            "confidence_p05": sentence.get("confidence_p05"),
            "lang": language,
            "meta": {"tokens": sentence.get("tokens", 0)},
        }
        return entry

    def _write_clean_text(self, path: Path, sentences: List[Dict[str, Any]], mode: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for sentence in sentences:
                text = sentence.get("text_human") if mode == "human" else sentence.get("text_machine") or sentence.get("text_human")
                text = (text or "").strip()
                if not text:
                    continue
                speaker = sentence.get("speaker")
                prefix = f"{speaker}: " if speaker else ""
                handle.write(f"{prefix}{text}\n")

    def _write_chunk_artifacts(
        self,
        structure: Dict[str, Any],
        chunks: List[Dict[str, Any]],
        doc_id: str,
        language: str,
    ) -> Dict[str, Dict[str, Any]]:
        artifacts: Dict[str, Dict[str, Any]] = {}
        if not chunks:
            return artifacts
        chunk_jsonl_path = self.out_dir / f"{doc_id}.chunks.jsonl"
        self._write_jsonl_file(chunk_jsonl_path, chunks)
        self.logger.info("Chunks ➜ %s (%d blocs)", chunk_jsonl_path, len(chunks))
        artifacts["chunks_jsonl"] = {"path": chunk_jsonl_path, "bytes": chunk_jsonl_path.stat().st_size}

        meta = {
            "schema_version": SCHEMA_VERSION,
            "document_id": doc_id,
            "count": len(chunks),
            "order": [chunk["id"] for chunk in chunks],
            "map_section_to_chunks": self._map_sections_to_chunks(chunks),
            "created_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        meta_path = self.out_dir / f"{doc_id}.chunks.meta.json"
        write_json(meta_path, meta, indent=2)
        artifacts["chunks_meta"] = {"path": meta_path, "bytes": meta_path.stat().st_size}

        quotes_path = self.out_dir / f"{doc_id}.quotes.jsonl"
        quotes_entries = self._build_quotes_entries(structure, chunks, language)
        self._write_jsonl_file(quotes_path, quotes_entries)
        artifacts["quotes_jsonl"] = {"path": quotes_path, "bytes": quotes_path.stat().st_size if quotes_path.exists() else 0}
        return artifacts

    def _map_sections_to_chunks(self, chunks: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}
        for chunk in chunks:
            for section_id in chunk.get("section_ids", []):
                mapping.setdefault(str(section_id), []).append(chunk["id"])
        return mapping

    def _build_quotes_entries(self, structure: Dict[str, Any], chunks: List[Dict[str, Any]], language: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        if not structure:
            return entries
        chunk_lookup = [(chunk["id"], chunk["start"], chunk["end"]) for chunk in chunks]
        for section in structure.get("sections", []):
            section_id = section.get("section_id")
            for quote in section.get("quotes", []):
                quote_id = stable_id(section_id or self.media_path.stem, section.get("start"), section.get("end"))
                chunk_id = self._chunk_id_for_timespan(chunk_lookup, section.get("start"), section.get("end"))
                entries.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "id": quote_id,
                        "section_id": section_id,
                        "chunk_id": chunk_id,
                        "ts_start": section.get("start"),
                        "ts_end": section.get("end"),
                        "text": quote,
                        "lang": language,
                    }
                )
        return entries

    def _collect_low_conf_spans(
        self,
        structure: Dict[str, Any],
        chunks: List[Dict[str, Any]],
        language: str,
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        chunk_lookup = [(chunk["id"], chunk["start"], chunk["end"]) for chunk in chunks]
        for section in structure.get("sections", []):
            section_id = section.get("section_id")
            for sentence in section.get("sentences", []):
                mean = sentence.get("confidence_mean")
                p05 = sentence.get("confidence_p05")
                reason = None
                if mean is not None and mean < self.low_conf_threshold:
                    reason = "low_mean"
                elif p05 is not None and p05 < self.low_conf_p05_threshold:
                    reason = "p05_drop"
                if not reason:
                    continue
                chunk_id = sentence.get("chunk_id") or self._chunk_id_for_timespan(chunk_lookup, sentence.get("start"), sentence.get("end"))
                entry = {
                    "schema_version": SCHEMA_VERSION,
                    "id": stable_id(self.media_path.stem, sentence.get("start"), sentence.get("end"), sentence.get("speaker")),
                    "source": self.media_path.name,
                    "section_id": section_id,
                    "chunk_id": chunk_id,
                    "ts_start": sentence.get("start"),
                    "ts_end": sentence.get("end"),
                    "speaker": sentence.get("speaker"),
                    "text_human": sentence.get("text"),
                    "text_machine": sentence.get("text_machine") or sentence.get("text"),
                    "reason": reason,
                    "score_mean": mean,
                    "p05": p05,
                    "lang": language,
                }
                entries.append(entry)
        for chunk in chunks:
            if chunk.get("low_span_ratio", 0) >= self.chunker.low_span_threshold:
                entries.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "id": stable_id(chunk["document_id"], chunk["start"], chunk["end"], chunk.get("speaker_majority")),
                        "source": self.media_path.name,
                        "section_id": ",".join(chunk.get("section_ids", [])),
                        "chunk_id": chunk["id"],
                        "ts_start": chunk["start"],
                        "ts_end": chunk["end"],
                        "speaker": chunk.get("speaker_majority"),
                        "text_human": chunk.get("text_human"),
                        "text_machine": chunk.get("text_machine"),
                        "reason": "low_span",
                        "score_mean": chunk.get("confidence_mean"),
                        "p05": chunk.get("confidence_p05"),
                        "lang": language,
                    }
                )
        entries.sort(key=lambda item: (item.get("score_mean") or 1.0, item.get("p05") or 1.0))
        return entries

    def _write_low_conf_jsonl(self, entries: List[Dict[str, Any]], doc_id: str) -> Path:
        path = self.out_dir / f"{doc_id}.low_confidence.jsonl"
        self._write_jsonl_file(path, entries)
        self.logger.info("Low-confidence queue ➜ %s (%d spans)", path, len(entries))
        return path

    def _build_quality_metrics(
        self,
        sentences: List[Dict[str, Any]],
        chunks: List[Dict[str, Any]],
        clean_report: Dict[str, Any],
        polish_report: Dict[str, Any],
        low_conf_entries: List[Dict[str, Any]],
        structure: Dict[str, Any],
    ) -> Dict[str, Any]:
        tokens_total = sum(chunk.get("token_count", 0) for chunk in chunks)
        chunk_conf = [chunk.get("confidence_mean") for chunk in chunks if chunk.get("confidence_mean") is not None]
        chunk_mean = round(sum(chunk_conf) / len(chunk_conf), 3) if chunk_conf else None
        metrics = {
            "schema_version": SCHEMA_VERSION,
            "document_id": self.media_path.stem,
            "tokens_total": tokens_total,
            "phrases_total": len(sentences),
            "chunks_total": len(chunks),
            "low_conf_count": len(low_conf_entries),
            "chunk_confidence_mean": chunk_mean,
            "clean": clean_report,
            "polish": polish_report,
            "sparkline": self._build_sparkline(structure),
            "overlap_sentences": self.chunker.overlap_sentences,
        }
        if sentences:
            low_sentences = sum(
                1 for sentence in sentences if sentence.get("confidence_mean") is not None and sentence["confidence_mean"] < self.low_conf_threshold
            )
            metrics["low_sentence_ratio"] = round(low_sentences / len(sentences), 3)
        metrics["stages"] = self.run_stats.get("stages", {})
        return metrics

    def _write_metrics_file(
        self,
        metrics_payload: Dict[str, Any],
        clean_artifacts: Dict[str, Dict[str, Any]],
        chunk_artifacts: Dict[str, Dict[str, Any]],
        low_conf_path: Path,
    ) -> None:
        doc_id = self.media_path.stem
        artifacts = {}
        artifacts.update(clean_artifacts)
        artifacts.update(chunk_artifacts)
        if low_conf_path:
            artifacts["low_confidence"] = {
                "path": low_conf_path,
                "bytes": low_conf_path.stat().st_size if low_conf_path.exists() else 0,
            }
        metrics_path = self.out_dir / f"{doc_id}.metrics.json"
        payload = dict(metrics_payload)
        serialized_artifacts = {}
        for name, info in artifacts.items():
            if info is None:
                serialized_artifacts[name] = None
                continue
            entry = dict(info)
            if isinstance(entry.get("path"), Path):
                entry["path"] = str(entry["path"])
            serialized_artifacts[name] = entry
        payload["artifacts"] = serialized_artifacts
        payload["generated_at"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        payload["stages_ms"] = {name: int(duration * 1000) for name, duration in self.run_stats.get("stages", {}).items()}
        write_json(metrics_path, payload, indent=2)
        self.logger.info("Métriques ➜ %s", metrics_path)

    def _build_sparkline(self, structure: Optional[Dict[str, Any]]) -> str:
        if not structure:
            return ""
        blocks = "▁▂▃▄▅▆▇█"
        spark = []
        for section in structure.get("sections", []):
            avg = section.get("metadata", {}).get("avg_confidence")
            if avg is None:
                spark.append("·")
                continue
            index = min(len(blocks) - 1, max(0, int(avg * (len(blocks) - 1))))
            spark.append(blocks[index])
        return "".join(spark)

    def _write_jsonl_file(self, path: Path, rows: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows or []:
                handle.write(json.dumps(row, ensure_ascii=False))
                handle.write("\n")

    def _mark_stage_duration(self, name: str, start_ts: float) -> None:
        elapsed = round(time.time() - start_ts, 3)
        self.run_stats["stages"][name] = elapsed

    def _verify_artifacts(self) -> None:
        if self.command not in {"run", "resume"}:
            return
        stem = self.media_path.stem
        required_paths = [
            self.audio_path,
            self.manifest_path,
            self.work_dir / "02_merged_raw.json",
            self.work_dir / "03_aligned_whisperx.json",
            self.work_dir / "04_cleaned.json",
            self.work_dir / "05_polished.json",
        ]
        required_dirs = [self.work_dir / "00_segments", self.work_dir / "01_asr_jsonl"]
        missing = [str(p) for p in required_paths if not p.exists()]
        missing.extend(str(d) for d in required_dirs if not d.exists())
        if missing:
            raise PipelineError(f"Mode strict: artefacts manquants {missing}")
        expected_exports = {
            self.out_dir / f"{stem}.md",
            self.out_dir / f"{stem}.json",
            self.out_dir / f"{stem}.vtt",
            self.out_dir / f"{stem}.low_confidence.csv",
            self.out_dir / f"{stem}.chapters.json",
        }
        for path in expected_exports:
            if not path.exists():
                raise PipelineError(f"Mode strict: export absent {path}")
        if self.strict:
            existing = {p.name for p in self.out_dir.glob(f"{stem}.*")}
            allowed = {p.name for p in expected_exports}
            extra = existing - allowed
            if extra:
                raise PipelineError(f"Mode strict: exports non attendus {', '.join(sorted(extra))}")

    def finalize_run(self, success: bool, error: Optional[str]) -> None:
        end_ts = time.time()
        self.run_stats["success"] = success
        self.run_stats["error"] = error
        manifest = {
            "input": str(self.media_path),
            "input_sha256": self.input_hash,
            "config": str(self.config_path),
            "strict": self.strict,
            "mode": self.mode,
            "fail_fast": self.fail_fast,
            "no_partial_export": self.no_partial_export,
            "start": dt.datetime.fromtimestamp(self._run_start).isoformat(),
            "end": dt.datetime.fromtimestamp(end_ts).isoformat(),
            "duration_sec": round(end_ts - self._run_start, 3),
            "stages": self.run_stats["stages"],
            "asr": self.asr_metrics or {},
            "environment": self._collect_versions(),
            "status": "ok" if success else "failed",
            "error": error,
        }
        run_manifest_path = self.local_log_dir / "run_manifest.json"
        write_json(run_manifest_path, manifest)
        self._update_pipeline_metrics(manifest)

    def _collect_versions(self) -> Dict[str, str]:
        packages = [
            "faster-whisper",
            "whisperx",
            "pyannote.audio",
            "torch",
            "torchaudio",
            "onnxruntime",
        ]
        versions: Dict[str, str] = {}
        for pkg in packages:
            try:
                versions[pkg] = metadata.version(pkg)
            except Exception:
                versions[pkg] = "missing"
        versions["python"] = platform.python_version()
        versions["ffmpeg"] = self._ffmpeg_version()
        versions["ffprobe"] = self._ffprobe_version()
        return versions

    def _ffmpeg_version(self) -> str:
        try:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
            first_line = (result.stdout or "").splitlines()[0]
            return first_line.split()[2]
        except Exception:
            return "unknown"

    def _ffprobe_version(self) -> str:
        try:
            result = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, check=True)
            first_line = (result.stdout or "").splitlines()[0]
            return first_line.split()[2]
        except Exception:
            return "unknown"

    def _compute_input_hash(self, path: Path) -> str:
        sha = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(chunk)
        return sha.hexdigest()

    def _update_pipeline_metrics(self, manifest: Dict[str, Any]) -> None:
        metrics_path = self.local_log_dir / "metrics.json"
        payload: Dict[str, Any]
        if metrics_path.exists():
            try:
                payload = read_json(metrics_path)
            except Exception:
                payload = {}
        else:
            payload = {}
        payload["pipeline"] = {
            "status": manifest["status"],
            "duration_sec": manifest["duration_sec"],
            "stages": manifest.get("stages", {}),
            "input_sha256": manifest.get("input_sha256"),
            "timestamp": manifest.get("end"),
        }
        write_json(metrics_path, payload, indent=2)

    # ---- entrypoints ----
    def execute(self) -> None:
        if self.command == "dry-run":
            self.dry_run()
        elif self.command == "prepare":
            self.stage_prepare()
        elif self.command == "asr":
            self.stage_asr()
        elif self.command == "merge":
            self.stage_merge()
        elif self.command == "align":
            self.stage_align()
        elif self.command == "post":
            self.stage_post()
        elif self.command == "resume":
            self.logger.info("=== RESUME (post-processing only) ===")
            self.align_info = self._load_align_from_disk()
            self.stage_post()
            if not self.dry_run:
                self.last_artifacts = self.stage_export()
        elif self.command == "export":
            self.stage_export()
        else:
            self.run_all()

    def run_all(self) -> None:
        self.logger.info("=== Pipeline complet ===")
        self.stage_prepare()
        self.stage_asr()
        self.stage_merge()
        self.stage_align()
        self.stage_post()
        if self.dry_run:
            self.logger.info("--dry-run activé: arrêt avant exports finaux.")
            if self.strict:
                self._verify_artifacts()
            return
        self.last_artifacts = self.stage_export()
        if self.strict:
            self._verify_artifacts()
        if not self.keep_build:
            self.logger.info("Les artefacts work sont conservés par défaut (option --keep-build obsolète).")
        self.logger.info("Dossier de travail ➜ %s", self.work_dir)

    def dry_run(self) -> None:
        self.logger.info("=== Dry-run (aucune étape lancée) ===")
        statuses = {
            "audio_16k.wav": self.audio_path.exists(),
            "manifest.csv": self.manifest_path.exists(),
            "manifest_state.json": self.state_path.exists(),
            "02_merged_raw.json": (self.work_dir / "02_merged_raw.json").exists(),
            "03_aligned_whisperx.json": (self.work_dir / "03_aligned_whisperx.json").exists(),
            "04_cleaned.json": (self.work_dir / "04_cleaned.json").exists(),
            "05_polished.json": (self.work_dir / "05_polished.json").exists(),
        }
        for name, ok in statuses.items():
            self.logger.info("%-28s : %s", name, "OK" if ok else "absent")
        seg_cfg = self.config.get("segmenter", {})
        self.logger.info(
            "Fenêtres: %ss + overlap %ss | workers cibles: %s",
            seg_cfg.get("segment_length", 75),
            seg_cfg.get("overlap", 8),
            self.asr.estimate_worker_count(),
        )
        self.logger.info("Exports ➜ %s/%s.*", self.out_dir, self.media_path.stem)


def main():
    args = parse_args()
    runner = PipelineRunner(args)
    try:
        runner.execute()
    except Exception as exc:
        runner.finalize_run(success=False, error=str(exc))
        raise
    else:
        runner.finalize_run(success=True, error=None)


if __name__ == "__main__":
    main()
