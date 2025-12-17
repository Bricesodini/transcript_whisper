from __future__ import annotations

from enum import Enum
from typing import Optional, Tuple


class FailureReason(str, Enum):
    NONE = "none"
    UNC_UNREACHABLE = "unc_unreachable"
    VENV_MISSING_DEPS = "venv_missing_deps"
    PYANNOTE_TOKEN_MISSING = "pyannote_token_missing"
    COMMAND_NOT_FOUND = "command_not_found"
    MOJIBAKE_DETECTED = "mojibake_detected"
    FTS_ACCENT_TEST_FAILED = "fts_accent_test_failed"
    DOC_LOCKED = "doc_locked"
    VALIDATION_FAILED = "validation_failed"
    CANCELED = "canceled"
    DOC_BUSY = "doc_busy"
    UNKNOWN = "unknown"


FAILURE_HINTS = {
    FailureReason.UNC_UNREACHABLE: "Vérifier l'accès NAS / VPN (chemin UNC indisponible).",
    FailureReason.VENV_MISSING_DEPS: "Vérifier le venv Transcribe Suite (pip install -r requirements.lock).",
    FailureReason.PYANNOTE_TOKEN_MISSING: "Exporter PYANNOTE_TOKEN avant de lancer la commande.",
    FailureReason.COMMAND_NOT_FOUND: "Contrôler TS_RUN_BAT_PATH / scripts binaires.",
    FailureReason.MOJIBAKE_DETECTED: "Inspecter l'encodage source (voir rag doctor).",
    FailureReason.FTS_ACCENT_TEST_FAILED: "Relancer rag doctor pour corriger le tokenizer unicode61.",
    FailureReason.DOC_LOCKED: "Une autre opération est en cours sur ce document.",
    FailureReason.DOC_BUSY: "Document occupé par un job write en cours.",
    FailureReason.VALIDATION_FAILED: "Corriger les erreurs de validation signalées dans les logs.",
    FailureReason.CANCELED: "Job annulé manuellement.",
    FailureReason.UNKNOWN: "Consulter le log détaillé pour le diagnostic.",
}


def failure_hint(reason: FailureReason) -> Optional[str]:
    return FAILURE_HINTS.get(reason)


def classify_failure_from_log(
    log_tail: str,
    exit_code: Optional[int],
    canceled: bool,
    forced_reason: Optional[FailureReason] = None,
) -> Tuple[FailureReason, Optional[str]]:
    if forced_reason and forced_reason != FailureReason.NONE:
        return forced_reason, failure_hint(forced_reason)
    log_lower = log_tail.lower()
    if canceled:
        return FailureReason.CANCELED, FAILURE_HINTS[FailureReason.CANCELED]
    matchers = [
        ("pyannote_token", FailureReason.PYANNOTE_TOKEN_MISSING),
        ("token missing", FailureReason.PYANNOTE_TOKEN_MISSING),
        ("could not find venv", FailureReason.VENV_MISSING_DEPS),
        ("module not found", FailureReason.VENV_MISSING_DEPS),
        ("is not recognized as an internal or external command", FailureReason.COMMAND_NOT_FOUND),
        ("system cannot find the path", FailureReason.UNC_UNREACHABLE),
        ("network path was not found", FailureReason.UNC_UNREACHABLE),
        ("unc", FailureReason.UNC_UNREACHABLE),
        ("mojibake", FailureReason.MOJIBAKE_DETECTED),
        ("Ã", FailureReason.MOJIBAKE_DETECTED),
        ("fts accent", FailureReason.FTS_ACCENT_TEST_FAILED),
        ("remove_diacritics", FailureReason.FTS_ACCENT_TEST_FAILED),
        ("validation failed", FailureReason.VALIDATION_FAILED),
        ("locked by another job", FailureReason.DOC_LOCKED),
    ]
    for needle, reason in matchers:
        if needle in log_lower:
            return reason, failure_hint(reason)
    if exit_code and exit_code != 0:
        return FailureReason.UNKNOWN, failure_hint(FailureReason.UNKNOWN)
    return FailureReason.NONE, None
