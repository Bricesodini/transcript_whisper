import csv
import json
import os
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import PipelineError, detect_language, read_json, resolve_runtime_device, sanitize_whisper_text, write_json

try:
    from faster_whisper import WhisperModel
except ImportError as exc:  # pragma: no cover
    WhisperModel = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@dataclass
class SegmentJob:
    index: int
    start_ms: int
    end_ms: int
    audio_path: Path
    output_path: Path
    status: str = "PENDING"
    retries: int = 0


_WORKER_MODEL: Optional[WhisperModel] = None
_WORKER_MODEL_OPTIONS: Dict[str, Any] = {}
_WORKER_LOG_PATH: Optional[Path] = None


def _worker_bootstrap(env: Dict[str, str], log_dir: str, model_opts: Dict[str, Any]) -> None:
    for key, value in (env or {}).items():
        if value is None:
            continue
        os.environ[key] = str(value)
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    global _WORKER_LOG_PATH, _WORKER_MODEL_OPTIONS
    _WORKER_MODEL_OPTIONS = model_opts or {}
    _WORKER_LOG_PATH = log_dir_path / f"asr_worker_{os.getpid()}.log"


def _worker_log(message: str) -> None:
    if _WORKER_LOG_PATH is None:
        return
    try:
        _WORKER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _WORKER_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(message.strip() + "\n")
    except OSError:
        pass


def _ensure_worker_model() -> WhisperModel:
    global _WORKER_MODEL
    if _WORKER_MODEL is not None:
        return _WORKER_MODEL
    if WhisperModel is None:  # pragma: no cover
        raise RuntimeError(f"faster-whisper indisponible: {IMPORT_ERROR}")
    model_name = _WORKER_MODEL_OPTIONS.get("model_name", "large-v3")
    requested_device = _WORKER_MODEL_OPTIONS.get("device", "auto")
    device = resolve_runtime_device(requested_device, logger=None, label="ASR worker")
    compute_type = _WORKER_MODEL_OPTIONS.get("compute_type", "auto")
    _WORKER_MODEL = WhisperModel(model_name, device=device, compute_type=compute_type)
    return _WORKER_MODEL


def _transcribe_segment(job: Dict[str, Any], decoder_cfg: Dict[str, Any]) -> Dict[str, Any]:
    model = _ensure_worker_model()
    language_hint = job.get("language") or "auto"
    forced_language = None if language_hint in ("auto", "", None) else language_hint
    temperature = float(decoder_cfg.get("temperature", 0.0))
    fallback = decoder_cfg.get("temperature_fallback")
    if fallback:
        temperature = (temperature, temperature + float(fallback))
    raw_kwargs = dict(
        beam_size=int(decoder_cfg.get("beam_size", 1)),
        best_of=int(decoder_cfg.get("best_of", 1)),
        language=forced_language,
        vad_filter=bool(decoder_cfg.get("vad_filter", False)),
        chunk_length=decoder_cfg.get("chunk_length"),
        word_timestamps=bool(decoder_cfg.get("word_timestamps", False)),
        condition_on_previous_text=bool(decoder_cfg.get("condition_on_previous_text", False)),
        no_speech_threshold=float(decoder_cfg.get("no_speech_threshold", 0.6)),
        temperature=temperature,
        initial_prompt=job.get("initial_prompt") or decoder_cfg.get("initial_prompt"),
    )
    allowed_keys = {
        "beam_size",
        "best_of",
        "language",
        "vad_filter",
        "chunk_length",
        "word_timestamps",
        "condition_on_previous_text",
        "no_speech_threshold",
        "temperature",
        "initial_prompt",
    }
    kwargs = {key: value for key, value in raw_kwargs.items() if key in allowed_keys and value is not None}
    segments_iter, info = model.transcribe(str(job["audio_path"]), **kwargs)
    offset_ms = int(job["start_ms"])
    chunks: List[Dict[str, Any]] = []
    texts: List[str] = []
    logprobs: List[float] = []
    no_speech_scores: List[float] = []
    for seg in segments_iter:
        text = sanitize_whisper_text(seg.text)
        if text:
            texts.append(text)
        start_ms = offset_ms + int(round(float(seg.start or 0.0) * 1000))
        end_ms = offset_ms + int(round(float(seg.end or 0.0) * 1000))
        avg_lp = getattr(seg, "avg_logprob", None)
        if avg_lp is not None:
            logprobs.append(float(avg_lp))
        ns_prob = getattr(seg, "no_speech_prob", None)
        if ns_prob is not None:
            no_speech_scores.append(float(ns_prob))
        chunks.append(
            {
                "t0": start_ms,
                "t1": end_ms,
                "text": text,
                "avg_logprob": float(avg_lp) if avg_lp is not None else None,
            }
        )

    payload = {
        "segment_index": job["index"],
        "start_ms": job["start_ms"],
        "end_ms": job["end_ms"],
        "language": info.language or language_hint,
        "avg_logprob": (sum(logprobs) / len(logprobs)) if logprobs else None,
        "no_speech_prob": (sum(no_speech_scores) / len(no_speech_scores)) if no_speech_scores else None,
        "chunks": chunks,
    }

    output_path = Path(job["output_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _worker_log(f"Segment {job['index']:05d} ➜ {output_path.name} ({len(chunks)} chunks)")
    return {
        "segment_index": job["index"],
        "language": payload["language"],
        "text_sample": " ".join(texts[:4]),
    }


class ASRProcessor:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        self.cfg = config
        self.model_name = config.get("defaults", {}).get("model", "large-v3")
        self.asr_cfg = config.get("asr", {})
        requested_device = self.asr_cfg.get("device", "auto")
        self.asr_device = resolve_runtime_device(requested_device, logger=self.logger, label="ASR")
        self._model: Optional[WhisperModel] = None
        self._batch_warned = False

    def load_model(self):  # utilisé par SegmentRefiner
        if self._model is not None:
            return self._model
        if WhisperModel is None:
            raise PipelineError(f"faster-whisper non disponible: {IMPORT_ERROR}")
        compute_type = self.asr_cfg.get("compute_type", "auto")
        self.logger.info("Chargement modèle Faster-Whisper (%s)", self.model_name)
        try:
            self._model = WhisperModel(self.model_name, device=self.asr_device, compute_type=compute_type)
        except ValueError as exc:
            raise PipelineError(f"Chargement Faster-Whisper impossible: {exc}") from exc
        return self._model

    def ensure_model_cached(self) -> None:
        self.load_model()

    def estimate_worker_count(self) -> int:
        return self._resolve_worker_count()

    def run(
        self,
        manifest_path: Path,
        work_dir: Path,
        requested_lang: str,
        detect_lang: bool = True,
        initial_prompt: Optional[str] = None,
        force: bool = False,
        fail_fast: bool = True,
        only_failed: bool = False,
    ) -> Dict[str, Any]:
        if not manifest_path.exists():
            raise PipelineError(f"Manifest introuvable: {manifest_path}")
        jsonl_dir = work_dir / "01_asr_jsonl"
        jsonl_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = work_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        state_path = work_dir / "manifest_state.json"

        jobs = self._read_manifest(manifest_path, work_dir, jsonl_dir)
        state = self._load_state(state_path)
        pending = self._filter_jobs(jobs, state, force, only_failed)
        self._save_state(state_path, state)

        if not pending:
            self.logger.info("ASR parallèle: rien à faire (tous les segments sont DONE)")
            resolved_lang = self._resolve_language([], requested_lang, detect_lang)
            metrics_payload = self._write_metrics(
                work_dir,
                {
                    "segments_total": len(jobs),
                    "segments_pending": 0,
                    "segments_processed": 0,
                    "segments_skipped": len(jobs),
                    "segments_failed": [],
                    "worker_count": self._resolve_worker_count(),
                    "retry_events": 0,
                    "duration_sec": 0.0,
                    "status": "ok",
                },
            )
            return {
                "language": resolved_lang,
                "jsonl_dir": jsonl_dir,
                "state_path": state_path,
                "manifest": manifest_path,
                "results": [],
                "failed_segments": [],
                "metrics": metrics_payload,
            }

        worker_count = self._resolve_worker_count()
        decoder_cfg = self._decoder_options(initial_prompt)
        worker_env = self._blas_env()
        model_opts = {
            "model_name": self.model_name,
            "device": self.asr_device,
            "compute_type": self.asr_cfg.get("compute_type", "auto"),
        }
        max_retries = int(self.asr_cfg.get("max_retries", 2))
        language_hint = requested_lang or "auto"

        results: List[Dict[str, Any]] = []
        queue = deque(pending)
        inflight = {}
        self.logger.info("ASR parallèle: %d segments à traiter (%d workers)", len(pending), worker_count)
        start_time = time.time()
        processed = 0
        retry_events = 0
        failed_segments: List[int] = []
        with ProcessPoolExecutor(
            max_workers=worker_count,
            initializer=_worker_bootstrap,
            initargs=(worker_env, str(logs_dir), model_opts),
        ) as executor:
            try:
                while queue or inflight:
                    while queue and len(inflight) < worker_count:
                        job = queue.popleft()
                        payload = {
                            "index": job.index,
                            "start_ms": job.start_ms,
                            "end_ms": job.end_ms,
                            "audio_path": str(job.audio_path),
                            "output_path": str(job.output_path),
                            "language": language_hint,
                            "initial_prompt": decoder_cfg.get("initial_prompt"),
                        }
                        future = executor.submit(_transcribe_segment, payload, decoder_cfg)
                        inflight[future] = job
                        self._update_state(state, job.index, "IN_PROGRESS", job.retries, state_path)
                    if not inflight:
                        break
                    done, _ = wait(list(inflight.keys()), return_when=FIRST_COMPLETED)
                    for future in done:
                        job = inflight.pop(future)
                        try:
                            result = future.result()
                            processed += 1
                            results.append(result)
                            self._update_state(state, job.index, "DONE", job.retries, state_path)
                        except Exception as exc:  # pragma: no cover - surfaced au niveau pipeline
                            job.retries += 1
                            if job.retries <= max_retries:
                                retry_events += 1
                                self.logger.warning(
                                    "ASR segment %s en erreur ➜ retry (%d/%d)", job.index, job.retries, max_retries
                                )
                                queue.append(job)
                                self._update_state(state, job.index, "RETRY", job.retries, state_path)
                            else:
                                failed_segments.append(job.index)
                                self._update_state(state, job.index, "FAILED", job.retries, state_path)
                                if fail_fast:
                                    raise PipelineError(
                                        f"ASR segment {job.index} en échec définitif (voir logs {logs_dir})"
                                    ) from exc
                                else:
                                    self.logger.error(
                                        "ASR segment %s en échec définitif (voir logs %s)", job.index, logs_dir
                                    )
                                    continue
            finally:
                duration = max(time.time() - start_time, 0.0)
                metrics_payload = self._write_metrics(
                    work_dir,
                    {
                        "segments_total": len(jobs),
                        "segments_pending": len(pending),
                        "segments_processed": processed,
                        "segments_skipped": len(jobs) - len(pending),
                        "segments_failed": failed_segments,
                        "worker_count": worker_count,
                        "retry_events": retry_events,
                        "duration_sec": round(duration, 2),
                        "status": "failed" if failed_segments else "ok",
                    },
                )

        resolved_lang = self._resolve_language(results, requested_lang, detect_lang)
        return {
            "language": resolved_lang,
            "jsonl_dir": jsonl_dir,
            "state_path": state_path,
            "manifest": manifest_path,
            "results": results,
            "failed_segments": failed_segments,
            "metrics": metrics_payload,
        }

    def _read_manifest(self, manifest_path: Path, work_dir: Path, jsonl_dir: Path) -> List[SegmentJob]:
        jobs: List[SegmentJob] = []
        with manifest_path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                idx = int(row["index"])
                start_ms = int(row["start_ms"])
                end_ms = int(row["end_ms"])
                rel_path = row.get("path") or ""
                audio_path = Path(rel_path)
                if not audio_path.is_absolute():
                    audio_path = (manifest_path.parent / audio_path).resolve()
                if not audio_path.exists():
                    raise PipelineError(f"Segment audio manquant: {audio_path}")
                output_path = jsonl_dir / f"seg_{idx:05d}.jsonl"
                jobs.append(SegmentJob(idx, start_ms, end_ms, audio_path, output_path))
        return jobs

    def _load_state(self, state_path: Path) -> Dict[str, Any]:
        if not state_path.exists():
            return {"meta": {}, "segments": {}}
        with state_path.open("r", encoding="utf-8") as handle:
            try:
                data = json.load(handle)
            except json.JSONDecodeError:
                return {"meta": {}, "segments": {}}
        data.setdefault("segments", {})
        return data

    def _filter_jobs(self, jobs: List[SegmentJob], state: Dict[str, Any], force: bool, only_failed: bool) -> List[SegmentJob]:
        pending: List[SegmentJob] = []
        segments_state = state.setdefault("segments", {})
        for job in jobs:
            key = str(job.index)
            seg_state = segments_state.setdefault(key, {"status": "PENDING", "retries": 0})
            job.status = seg_state.get("status", "PENDING")
            job.retries = int(seg_state.get("retries", 0))
            if force:
                pending.append(job)
                continue
            if only_failed:
                if job.status == "FAILED":
                    if job.output_path.exists():
                        job.output_path.unlink(missing_ok=True)
                    pending.append(job)
                continue
            if job.output_path.exists() and job.status == "DONE":
                continue
            if job.output_path.exists():
                seg_state.update({"status": "DONE", "retries": job.retries})
                continue
            pending.append(job)
        return pending

    def _update_state(self, state: Dict[str, Any], index: int, status: str, retries: int, path: Path) -> None:
        entry = state.setdefault("segments", {}).setdefault(str(index), {})
        entry.update({
            "status": status,
            "retries": retries,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
        self._save_state(path, state)

    def _save_state(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, payload, indent=2)

    def _decoder_options(self, initial_prompt: Optional[str]) -> Dict[str, Any]:
        cfg = self.asr_cfg
        batch_size = cfg.get("batch_size")
        if batch_size not in (None, 0) and not self._batch_warned:
            self.logger.warning("Paramètre 'asr.batch_size' ignoré (non supporté par faster-whisper).")
            self._batch_warned = True
        chunk_length = self._sanitize_chunk_length(cfg.get("chunk_length"))
        return {
            "temperature": cfg.get("temperature", 0.0),
            "temperature_fallback": cfg.get("temperature_fallback"),
            "beam_size": cfg.get("beam_size", 1),
            "best_of": cfg.get("best_of", 1),
            "vad_filter": cfg.get("vad_filter", False),
            "chunk_length": chunk_length,
            "word_timestamps": cfg.get("word_timestamps", False),
            "condition_on_previous_text": cfg.get("condition_on_previous_text", False),
            "no_speech_threshold": cfg.get("no_speech_threshold", 0.6),
            "initial_prompt": initial_prompt or cfg.get("initial_prompt"),
        }

    def _sanitize_chunk_length(self, raw_value: Any) -> Optional[int]:
        if raw_value in (None, "", False, 0):
            return None
        try:
            chunk_value = float(raw_value)
        except (TypeError, ValueError):
            self.logger.warning("Paramètre asr.chunk_length invalide (%r) ➜ ignoré", raw_value)
            return None
        if chunk_value <= 0:
            self.logger.warning("Paramètre asr.chunk_length <= 0 (%s) ➜ ignoré", raw_value)
            return None
        sanitized = int(round(chunk_value))
        if sanitized <= 0:
            self.logger.warning("Paramètre asr.chunk_length trop faible (%s) ➜ ignoré", raw_value)
            return None
        return sanitized

    def _resolve_worker_count(self) -> int:
        limit = int(self.asr_cfg.get("max_workers", 8))
        env_limit = self._env_thread_ceiling()
        ceiling = min(limit, env_limit) if env_limit else limit
        physical = self._physical_cores()
        logical = os.cpu_count() or 1
        target = physical or logical
        return max(1, min(ceiling, target))

    def _physical_cores(self) -> Optional[int]:
        try:
            import psutil  # type: ignore
        except ImportError:  # pragma: no cover
            return None
        return psutil.cpu_count(logical=False)

    def _env_thread_ceiling(self) -> Optional[int]:
        for key in ("ASR_THREADS", "CTRANSLATE2_NUM_THREADS"):
            value = os.environ.get(key)
            if not value:
                continue
            try:
                parsed = int(value)
            except ValueError:
                continue
            if parsed > 0:
                return parsed
        return None

    def _blas_env(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        thread_cap = os.environ.get("ASR_THREADS")
        default_threads = thread_cap or "1"
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env[var] = os.environ.get(var, default_threads if thread_cap else "1")
        if thread_cap:
            env["ASR_THREADS"] = thread_cap
        ctranslate_threads = os.environ.get("CTRANSLATE2_NUM_THREADS") or thread_cap
        if ctranslate_threads:
            env["CTRANSLATE2_NUM_THREADS"] = ctranslate_threads
        return env

    def _resolve_language(
        self,
        results: List[Dict[str, Any]],
        requested_lang: str,
        detect_lang: bool,
    ) -> str:
        if requested_lang and requested_lang != "auto":
            return requested_lang
        detected = [res.get("language") for res in results if res.get("language") not in (None, "", "auto")]
        if detected:
            winner = Counter(detected).most_common(1)[0][0]
            return winner
        if detect_lang and results:
            sample = " ".join(res.get("text_sample", "") for res in results[:5]).strip()
            lang = detect_language(sample) if sample else None
            if lang:
                return lang
        return "auto"

    def _write_metrics(self, work_dir: Path, metrics: Dict[str, Any]) -> Dict[str, Any]:
        metrics_path = work_dir / "logs" / "metrics.json"
        payload: Dict[str, Any]
        if metrics_path.exists():
            try:
                payload = read_json(metrics_path)
            except Exception:
                payload = {}
        else:
            payload = {}
        metrics.setdefault("timestamp", datetime.utcnow().isoformat() + "Z")
        payload["asr"] = metrics
        write_json(metrics_path, payload, indent=2)
        return metrics
