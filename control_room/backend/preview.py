from __future__ import annotations

import asyncio
import difflib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Tuple

PREVIEW_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def _run_regex_preview(text: str, pattern: str, replacement: Optional[str]) -> Tuple[str, int]:
    compiled = re.compile(pattern, flags=re.IGNORECASE)
    replacement_value = replacement or ""
    result, count = compiled.subn(replacement_value, text)
    return result, count


async def preview_with_timeout(
    text: str, pattern: Optional[str], replacement: Optional[str], timeout_ms: int
) -> Dict[str, object]:
    if not pattern:
        return {"source": text, "preview": text, "count": 0, "diff": ""}
    loop = asyncio.get_running_loop()
    try:
        preview, count = await asyncio.wait_for(
            loop.run_in_executor(
                PREVIEW_EXECUTOR, _run_regex_preview, text, pattern, replacement
            ),
            timeout=timeout_ms / 1000,
        )
    except re.error as exc:
        return {
            "source": text,
            "preview": text,
            "count": 0,
            "diff": "",
            "error": f"Regex invalide: {exc}",
        }
    except asyncio.TimeoutError:
        return {
            "source": text,
            "preview": text,
            "count": 0,
            "diff": "",
            "error": "Regex timeout",
        }
    diff = _build_diff(text, preview)
    return {"source": text, "preview": preview, "count": count, "diff": diff}


def _build_diff(source: str, preview: str, context: int = 2, limit: int = 2000) -> str:
    diff_lines = difflib.unified_diff(
        source.splitlines(),
        preview.splitlines(),
        fromfile="source",
        tofile="preview",
        n=context,
    )
    diff_text = "\n".join(diff_lines)
    if len(diff_text) > limit:
        return diff_text[: limit] + "\n...diff truncated..."
    return diff_text
