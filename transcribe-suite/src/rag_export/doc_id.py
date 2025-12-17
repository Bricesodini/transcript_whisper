"""Doc identifier utilities (slug + deterministic hash)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Optional


def slugify(value: Optional[str], *, max_length: int = 72) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    slug = ascii_text.strip("-")
    if max_length > 0:
        slug = slug[:max_length].rstrip("-")
    return slug


def compute_doc_id(
    title: Optional[str],
    source_key: Optional[str],
    *,
    slug_max_length: int = 72,
    hash_length: int = 8,
    fallback_hash_length: int = 12,
) -> str:
    slug = slugify(title, max_length=slug_max_length)
    reference = (source_key or title or "").encode("utf-8", "ignore")
    digest = hashlib.sha1(reference).hexdigest()
    short_hash = digest[:max(hash_length, 4)]
    fallback_hash = digest[:max(fallback_hash_length, hash_length)]
    if slug:
        candidate = f"{slug}_{short_hash}"
    else:
        candidate = fallback_hash
    if slug_max_length > 0 and len(candidate) > slug_max_length + 1 + len(short_hash):
        candidate = f"{slug[:slug_max_length]}_{short_hash}"
    return candidate or fallback_hash


def resolve_doc_id(
    title: Optional[str],
    source_key: Optional[str],
    doc_cfg: Optional[dict],
    override: Optional[str] = None,
) -> str:
    cfg = doc_cfg or {}
    slug_max = int(cfg.get("slug_max_length", 72))
    hash_len = int(cfg.get("hash_length", 8))
    fallback_len = int(cfg.get("fallback_hash_length", 12))
    if override:
        slug = slugify(override, max_length=slug_max)
        return slug or override
    return compute_doc_id(
        title,
        source_key,
        slug_max_length=slug_max,
        hash_length=hash_len,
        fallback_hash_length=fallback_len,
    )
