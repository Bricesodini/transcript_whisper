import argparse
import datetime as dt
import hashlib
import logging
import platform
import subprocess
import time
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional

from align import Aligner
from asr import ASRProcessor
from clean import Cleaner
from diarize import Diarizer
from export import Exporter
from merger import DeterministicMerger
from polish import Polisher
from preproc import Preprocessor
from refine import SegmentRefiner
from segmenter import Segmenter
from structure import Structurer
from utils import (
    PipelineError,
    load_config,
    normalize_media_path,
    prepare_paths,
    read_json,
    select_profile,
    setup_logger,
    stage_timer,
    write_json,
)

COMMANDS = ("run", "prepare", "asr", "merge", "align", "post", "export", "resume", "dry-run")


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
    parser.add_argument("--verbose", action="store_true", help="Afficher les logs détaillés")
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

        exports_root = self.paths.get("exports_dir", self.paths.get("out_dir"))
        self.out_dir = Path(exports_root) / self.media_path.stem
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.global_log_dir = self.paths.get("logs_dir", self.work_dir / "logs")
        self.global_log_dir.mkdir(parents=True, exist_ok=True)
        self.local_log_dir = self.work_dir / "logs"
        run_name = f"{self.media_path.stem}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.logger = setup_logger(self.global_log_dir, run_name, verbose=args.verbose)
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

        config = self.config
        self.preproc = Preprocessor(config, self.logger)
        self.segmenter = Segmenter(config, self.logger)
        self.asr = ASRProcessor(config, self.logger)
        self.merger = DeterministicMerger(config, self.logger)
        self.cleaner = Cleaner(config, self.logger)
        self.polisher = Polisher(config.get("polish", {}), self.logger)
        self.structurer = Structurer(config, self.logger)
        self.exporter = Exporter(config, self.logger)
        self.aligner = Aligner(config, self.logger)
        self.diarizer = None if self.skip_diarization else Diarizer(config, self.logger)
        self.refiner = SegmentRefiner(config, self.logger, self.asr)

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
        with stage_timer(self.logger, "Diarisation Pyannote"):
            self.diarization_result = self.diarizer.run(audio_path, self.work_dir)
        self._mark_stage_duration("diarize", stage_start)
        return self.diarization_result

    def stage_align(self) -> Dict[str, Any]:
        stage_start = time.time()
        self.logger.info("▶ ALIGN")
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
        align_result = self.align_info or self.stage_align()
        segments_for_clean = align_result["segments"]
        audio_path = self.ensure_audio_ready()
        if self.refiner.enabled and segments_for_clean:
            with stage_timer(self.logger, "Re-ASR ciblé"):
                segments_for_clean = self.refiner.run(audio_path, segments_for_clean, align_result["language"], self.work_dir)
        with stage_timer(self.logger, "Nettoyage linguistique"):
            cleaned_segments = self.cleaner.run(segments_for_clean, align_result["language"])
            write_json(
                self.work_dir / "04_cleaned.json",
                {"language": align_result["language"], "segments": cleaned_segments},
            )
        with stage_timer(self.logger, "Polish lecture"):
            polished_segments = self.polisher.run(cleaned_segments, lang=align_result["language"])
            write_json(
                self.work_dir / "05_polished.json",
                {"language": align_result["language"], "segments": polished_segments},
            )
        with stage_timer(self.logger, "Structuration et chapitrage"):
            structure_data = self.structurer.run(polished_segments, align_result["language"])
            write_json(self.work_dir / "structure.json", structure_data)
            chapters_path = self.out_dir / f"{self.media_path.stem}.chapters.json"
            write_json(
                chapters_path,
                {
                    "language": align_result["language"],
                    "sections": structure_data.get("sections", []),
                },
            )
            self.logger.info("Chapitres ➜ %s", chapters_path)
        self.post_info = {
            "cleaned": cleaned_segments,
            "polished": polished_segments,
            "structure": structure_data,
        }
        self._mark_stage_duration("post", stage_start)
        return self.post_info

    def stage_export(self) -> Dict[str, Path]:
        stage_start = time.time()
        self.logger.info("▶ EXPORT")
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
        if not polished_path.exists() or not structure_path.exists() or not cleaned_path.exists():
            raise PipelineError("Post-traitement introuvable: lance d'abord la commande 'post'.")
        polished_payload = read_json(polished_path)
        structure_payload = read_json(structure_path)
        cleaned_payload = read_json(cleaned_path)
        self.post_info = {
            "cleaned": cleaned_payload.get("segments", []),
            "polished": polished_payload.get("segments", []),
            "structure": structure_payload,
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
