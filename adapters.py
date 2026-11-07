#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tool-neutral adapter contracts and read-only source probes.

This module deliberately stops at discovery/normalization boundaries.  A
source adapter may report ``unsupported`` when its on-disk format cannot be
read safely; it must never invent sessions or write to the source directory.
The canonical event and semantic layers consume the contract below, so they do
not need tool-specific branches.
"""
from __future__ import annotations

import abc
import dataclasses
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Iterable, Iterator


SOURCE_KINDS = ("zcode", "codex", "claude", "pi", "dsh", "opencode")
CANONICAL_SCHEMA_VERSION = 1
# Every adapter may emit one of these source-neutral types.  ``source_event``
# is the honest escape hatch for a source record we do not understand yet;
# the original kind remains in the payload for later normalization.
CANONICAL_EVENT_TYPES = frozenset({
    "user_message", "assistant_message", "tool_call", "tool_result",
    "command", "file_change", "todo_change", "subagent_event", "error",
    "memory_break", "git_commit", "git_status", "system_message", "source_event",
})


@dataclasses.dataclass(frozen=True)
class AdapterStatus:
    source_kind: str
    status: str  # ready | unsupported | missing | error
    root: str
    reason: str = ""
    workspaces: int = 0
    sessions: int = 0
    ingestion_mode: str = "stream"
    capabilities: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class WorkspaceRef:
    workspace_id: str
    source_kind: str
    root: str
    name: str
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class SessionRef:
    session_id: str
    workspace_id: str
    source_kind: str
    source_path: str
    title: str = ""
    parent_id: str | None = None
    started_at: int | None = None
    updated_at: int | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class CanonicalEvent:
    event_id: str
    workspace_id: str
    session_id: str | None
    source_kind: str
    source_id: str
    event_type: str
    actor: str
    occurred_at: int | None
    summary: str
    payload: dict[str, Any] = dataclasses.field(default_factory=dict)
    raw_ref: str = ""
    parent_event_id: str | None = None


class SourceAdapter(abc.ABC):
    source_kind: str
    ingestion_mode: str = "stream"

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    @abc.abstractmethod
    def probe(self) -> AdapterStatus:
        raise NotImplementedError

    @abc.abstractmethod
    def discover_workspaces(self) -> list[WorkspaceRef]:
        raise NotImplementedError

    @abc.abstractmethod
    def list_sessions(self, workspace_id: str | None = None) -> list[SessionRef]:
        raise NotImplementedError

    @abc.abstractmethod
    def read_incremental(self, session: SessionRef, cursor: int = 0) -> Iterator[dict[str, Any]]:
        raise NotImplementedError

    @abc.abstractmethod
    def normalize(self, record: dict[str, Any], session: SessionRef) -> CanonicalEvent | None:
        raise NotImplementedError

    def next_cursor(self, session: SessionRef, cursor: int = 0) -> int:
        """Return the source cursor after a successful incremental read."""
        return cursor


class JsonlAdapter(SourceAdapter):
    """Common safe behavior for file-backed JSONL sources."""

    extensions: tuple[str, ...] = (".jsonl",)

    def files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.rglob("*") if p.is_file() and p.suffix.lower() in self.extensions)

    def _read_jsonl(self, path: Path, cursor: int = 0) -> Iterator[dict[str, Any]]:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(cursor)
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    yield item

    def read_incremental(self, session: SessionRef, cursor: int = 0) -> Iterator[dict[str, Any]]:
        yield from self._read_jsonl(Path(session.source_path), cursor)

    def next_cursor(self, session: SessionRef, cursor: int = 0) -> int:
        path = Path(session.source_path)
        return path.stat().st_size if path.exists() else cursor

    @staticmethod
    def _timestamp(record: dict[str, Any]) -> int | None:
        value = record.get("timestamp") or record.get("time") or record.get("created_at")
        if isinstance(value, dict):
            value = value.get("created") or value.get("start")
        if isinstance(value, (int, float)):
            return int(value if value > 10**12 else value * 1000)
        if isinstance(value, str):
            try:
                return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                return None
        return None


class CodexAdapter(JsonlAdapter):
    """Read Codex JSONL and compressed JSONL rollouts without writing them."""

    source_kind = "codex"
    extensions = (".jsonl", ".zst")

    @staticmethod
    def _decoder() -> str | None:
        candidates = [shutil.which("zstd"), r"C:\tools\poppler\Library\bin\zstd.exe"]
        return next((path for path in candidates if path and Path(path).exists()), None)

    @staticmethod
    def _workspace_id(cwd: str) -> str:
        return "codex:" + cwd.replace("\\", "/").lower()

    def _iter_records(self, path: Path, cursor: int = 0) -> Iterator[dict[str, Any]]:
        if not path.name.endswith(".zst"):
            yield from self._read_jsonl(path, cursor)
            return
        decoder = self._decoder()
        if not decoder:
            raise RuntimeError("unsupported: Codex compressed rollouts require zstd.exe")
        process = subprocess.Popen([decoder, "-dc", "--", str(path)], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        line_no = 0
        try:
            assert process.stdout is not None
            for line in process.stdout:
                line_no += 1
                if line_no <= int(cursor):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record
        finally:
            if process.stdout:
                process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()

    def _scan_context(self, path: Path) -> tuple[str | None, str, int | None]:
        session_id = path.stem.replace(".jsonl", "")
        started = None
        for index, record in enumerate(self._iter_records(path)):
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
            context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            cwd = (payload.get("cwd") or payload.get("working_directory") or payload.get("workspace") or
                   context.get("cwd") or context.get("working_directory"))
            if payload.get("type") == "turn_context":
                cwd = cwd or payload.get("cwd")
            started = started or self._timestamp(record)
            if cwd:
                return str(cwd), session_id, started
            if index >= 180:
                break
        return None, session_id, started

    def probe(self) -> AdapterStatus:
        if not self.root.exists():
            return AdapterStatus(self.source_kind, "missing", str(self.root), "rollout directory does not exist",
                                 capabilities=("read-only",))
        files = self.files()
        if not files:
            return AdapterStatus(self.source_kind, "unsupported", str(self.root), "no rollout files found",
                                 capabilities=("read-only",))
        compressed = sum(1 for p in files if p.name.endswith(".zst"))
        if compressed and not self._decoder():
            return AdapterStatus(self.source_kind, "unsupported", str(self.root),
                                 "compressed rollouts found but zstd.exe is unavailable", sessions=len(files),
                                 ingestion_mode="compressed-stream", capabilities=("read-only", "discovery"))
        workspaces = self.discover_workspaces()
        if not workspaces:
            return AdapterStatus(self.source_kind, "unsupported", str(self.root),
                                 "rollouts did not expose a workspace path", sessions=len(files),
                                 ingestion_mode="compressed-stream" if compressed else "stream",
                                 capabilities=("read-only", "discovery"))
        return AdapterStatus(self.source_kind, "ready", str(self.root),
                             "compressed rollout stream; cursor is decompressed line count" if compressed else "",
                             workspaces=len(workspaces), sessions=len(files),
                             ingestion_mode="compressed-stream" if compressed else "stream",
                             capabilities=("read-only", "incremental", "replay"))

    def discover_workspaces(self) -> list[WorkspaceRef]:
        by_root: dict[str, WorkspaceRef] = {}
        for path in self.files():
            cwd, _, _ = self._scan_context(path)
            if not cwd:
                continue
            root = str(Path(cwd).expanduser())
            wid = self._workspace_id(root)
            by_root.setdefault(wid, WorkspaceRef(wid, self.source_kind, root, Path(root).name or root,
                                                 {"format": "codex-rollout-jsonl-zstd" if path.name.endswith('.zst') else "codex-rollout-jsonl"}))
        return sorted(by_root.values(), key=lambda item: item.root.lower())

    def list_sessions(self, workspace_id: str | None = None) -> list[SessionRef]:
        out = []
        for path in self.files():
            cwd, session_id, started = self._scan_context(path)
            if not cwd:
                continue
            wid = self._workspace_id(str(Path(cwd)))
            if workspace_id and wid != workspace_id:
                continue
            out.append(SessionRef(str(session_id), wid, self.source_kind, str(path), path.stem,
                                  started_at=started, updated_at=int(path.stat().st_mtime * 1000),
                                  metadata={"format": "codex-rollout-jsonl-zstd" if path.name.endswith('.zst') else "codex-rollout-jsonl"}))
        return sorted(out, key=lambda item: (item.started_at or 0, item.session_id))

    def read_incremental(self, session: SessionRef, cursor: int = 0) -> Iterator[dict[str, Any]]:
        yield from self._iter_records(Path(session.source_path), cursor)

    def next_cursor(self, session: SessionRef, cursor: int = 0) -> int:
        path = Path(session.source_path)
        if not path.name.endswith(".zst"):
            return super().next_cursor(session, cursor)
        return max(int(cursor or 0), sum(1 for _ in self._iter_records(path)))

    def normalize(self, record: dict[str, Any], session: SessionRef) -> CanonicalEvent | None:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
        kind = str(payload.get("type") or record.get("type") or record.get("event") or "record")
        event_id = str(record.get("id") or record.get("event_id") or payload.get("id") or
                       f"{session.session_id}:{record.get('ordinal', '')}:{kind}")
        content = payload.get("content") if isinstance(payload.get("content"), list) else []
        text = " ".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        if kind in ("message", "UserMessage", "AssistantMessage"):
            role = str(payload.get("role") or "system")
            event_type = "user_message" if role == "user" else "assistant_message"
            actor = "user" if role == "user" else "assistant"
            summary = text or str(payload.get("text") or kind)
        elif kind in ("function_call", "tool_call", "command_execution"):
            event_type, actor = "tool_call", "assistant"
            summary = str(payload.get("name") or payload.get("command") or kind)
        elif kind in ("function_call_output", "tool_result"):
            event_type, actor = "tool_result", "tool"
            summary = str(payload.get("output") or payload.get("result") or kind)
        elif kind in ("turn_context", "session", "response_item"):
            event_type, actor = "source_event", "system"
            summary = str(payload.get("cwd") or payload.get("type") or kind)
        else:
            event_type, actor = "source_event", str(payload.get("role") or "system")
            summary = str(payload.get("text") or payload.get("message") or kind)
        return CanonicalEvent(f"codex:{event_id}", session.workspace_id, session.session_id,
                              self.source_kind, event_id, event_type, actor, self._timestamp(record),
                              " ".join(summary.split())[:600], record, f"file:{session.source_path}")


class ClaudeAdapter(JsonlAdapter):
    source_kind = "claude"

    @staticmethod
    def _content_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
        content = message.get("content")
        if not isinstance(content, list):
            return []
        return [part for part in content if isinstance(part, dict)]

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, list):
            return " ".join(ClaudeAdapter._text(item) for item in value)
        if isinstance(value, dict):
            return str(value.get("text") or value.get("content") or value.get("name") or "")
        return str(value or "")

    @classmethod
    def _canonical_kind(cls, raw_kind: str, record: dict[str, Any],
                        message: dict[str, Any]) -> tuple[str, str, str]:
        """Map Claude's source ``type`` to the source-neutral event contract."""
        kind = raw_kind.lower().strip()
        parts = cls._content_parts(message)
        part_types = {str(part.get("type") or "").lower() for part in parts}
        tool_uses = [part for part in parts if str(part.get("type") or "").lower() in
                     {"tool_use", "tooluse", "tool_call", "toolcall"}]
        tool_results = [part for part in parts if str(part.get("type") or "").lower() in
                        {"tool_result", "toolresult"}]
        text = cls._text(message.get("content") or message.get("text") or record.get("summary") or "")
        if tool_results or kind in {"tool_result", "toolresult"}:
            ids = [str(part.get("tool_use_id") or part.get("id") or "") for part in tool_results]
            detail = text or ", ".join(item for item in ids if item)
            return "tool_result", "tool", detail
        if tool_uses or kind in {"tool_use", "tooluse", "tool_call", "toolcall"}:
            names = [str(part.get("name") or part.get("tool_name") or "tool") for part in tool_uses]
            return "tool_call", "assistant", ", ".join(names) or text
        if kind in {"user", "human", "user_message"} or message.get("role") == "user":
            return "user_message", "user", text
        if kind in {"assistant", "assistant_message", "model"} or message.get("role") == "assistant":
            return "assistant_message", "assistant", text
        if kind in {"error", "api_error", "error_message", "failure", "failed"} or record.get("error"):
            return "error", "system", cls._text(record.get("error") or text or record)
        if "compact" in kind or "context" in kind and "compac" in kind:
            pre = record.get("pre_tokens") or record.get("preTokens") or record.get("preCompactTokenCount")
            post = record.get("post_tokens") or record.get("postTokens") or record.get("truePostCompactTokenCount")
            if pre and post:
                loss = round(100 * (1 - int(post) / int(pre)), 1)
                return "memory_break", "system", f"上下文压缩 {loss:g}% · {int(pre):,} → {int(post):,} token"
            return "source_event", "system", text or kind
        if "subagent" in kind or kind in {"agent_start", "agent_stop", "agent_spawn", "agent_complete"}:
            return "subagent_event", "system", text or kind
        if "todo" in kind or kind in {"task_update", "task_change"}:
            return "todo_change", "system", text or kind
        if kind in {"attachment", "file-history-delta", "file-history-snapshot", "file_change", "file_read", "file_write"} \
                or ("file" in kind and "history" in kind):
            return "file_change", "system", text or kind
        if kind in {"system", "system_message", "progress", "thinking"}:
            return "system_message", "system", text or kind
        return "source_event", str(message.get("role") or "system"), text or kind

    def probe(self) -> AdapterStatus:
        if not self.root.exists():
            return AdapterStatus(self.source_kind, "missing", str(self.root), "projects directory does not exist",
                                 capabilities=("read-only",))
        files = self.files()
        return AdapterStatus(self.source_kind, "ready" if files else "unsupported", str(self.root),
                             "no JSONL sessions found" if not files else "", workspaces=len(self.discover_workspaces()),
                             sessions=len(files), capabilities=("read-only", "incremental", "replay") if files else ("read-only",))

    def discover_workspaces(self) -> list[WorkspaceRef]:
        result = []
        for directory in sorted(p for p in self.root.iterdir() if p.is_dir()):
            files = list(directory.glob("*.jsonl"))
            if files:
                name = directory.name.replace("--", "/")
                result.append(WorkspaceRef(f"claude:{directory.name}", self.source_kind, str(directory), name))
        return result

    def list_sessions(self, workspace_id: str | None = None) -> list[SessionRef]:
        out = []
        for workspace in self.discover_workspaces():
            if workspace_id and workspace.workspace_id != workspace_id:
                continue
            for path in sorted(Path(workspace.root).glob("*.jsonl")):
                out.append(SessionRef(path.stem, workspace.workspace_id, self.source_kind, str(path), path.stem,
                                      metadata={"format": "claude-jsonl"}))
        return out

    def normalize(self, record: dict[str, Any], session: SessionRef) -> CanonicalEvent | None:
        kind = str(record.get("type") or record.get("message", {}).get("role") or "record")
        message = record.get("message") if isinstance(record.get("message"), dict) else record
        event_id = str(record.get("uuid") or record.get("id") or f"{session.session_id}:{record.get('timestamp', '')}:{kind}")
        event_type, actor, text = self._canonical_kind(kind, record, message)
        payload = dict(record)
        payload["raw_kind"] = kind
        return CanonicalEvent(f"claude:{event_id}", session.workspace_id, session.session_id, self.source_kind,
                              event_id, event_type, actor, self._timestamp(record),
                              " ".join(str(text).split())[:600] or event_type, payload,
                              f"file:{session.source_path}")


class ZCodeAdapter(SourceAdapter):
    """Read-only inventory bridge for the ZCode SQLite source.

    ZCode is already normalized by ``sync.py`` because its append cursor is a
    database timestamp rather than a file byte offset.  This adapter keeps it
    in the same discovery/status contract without pretending that the JSONL
    tail importer can safely consume SQLite pages.
    """

    source_kind = "zcode"
    ingestion_mode = "sqlite-sync"

    def _connect(self) -> sqlite3.Connection:
        if not self.root.exists():
            raise FileNotFoundError(self.root)
        con = sqlite3.connect(f"file:{self.root.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _workspace_id(directory: str) -> str:
        return "zcode:" + hashlib.sha1(directory.encode("utf-8", "replace")).hexdigest()[:16]

    def probe(self) -> AdapterStatus:
        if not self.root.exists():
            return AdapterStatus(self.source_kind, "missing", str(self.root), "SQLite source database does not exist",
                                 ingestion_mode="sqlite-sync", capabilities=("read-only", "git-snapshot", "incremental"))
        try:
            con = self._connect()
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"session", "message", "part", "todo"}
            if not required.issubset(tables):
                missing = ", ".join(sorted(required - tables))
                return AdapterStatus(self.source_kind, "unsupported", str(self.root), f"missing ZCode tables: {missing}",
                                     ingestion_mode="sqlite-sync", capabilities=("read-only",))
            sessions = con.execute("SELECT count(*) FROM session WHERE time_deleted IS NULL").fetchone()[0]
            workspaces = con.execute("SELECT count(DISTINCT directory) FROM session WHERE time_deleted IS NULL AND directory IS NOT NULL").fetchone()[0]
            con.close()
        except (OSError, sqlite3.Error) as exc:
            return AdapterStatus(self.source_kind, "error", str(self.root), f"SQLite probe failed: {exc}",
                                 ingestion_mode="sqlite-sync", capabilities=("read-only",))
        if not sessions:
            return AdapterStatus(self.source_kind, "unsupported", str(self.root), "no active ZCode sessions found",
                                 workspaces=workspaces, sessions=sessions, ingestion_mode="sqlite-sync",
                                 capabilities=("read-only", "git-snapshot", "incremental"))
        return AdapterStatus(self.source_kind, "ready", str(self.root),
                             "use sync.py for timestamp-cursor ingestion", workspaces=workspaces,
                             sessions=sessions, ingestion_mode="sqlite-sync",
                             capabilities=("read-only", "git-snapshot", "incremental", "parent-child-sessions"))

    def discover_workspaces(self) -> list[WorkspaceRef]:
        con = self._connect()
        rows = con.execute("SELECT directory, count(*) AS sessions FROM session WHERE time_deleted IS NULL AND directory IS NOT NULL GROUP BY directory ORDER BY directory").fetchall()
        con.close()
        return [WorkspaceRef(self._workspace_id(row["directory"]), self.source_kind, row["directory"],
                             Path(row["directory"]).name or row["directory"],
                             {"sessions": row["sessions"], "format": "zcode-sqlite"}) for row in rows]

    def list_sessions(self, workspace_id: str | None = None) -> list[SessionRef]:
        con = self._connect()
        rows = con.execute("SELECT id,parent_id,title,directory,time_created,time_updated FROM session WHERE time_deleted IS NULL ORDER BY time_created,id").fetchall()
        con.close()
        out = []
        for row in rows:
            wid = self._workspace_id(row["directory"])
            if workspace_id and wid != workspace_id:
                continue
            out.append(SessionRef(str(row["id"]), wid, self.source_kind, str(self.root), row["title"] or "",
                                  row["parent_id"], row["time_created"], row["time_updated"],
                                  {"format": "zcode-sqlite"}))
        return out

    def read_incremental(self, session: SessionRef, cursor: int = 0) -> Iterator[dict[str, Any]]:
        raise RuntimeError("unsupported: ZCode SQLite uses sync.py timestamp-cursor ingestion")

    def normalize(self, record: dict[str, Any], session: SessionRef) -> CanonicalEvent | None:
        return None


class PiAdapter(JsonlAdapter):
    """Read Pi v3 session JSONL files without scanning unrelated directories."""

    source_kind = "pi"
    ingestion_mode = "stream"

    def files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("**/*.jsonl"))

    @staticmethod
    def _session_header(path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                first = json.loads(handle.readline())
            return first if isinstance(first, dict) and first.get("type") == "session" else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _workspace_id(cwd: str) -> str:
        return "pi:" + cwd.replace("\\", "/").lower()

    def probe(self) -> AdapterStatus:
        if not self.root.exists():
            return AdapterStatus(self.source_kind, "missing", str(self.root), "Pi sessions directory does not exist",
                                 capabilities=("read-only",))
        files = self.files()
        headers = [self._session_header(path) for path in files]
        valid = [header for header in headers if header.get("cwd") and header.get("id")]
        workspaces = len({self._workspace_id(str(header["cwd"])) for header in valid})
        if not valid:
            return AdapterStatus(self.source_kind, "unsupported", str(self.root),
                                 "no Pi v3 JSONL sessions with cwd were found", ingestion_mode="stream",
                                 capabilities=("read-only", "discovery"))
        return AdapterStatus(self.source_kind, "ready", str(self.root), workspaces=workspaces,
                             sessions=len(valid), ingestion_mode="stream",
                             capabilities=("read-only", "incremental", "replay", "parent-child-sessions"))

    def discover_workspaces(self) -> list[WorkspaceRef]:
        by_root: dict[str, WorkspaceRef] = {}
        for path in self.files():
            header = self._session_header(path)
            cwd = header.get("cwd")
            if not cwd:
                continue
            root = str(Path(cwd).expanduser())
            wid = self._workspace_id(root)
            by_root.setdefault(wid, WorkspaceRef(wid, self.source_kind, root, Path(root).name or root,
                                                 {"format": "pi-session-v3"}))
        return sorted(by_root.values(), key=lambda item: item.root.lower())

    def list_sessions(self, workspace_id: str | None = None) -> list[SessionRef]:
        out: list[SessionRef] = []
        for path in self.files():
            header = self._session_header(path)
            cwd = header.get("cwd")
            if not cwd:
                continue
            root = str(Path(cwd).expanduser())
            wid = self._workspace_id(root)
            if workspace_id and wid != workspace_id:
                continue
            out.append(SessionRef(str(header.get("id") or path.stem), wid, self.source_kind, str(path),
                                  str(header.get("title") or path.stem), metadata={"format": "pi-session-v3"},
                                  started_at=self._timestamp(header)))
        return sorted(out, key=lambda item: (item.started_at or 0, item.session_id))

    def normalize(self, record: dict[str, Any], session: SessionRef) -> CanonicalEvent | None:
        if record.get("type") == "session":
            return None
        if record.get("type") != "message":
            if record.get("type") in ("compaction", "context_compaction"):
                payload = record.get("data") if isinstance(record.get("data"), dict) else record
                pre = payload.get("preTokens") or payload.get("preCompactTokenCount") or 0
                post = payload.get("postTokens") or payload.get("truePostCompactTokenCount") or 0
                if pre and post:
                    loss = round(100 * (1 - post / pre), 1)
                    return CanonicalEvent(f"pi:{record.get('id') or session.session_id + ':' + str(record.get('timestamp'))}",
                                          session.workspace_id, session.session_id, self.source_kind,
                                          str(record.get("id") or record.get("timestamp")), "memory_break", "system",
                                          self._timestamp(record), f"上下文压缩 {loss:g}% · {pre:,} → {post:,} token",
                                          {"pre_tokens": pre, "post_tokens": post, "loss_pct": loss},
                                          f"file:{session.source_path}")
            return None
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        role = str(message.get("role") or "system")
        parts = message.get("content") if isinstance(message.get("content"), list) else []
        texts: list[str] = []
        tool_names: list[str] = []
        payload_parts: list[dict[str, Any]] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "text":
                texts.append(str(part.get("text") or ""))
            elif kind in ("toolCall", "tool_call", "toolUse", "tool_use"):
                name = str(part.get("name") or part.get("toolName") or "tool")
                tool_names.append(name)
                payload_parts.append(part)
            elif kind in ("toolResult", "tool_result"):
                payload_parts.append(part)
        event_id = str(record.get("id") or f"{session.session_id}:{record.get('timestamp')}:{role}")
        if tool_names:
            event_type, actor = "tool_call", "assistant"
            summary = ", ".join(tool_names)
        elif role == "user":
            event_type, actor = "user_message", "user"
            summary = " ".join(texts)
        elif role == "assistant":
            event_type, actor = "assistant_message", "assistant"
            summary = " ".join(texts)
        else:
            event_type, actor = "system_message", "system"
            summary = " ".join(texts)
        summary = " ".join(summary.split())[:600] or event_type
        return CanonicalEvent(f"pi:{event_id}", session.workspace_id, session.session_id, self.source_kind,
                              event_id, event_type, actor, self._timestamp(record), summary,
                              {"message": message, "parts": payload_parts}, f"file:{session.source_path}",
                              str(record.get("parentId")) if record.get("parentId") else None)


class OpenCodeAdapter(SourceAdapter):
    """Read OpenCode's local SQLite session store without writing to it."""

    source_kind = "opencode"
    ingestion_mode = "sqlite-sync"

    def _connect(self) -> sqlite3.Connection:
        if not self.root.exists():
            raise FileNotFoundError(self.root)
        con = sqlite3.connect(f"file:{self.root.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _workspace_id(directory: str) -> str:
        return "opencode:" + hashlib.sha1(directory.replace("\\", "/").lower().encode()).hexdigest()[:16]

    def _session_table(self, con: sqlite3.Connection) -> str:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "session_v2" in tables and con.execute("SELECT count(*) FROM session_v2").fetchone()[0]:
            return "session_v2"
        return "session"

    def probe(self) -> AdapterStatus:
        if not self.root.exists():
            return AdapterStatus(self.source_kind, "missing", str(self.root), "OpenCode SQLite store does not exist",
                                 ingestion_mode="sqlite-sync", capabilities=("read-only",))
        try:
            con = self._connect()
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            session_table = "session_v2" if "session_v2" in tables and con.execute("SELECT count(*) FROM session_v2").fetchone()[0] else "session"
            required = {session_table, "part"}
            if not ({"message", "session_message"} & tables):
                required.add("message")
            if not required.issubset(tables):
                missing = ", ".join(sorted(required - tables))
                return AdapterStatus(self.source_kind, "unsupported", str(self.root), f"missing OpenCode tables: {missing}",
                                     ingestion_mode="sqlite-sync", capabilities=("read-only",))
            sessions = con.execute(f"SELECT count(*) FROM {session_table}").fetchone()[0]
            workspaces = con.execute(f"SELECT count(DISTINCT directory) FROM {session_table} WHERE directory IS NOT NULL").fetchone()[0]
            con.close()
        except (OSError, sqlite3.Error) as exc:
            return AdapterStatus(self.source_kind, "error", str(self.root), f"SQLite probe failed: {exc}",
                                 ingestion_mode="sqlite-sync", capabilities=("read-only",))
        if not sessions:
            return AdapterStatus(self.source_kind, "unsupported", str(self.root), "no OpenCode sessions found",
                                 workspaces=workspaces, sessions=sessions, ingestion_mode="sqlite-sync",
                                 capabilities=("read-only", "discovery"))
        return AdapterStatus(self.source_kind, "ready", str(self.root),
                             "SQLite timestamp cursor", workspaces=workspaces, sessions=sessions,
                             ingestion_mode="sqlite-sync",
                             capabilities=("read-only", "incremental", "replay", "parent-child-sessions", "todo"))

    def discover_workspaces(self) -> list[WorkspaceRef]:
        con = self._connect()
        table = self._session_table(con)
        rows = con.execute(f"SELECT directory, count(*) AS sessions FROM {table} WHERE directory IS NOT NULL GROUP BY directory ORDER BY directory").fetchall()
        con.close()
        return [WorkspaceRef(self._workspace_id(str(row["directory"])), self.source_kind, str(row["directory"]),
                             Path(str(row["directory"])).name or str(row["directory"]),
                             {"format": "opencode-sqlite", "sessions": row["sessions"]}) for row in rows]

    def list_sessions(self, workspace_id: str | None = None) -> list[SessionRef]:
        con = self._connect()
        table = self._session_table(con)
        rows = con.execute(f"SELECT id,parent_id,title,directory,time_created,time_updated FROM {table} ORDER BY time_created,id").fetchall()
        con.close()
        out: list[SessionRef] = []
        for row in rows:
            root = str(row["directory"])
            wid = self._workspace_id(root)
            if workspace_id and wid != workspace_id:
                continue
            out.append(SessionRef(str(row["id"]), wid, self.source_kind, str(self.root), row["title"] or "",
                                  row["parent_id"], row["time_created"], row["time_updated"],
                                  {"format": "opencode-sqlite"}))
        return out

    def read_incremental(self, session: SessionRef, cursor: int = 0) -> Iterator[dict[str, Any]]:
        con = self._connect()
        try:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            joins: list[str] = []
            message_sources: list[str] = []
            if "message" in tables:
                joins.append("LEFT JOIN message m ON m.id=p.message_id")
                message_sources.append("m.data")
            if "session_message" in tables:
                joins.append("LEFT JOIN session_message sm ON sm.id=p.message_id")
                message_sources.append("sm.data")
            if len(message_sources) > 1:
                message_expr = "COALESCE(" + ",".join(message_sources) + ")"
            else:
                message_expr = message_sources[0] if message_sources else "NULL"
            # Parts are the fine-grained event stream.  The message joins provide
            # the role without loading the full database into memory.
            rows = con.execute(
                f"""SELECT p.id,p.message_id,p.session_id,p.time_created,p.data,
                          {message_expr} AS message_data
                     FROM part p
                     {' '.join(joins)}
                    WHERE p.session_id=? AND p.time_created>=?
                    ORDER BY p.time_created,p.id""",
                (session.session_id, max(0, int(cursor) - 1)),
            )
            for row in rows:
                yield {"record_type": "part", **dict(row)}
            todo_rows = con.execute(
                "SELECT session_id,content,status,priority,position,time_created,time_updated FROM todo WHERE session_id=? AND time_updated>=? ORDER BY time_updated,position",
                (session.session_id, max(0, int(cursor) - 1)),
            )
            for row in todo_rows:
                yield {"record_type": "todo", **dict(row)}
        finally:
            con.close()

    def next_cursor(self, session: SessionRef, cursor: int = 0) -> int:
        con = self._connect()
        row = con.execute("SELECT max(time_created) FROM part WHERE session_id=?", (session.session_id,)).fetchone()
        todo = con.execute("SELECT max(time_updated) FROM todo WHERE session_id=?", (session.session_id,)).fetchone()
        con.close()
        return max(int(cursor or 0), int(row[0] or 0), int(todo[0] or 0))

    def normalize(self, record: dict[str, Any], session: SessionRef) -> CanonicalEvent | None:
        if record.get("record_type") == "todo":
            event_id = f"{session.session_id}:todo:{record.get('position')}"
            content = str(record.get("content") or "")
            status = str(record.get("status") or "")
            return CanonicalEvent(f"opencode:{event_id}", session.workspace_id, session.session_id,
                                  self.source_kind, event_id, "todo_change", "system",
                                  int(record.get("time_updated") or record.get("time_created") or 0),
                                  f"[{status}] {content}".strip(), dict(record), f"sqlite:{self.root}")
        data = record.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}
        data = data if isinstance(data, dict) else {}
        message = record.get("message_data")
        if isinstance(message, str):
            try:
                message = json.loads(message)
            except json.JSONDecodeError:
                message = {}
        message = message if isinstance(message, dict) else {}
        role = str(message.get("role") or "assistant")
        kind = str(data.get("type") or "record")
        part_id = str(record.get("id") or f"{session.session_id}:{record.get('time_created')}:{kind}")
        if kind == "text":
            event_type, actor = ("user_message", "user") if role == "user" else ("assistant_message", "assistant")
            summary = str(data.get("text") or "")
        elif kind == "tool":
            event_type, actor = "tool_call", "assistant"
            state = data.get("state") if isinstance(data.get("state"), dict) else {}
            inp = state.get("input") or {}
            detail = inp.get("filePath") or inp.get("path") or inp.get("command") or inp.get("url") or ""
            summary = f"{data.get('tool') or 'tool'}{(' · ' + str(detail)) if detail else ''}"
        elif kind in ("reasoning", "step-start", "step-finish"):
            if kind == "step-finish" and data.get("reason") in ("error", "failed"):
                event_type, actor = "error", "system"
            else:
                event_type, actor = "assistant_message", "assistant"
            summary = str(data.get("text") or data.get("reason") or kind)
        else:
            event_type, actor, summary = "source_event", "system", kind
        summary = " ".join(summary.split())[:600] or event_type
        return CanonicalEvent(f"opencode:{part_id}", session.workspace_id, session.session_id,
                              self.source_kind, part_id, event_type, actor, int(record.get("time_created") or 0),
                              summary, data, f"sqlite:{self.root}")


class DSHAdapter(SourceAdapter):
    """Read DSH v4 sessions through an installed zstd decoder.

    DSH stores compressed JSONL.  The cursor is a decompressed line count,
    because compressed byte offsets are not safe seek points.  This remains
    read-only and makes the trade-off explicit in the adapter status.
    """

    source_kind = "dsh"
    ingestion_mode = "compressed-stream"

    def __init__(self, root: str | Path):
        super().__init__(root)
        self._line_counts: dict[str, int] = {}

    def files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted([*self.root.rglob("*.jsonl.zstd"), *self.root.rglob("*.jsonl.zst")])

    @staticmethod
    def _decoder() -> str | None:
        candidates = [shutil.which("zstd"), r"C:\tools\poppler\Library\bin\zstd.exe"]
        return next((path for path in candidates if path and Path(path).exists()), None)

    def _iter_records(self, path: Path, cursor: int = 0) -> Iterator[dict[str, Any]]:
        decoder = self._decoder()
        if not decoder:
            raise RuntimeError("unsupported: DSH .zstd sessions require zstd.exe")
        process = subprocess.Popen([decoder, "-dc", "--", str(path)], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        line_no = 0
        try:
            assert process.stdout is not None
            for line in process.stdout:
                line_no += 1
                if line_no <= int(cursor):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record
        finally:
            if process.stdout:
                process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()
            self._line_counts[str(path)] = max(self._line_counts.get(str(path), 0), line_no)

    @staticmethod
    def _header(path: Path) -> dict[str, Any]:
        try:
            return next(iter(DSHAdapter(path.parent)._iter_records(path)), {})
        except (OSError, RuntimeError):
            return {}

    @staticmethod
    def _workspace_id(cwd: str) -> str:
        return "dsh:" + hashlib.sha1(cwd.replace("\\", "/").lower().encode()).hexdigest()[:16]

    def probe(self) -> AdapterStatus:
        if not self.root.exists():
            return AdapterStatus(self.source_kind, "missing", str(self.root), "DSH sessions directory does not exist",
                                 ingestion_mode=self.ingestion_mode, capabilities=("read-only",))
        files = self.files()
        decoder = self._decoder()
        if not files:
            return AdapterStatus(self.source_kind, "unsupported", str(self.root), "no compressed DSH sessions found",
                                 ingestion_mode=self.ingestion_mode, capabilities=("read-only", "discovery"))
        if not decoder:
            return AdapterStatus(self.source_kind, "unsupported", str(self.root),
                                 "DSH sessions found but zstd.exe is unavailable", sessions=len(files),
                                 ingestion_mode=self.ingestion_mode, capabilities=("read-only", "compressed-discovery"))
        valid = 0
        workspaces: set[str] = set()
        for path in files:
            header = self._header(path)
            if header.get("type") == "session" and header.get("cwd"):
                valid += 1
                workspaces.add(self._workspace_id(str(header["cwd"])))
        if not valid:
            return AdapterStatus(self.source_kind, "unsupported", str(self.root),
                                 "zstd files do not expose DSH v4 session headers", sessions=len(files),
                                 ingestion_mode=self.ingestion_mode, capabilities=("read-only", "compressed-discovery"))
        return AdapterStatus(self.source_kind, "ready", str(self.root),
                             "compressed JSONL; cursor is decompressed line count", workspaces=len(workspaces),
                             sessions=valid, ingestion_mode=self.ingestion_mode,
                             capabilities=("read-only", "incremental", "replay", "parent-child-sessions", "compressed-stream"))

    def discover_workspaces(self) -> list[WorkspaceRef]:
        by_root: dict[str, WorkspaceRef] = {}
        for path in self.files():
            header = self._header(path)
            cwd = header.get("cwd")
            if not cwd:
                continue
            root = str(Path(cwd).expanduser())
            wid = self._workspace_id(root)
            by_root.setdefault(wid, WorkspaceRef(wid, self.source_kind, root, Path(root).name or root,
                                                 {"format": "dsh-session-v4"}))
        return sorted(by_root.values(), key=lambda item: item.root.lower())

    def list_sessions(self, workspace_id: str | None = None) -> list[SessionRef]:
        out: list[SessionRef] = []
        for path in self.files():
            header = self._header(path)
            cwd = header.get("cwd")
            if not cwd:
                continue
            root = str(Path(cwd).expanduser())
            wid = self._workspace_id(root)
            if workspace_id and wid != workspace_id:
                continue
            out.append(SessionRef(str(header.get("id") or path.stem), wid, self.source_kind, str(path),
                                  str(header.get("title") or path.stem), header.get("parentSession"),
                                  header.get("createdAt"), metadata={"format": "dsh-session-v4"}))
        return sorted(out, key=lambda item: (item.started_at or 0, item.session_id))

    def read_incremental(self, session: SessionRef, cursor: int = 0) -> Iterator[dict[str, Any]]:
        yield from self._iter_records(Path(session.source_path), cursor)

    def next_cursor(self, session: SessionRef, cursor: int = 0) -> int:
        path = str(session.source_path)
        if path not in self._line_counts:
            for _ in self._iter_records(Path(path), 10**18):
                pass
        return max(int(cursor or 0), self._line_counts.get(path, 0))

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(DSHAdapter._text(item.get("text") if isinstance(item, dict) else item) for item in value)
        if isinstance(value, dict):
            return DSHAdapter._text(value.get("text") or value.get("content") or value.get("message") or "")
        return str(value or "")

    def normalize(self, record: dict[str, Any], session: SessionRef) -> CanonicalEvent | None:
        kind = str(record.get("type") or "")
        if kind == "session":
            return None
        data = record.get("data") if isinstance(record.get("data"), dict) else record
        actor = "system"
        event_type = "source_event"
        summary = kind
        if kind == "user/message":
            actor, event_type, summary = "user", "user_message", self._text(record.get("content") or data.get("content"))
        elif kind == "assistant/message":
            actor, event_type = "assistant", "assistant_message"
            message = record.get("message") if isinstance(record.get("message"), dict) else data.get("message")
            summary = self._text(message or record.get("content") or data.get("content"))
        elif kind == "tool/call":
            actor, event_type = "assistant", "tool_call"
            summary = f"{record.get('name') or data.get('name') or 'tool'} {self._text(record.get('arguments') or data.get('arguments'))}".strip()
        elif kind == "tool/result":
            actor, event_type = "tool", "tool_result"
            summary = self._text(record.get("content") or data.get("content") or data.get("message"))
        elif "error" in kind or record.get("error"):
            event_type, summary = "error", self._text(record.get("error") or data)
        elif "compaction" in kind or kind in ("context/compact", "context/compaction"):
            event_type, summary = "memory_break", self._text(data)
        elif kind in ("subagent/descriptor", "agent/spawn", "agent/created"):
            event_type, summary = "subagent_event", self._text(data)
        elif kind in ("todo/change", "todo/updated"):
            event_type, summary = "todo_change", self._text(data)
        else:
            # Keep high-signal lifecycle records; drop harness noise such as
            # sandbox and approval metadata from the canonical event stream.
            if kind in ("sandbox/mode", "approval/policy", "permission/preset", "request/context", "request/header"):
                return None
            summary = self._text(record.get("message") or record.get("content") or data)
        event_id = str(record.get("id") or f"{session.session_id}:{record.get('seq', '')}:{kind}")
        timestamp = record.get("time") or record.get("createdAt") or record.get("timestamp")
        if isinstance(timestamp, dict):
            timestamp = timestamp.get("created") or timestamp.get("start")
        try:
            occurred = int(timestamp if timestamp and int(timestamp) > 10**12 else int(timestamp or 0) * 1000)
        except (TypeError, ValueError):
            occurred = None
        return CanonicalEvent(f"dsh:{event_id}", session.workspace_id, session.session_id, self.source_kind,
                              event_id, event_type, actor, occurred, " ".join(summary.split())[:600],
                              record, f"file:{session.source_path}", str(record.get("parentId")) if record.get("parentId") else None)


class UnsupportedAdapter(SourceAdapter):
    def __init__(self, source_kind: str, root: str | Path, reason: str):
        super().__init__(root)
        self.source_kind = source_kind
        self.reason = reason

    def probe(self) -> AdapterStatus:
        status = "missing" if not self.root.exists() else "unsupported"
        return AdapterStatus(self.source_kind, status, str(self.root), self.reason)

    def discover_workspaces(self) -> list[WorkspaceRef]:
        return []

    def list_sessions(self, workspace_id: str | None = None) -> list[SessionRef]:
        return []

    def read_incremental(self, session: SessionRef, cursor: int = 0) -> Iterator[dict[str, Any]]:
        raise RuntimeError(f"unsupported: {self.reason}")
        yield {}

    def normalize(self, record: dict[str, Any], session: SessionRef) -> CanonicalEvent | None:
        return None


def default_adapters(home: str | Path | None = None) -> dict[str, SourceAdapter]:
    home_path = Path(home or Path.home()).expanduser()
    opencode_store = home_path / ".local" / "share" / "opencode" / "opencode.db"
    if not opencode_store.exists():
        opencode_store = home_path / ".config" / "opencode"
    return {
        "zcode": ZCodeAdapter(home_path / ".zcode" / "cli" / "db" / "db.sqlite"),
        "codex": CodexAdapter(home_path / ".codex" / "sessions"),
        "claude": ClaudeAdapter(home_path / ".claude" / "projects"),
        "pi": PiAdapter(home_path / ".pi" / "agent" / "sessions"),
        "dsh": DSHAdapter(home_path / ".dsh" / "sessions"),
        "opencode": OpenCodeAdapter(opencode_store),
    }


def validate_store_schema(store_path: str | Path) -> dict[str, Any]:
    required = {
        "workspace": {"workspace_id", "source_kind", "source_path"},
        "session": {"session_id", "workspace_id", "source_kind", "source_id"},
        "event": {"event_id", "workspace_id", "source_kind", "source_id", "event_type", "fingerprint"},
        "repository_snapshot": {"workspace_id", "branch", "head_sha"},
    }
    con = sqlite3.connect(str(store_path))
    result: dict[str, Any] = {"schema_version": CANONICAL_SCHEMA_VERSION, "tables": {}}
    try:
        for table, columns in required.items():
            actual = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            result["tables"][table] = {"required": sorted(columns), "missing": sorted(columns - actual)}
        kinds = []
        if result["tables"].get("workspace", {}).get("missing") == []:
            kinds = [row[0] for row in con.execute("SELECT DISTINCT source_kind FROM workspace")]
        result["source_kinds"] = kinds
        result["unknown_source_kinds"] = sorted(set(kinds) - set(SOURCE_KINDS))
        result["ok"] = all(not value["missing"] for value in result["tables"].values()) and not result["unknown_source_kinds"]
    finally:
        con.close()
    return result


def probe_all(home: str | Path | None = None) -> list[dict[str, Any]]:
    return [dataclasses.asdict(adapter.probe()) for adapter in default_adapters(home).values()]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="探测 sessionmap 可用的数据源适配器")
    parser.add_argument("--home", default=None)
    parser.add_argument("--store", default=None)
    args = parser.parse_args()
    payload: dict[str, Any] = {"adapters": probe_all(args.home)}
    if args.store:
        payload["schema"] = validate_store_schema(args.store)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
