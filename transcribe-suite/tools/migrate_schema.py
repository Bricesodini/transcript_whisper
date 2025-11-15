#!/usr/bin/env python3
"""Utility to migrate saved artifacts from schema vN to vN+1."""

import argparse
import json
import sys
from pathlib import Path

TARGET_VERSION = "1.0.0"


def migrate_payload(payload: dict) -> dict:
    """Apply in-place migrations to reach TARGET_VERSION."""
    version = payload.get("schema_version")
    if version == TARGET_VERSION:
        return payload
    if version is None:
        # legacy payloads: assume they match the new shape and just stamp version
        payload["schema_version"] = TARGET_VERSION
        return payload
    raise RuntimeError(f"Migration path from v{version} to v{TARGET_VERSION} not yet implemented")


def migrate_file(path: Path) -> None:
    migrated_lines = []
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                migrated = migrate_payload(payload)
                migrated_lines.append(json.dumps(migrated, ensure_ascii=False))
        with path.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(migrated_lines) + ("\n" if migrated_lines else ""))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        migrated = migrate_payload(payload)
        path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate transcription artifacts to the latest schema version.")
    parser.add_argument("paths", nargs="+", help="JSON/JSONL files to migrate in-place")
    args = parser.parse_args()
    for raw in args.paths:
        path = Path(raw)
        if not path.exists():
            print(f"[skip] {path} not found", file=sys.stderr)
            continue
        migrate_file(path)
        print(f"[ok] migrated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
