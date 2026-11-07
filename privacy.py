#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create a share-safe project view without touching the source store."""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


SENSITIVE = re.compile(r"(?i)(\.env\b|credentials?|secrets?|tokens?|passwords?|private[-_]?key|\bauth(?:entication|orization|_?token)?\b)")
ABSOLUTE = re.compile(r"(?i)([A-Z]:[/\\]|\\\\|/Users/|/home/)")


def redact_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = ABSOLUTE.sub("<path>", value)
    return value if not SENSITIVE.search(value) else "<redacted>"


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"source_path", "raw_ref", "session_id", "event_id", "workspace_id", "head_sha"}:
                continue
            if key in {"path", "filePath", "file_path", "command", "quote", "evidence", "summary", "what", "why", "text"}:
                result[key] = redact_text(item)
            else:
                result[key] = sanitize(item)
        return result
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return redact_text(value)


def make_share_view(view: dict[str, Any]) -> dict[str, Any]:
    result = sanitize(copy.deepcopy(view))
    result.setdefault("privacy", {})["redacted"] = True
    result["privacy"]["policy"] = "paths, identifiers, credentials and sensitive evidence removed"
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="生成可分享的 sessionmap 脱敏视图")
    ap.add_argument("view")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = make_share_view(json.loads(Path(args.view).read_text(encoding="utf-8")))
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": args.out, "redacted": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
