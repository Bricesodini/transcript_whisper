import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from utils import PipelineError, read_json, write_json


class DeterministicMerger:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        seg_cfg = config.get("segmenter", {})
        self.segment_length_ms = int(round(seg_cfg.get("segment_length", 75.0) * 1000))
        self.overlap_ms = int(round(seg_cfg.get("overlap", 8.0) * 1000))
        self.similarity_threshold = float(seg_cfg.get("similarity_threshold", 0.15))
        self.max_gap_ms = int(seg_cfg.get("max_gap_ms", 200))

    def run(
        self,
        manifest_path: Path,
        jsonl_dir: Path,
        work_dir: Path,
        language: str,
        force: bool = False,
    ) -> Dict[str, Any]:
        if not manifest_path.exists():
            raise PipelineError("Manifest manquant pour la fusion")
        output_path = work_dir / "02_merged_raw.json"
        if output_path.exists() and not force:
            payload = read_json(output_path)
            self.logger.info("Fusion segments (cache) ➜ %s", output_path)
            return self._format_payload(payload, output_path)

        chunks = self._collect_chunks(manifest_path, jsonl_dir)
        if not chunks:
            raise PipelineError("Aucun segment ASR disponible pour la fusion")
        merged: List[Dict[str, Any]] = []
        for segment in chunks:
            if not merged:
                merged.extend(segment)
                continue
            merged = self._merge_tail(merged, segment)
        seg_cfg = self._segment_meta()
        payload = {
            "language": language,
            "items": [
                {
                    "t0": chunk["t0"],
                    "t1": chunk["t1"],
                    "text": chunk["text"],
                    "avg_logprob": chunk.get("avg_logprob"),
                }
                for chunk in merged
                if chunk.get("text")
            ],
            "meta": {
                "seg_length_ms": seg_cfg.get("seg_length_ms"),
                "overlap_ms": self.overlap_ms,
            },
        }
        write_json(output_path, payload, indent=2)
        merge_log = work_dir / "logs" / "merge.log"
        merge_log.parent.mkdir(parents=True, exist_ok=True)
        with merge_log.open("w", encoding="utf-8") as handle:
            handle.write(f"Fusion {len(chunks)} segments ➜ {len(payload['items'])} items\n")
        self.logger.info("Fusion segments ➜ %s", output_path)
        return self._format_payload(payload, output_path)

    def _segment_meta(self) -> Dict[str, Any]:
        return {
            "seg_length_ms": self.segment_length_ms,
        }

    def _collect_chunks(self, manifest_path: Path, jsonl_dir: Path) -> List[List[Dict[str, Any]]]:
        ordered: List[List[Dict[str, Any]]] = []
        with manifest_path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                idx = int(row["index"])
                json_path = jsonl_dir / f"seg_{idx:05d}.jsonl"
                if not json_path.exists():
                    raise PipelineError(f"Segment JSONL manquant: {json_path}")
                with json_path.open("r", encoding="utf-8") as seg_file:
                    try:
                        payload = json.loads(seg_file.readline())
                    except json.JSONDecodeError as exc:
                        raise PipelineError(f"JSONL corrompu: {json_path}") from exc
                ordered.append(payload.get("chunks", []))
        return ordered

    def _merge_tail(self, existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]):
        if not incoming:
            return existing
        prev_end = existing[-1]["t1"]
        overlap_start = max(prev_end - self.overlap_ms, 0)
        overlap_end = prev_end
        prev_overlap = self._select_window(existing, overlap_start, overlap_end)
        new_overlap = self._select_window(incoming, overlap_start, overlap_end)
        trimmed_incoming = incoming
        trimmed_existing = existing
        if prev_overlap["text"] and new_overlap["text"]:
            distance = self._normalized_distance(prev_overlap["text"], new_overlap["text"])
            if distance <= self.similarity_threshold:
                trimmed_incoming = self._trim_head(incoming, overlap_end)
            else:
                prev_quality = prev_overlap["quality"]
                new_quality = new_overlap["quality"]
                if new_quality > prev_quality:
                    trimmed_existing = self._trim_tail(existing, overlap_start)
                else:
                    trimmed_incoming = self._trim_head(incoming, overlap_end)
        else:
            trimmed_incoming = self._trim_head(incoming, overlap_end)

        if trimmed_existing and trimmed_incoming:
            gap = trimmed_incoming[0]["t0"] - trimmed_existing[-1]["t1"]
            if gap > self.max_gap_ms:
                trimmed_existing[-1]["t1"] = trimmed_incoming[0]["t0"]

        trimmed_existing.extend(trimmed_incoming)
        return trimmed_existing

    def _select_window(self, chunks: List[Dict[str, Any]], start: int, end: int) -> Dict[str, Any]:
        texts: List[str] = []
        logprobs: List[float] = []
        for chunk in chunks:
            if chunk["t1"] <= start or chunk["t0"] >= end:
                continue
            if chunk.get("text"):
                texts.append(chunk["text"])
            lp = chunk.get("avg_logprob")
            if lp is not None:
                logprobs.append(float(lp))
        quality = (sum(logprobs) / len(logprobs)) if logprobs else -99.0
        return {"text": " ".join(texts).strip(), "quality": quality}

    def _trim_head(self, chunks: List[Dict[str, Any]], boundary: int) -> List[Dict[str, Any]]:
        trimmed: List[Dict[str, Any]] = []
        for chunk in chunks:
            if chunk["t1"] <= boundary:
                continue
            if chunk["t0"] < boundary < chunk["t1"]:
                chunk = dict(chunk)
                chunk["t0"] = boundary
            trimmed.append(chunk)
        return trimmed

    def _trim_tail(self, chunks: List[Dict[str, Any]], boundary: int) -> List[Dict[str, Any]]:
        trimmed: List[Dict[str, Any]] = []
        for chunk in chunks:
            if chunk["t0"] >= boundary:
                break
            if chunk["t1"] > boundary:
                chunk = dict(chunk)
                chunk["t1"] = boundary
                trimmed.append(chunk)
                break
            trimmed.append(chunk)
        return trimmed

    def _normalized_distance(self, a: str, b: str) -> float:
        if not a and not b:
            return 0.0
        if not a or not b:
            return 1.0
        return self._levenshtein(a, b) / max(len(a), len(b))

    def _levenshtein(self, a: str, b: str) -> int:
        if a == b:
            return 0
        if len(a) < len(b):
            a, b = b, a
        previous = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            current = [i]
            for j, cb in enumerate(b, 1):
                insert = current[j - 1] + 1
                delete = previous[j] + 1
                replace = previous[j - 1] + (ca != cb)
                current.append(min(insert, delete, replace))
            previous = current
        return previous[-1]

    def _format_payload(self, payload: Dict[str, Any], output_path: Path) -> Dict[str, Any]:
        return {
            "path": output_path,
            "payload": payload,
            "segments": [
                {
                    "start": item["t0"] / 1000,
                    "end": item["t1"] / 1000,
                    "text": item.get("text", ""),
                    "avg_logprob": item.get("avg_logprob"),
                }
                for item in payload.get("items", [])
            ],
        }
