#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare two sessionmap project views without comparing raw transcripts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_views(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = {str(item.get("id")): item for item in before.get("decisions", [])}
    new = {str(item.get("id")): item for item in after.get("decisions", [])}
    added = [new[key] for key in sorted(new.keys() - old.keys())]
    removed = [old[key] for key in sorted(old.keys() - new.keys())]
    changed = []
    for key in sorted(old.keys() & new.keys()):
        fields = {}
        for field in ("title", "track", "status", "what", "why", "alternative"):
            if old[key].get(field) != new[key].get(field):
                fields[field] = {"before": old[key].get(field), "after": new[key].get(field)}
        if fields:
            changed.append({"id": key, "title": new[key].get("title") or old[key].get("title"), "fields": fields})

    old_changes = {str(item.get("id")): item for item in before.get("changes", [])}
    new_changes = {str(item.get("id")): item for item in after.get("changes", [])}
    fact_added = [new_changes[key] for key in sorted(new_changes.keys() - old_changes.keys())]
    old_repo = before.get("repository") or {}
    new_repo = after.get("repository") or {}
    repository = {}
    for field in ("branch", "head_sha", "changed_count", "untracked_count"):
        if old_repo.get(field) != new_repo.get(field):
            repository[field] = {"before": old_repo.get(field), "after": new_repo.get(field)}
    return {
        "schema_version": 1,
        "workspace": after.get("workspace") or before.get("workspace"),
        "before_generated_at": before.get("generated_at"),
        "after_generated_at": after.get("generated_at"),
        "decisions": {"added": added, "removed": removed, "changed": changed},
        "facts": {"added": fact_added},
        "repository": repository,
        "summary": {"decisions_added": len(added), "decisions_removed": len(removed),
                     "decisions_changed": len(changed), "facts_added": len(fact_added),
                     "repository_fields_changed": len(repository)},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="比较两个 sessionmap 项目快照")
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = compare_views(load(args.before), load(args.after))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
