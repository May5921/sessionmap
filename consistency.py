#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report explicit fact mismatches without guessing intent."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_view(view: dict[str, Any]) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    decisions = view.get("decisions", [])
    unfinished = view.get("unfinished", [])
    todo_by_text = {str(item.get("text", "")).strip(): item for item in unfinished}
    for decision in decisions:
        status = decision.get("status")
        title = decision.get("title") or decision.get("id")
        if status == "done" and not decision.get("evidence_refs"):
            warnings.append({"kind": "decision_without_evidence", "severity": "error", "decision": title,
                             "detail": "已落地决策没有证据链接"})
        if status == "abandoned" and decision.get("evidence_count", 0) == 0:
            warnings.append({"kind": "abandoned_without_evidence", "severity": "warning", "decision": title,
                             "detail": "已放弃状态没有证据链接，需人工确认"})
    repository = view.get("repository") or {}
    if repository.get("available") and repository.get("changed_count", 0) and not repository.get("head_sha"):
        warnings.append({"kind": "git_snapshot_incomplete", "severity": "error",
                         "detail": "Git 有改动但没有 HEAD SHA"})
    return {"schema_version": 1, "workspace": view.get("workspace"), "warnings": warnings,
            "summary": {"warning_count": len(warnings),
                        "error_count": sum(1 for item in warnings if item.get("severity") == "error")}}


def main() -> None:
    ap = argparse.ArgumentParser(description="检查 sessionmap 事实与语义的可验证矛盾")
    ap.add_argument("view")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = check_view(load(args.view))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    if result["summary"]["error_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
