"""Read-only Claude/Codex adapters with exact identity and local provenance.

Decisions are grouped by assistant message or outer call. A completion is
not a new decision: it inherits its request time, including when parallel
inner calls finish in reverse order. An unmatched completion stays unknown.
No JavaScript interpretation, command execution, or live store imports.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re

from .paths import absolute, completed_command, requested_files


def epoch(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result = float(value)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            result = parsed.timestamp()
        return result if math.isfinite(result) else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def records(path, diagnostics):
    """Ignore an unfinished last line, not the already complete prefix."""
    with path.open("rb") as stream:
        for line, raw in enumerate(stream, 1):
            if not raw.endswith(b"\n"):
                diagnostics["partial_tail"] += 1
                break
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                diagnostics["malformed_record"] += 1
                continue
            if isinstance(row, dict):
                yield line, row
            else:
                diagnostics["malformed_record"] += 1


def excluded(row):
    flags = ("isSidechain", "isCompactSummary", "isSummary", "isMeta", "isReplay",
             "replayed", "inherited", "isInherited")
    return (any(row.get(k) for k in flags)
            or row.get("type") in ("summary", "compacted", "replacement_history")
            or row.get("phase") in ("summary", "analysis", "reasoning")
            or row.get("channel") == "analysis")


def prose(content):
    if isinstance(content, str):
        return clean_text(content)
    if not isinstance(content, list):
        return ""
    return "\n".join(clean_text(b.get("text", "")) for b in content
                     if isinstance(b, dict) and str(b.get("type", "")).lower() in ("text", "output_text"))


def clean_text(text):
    if not isinstance(text, str):
        return ""
    if text.lstrip().startswith(("KG MEMORY PRELOADED", "KG recall — memory matching",
                                "This session is being continued from a previous conversation")):
        return ""
    return re.sub(r"<(system-reminder|environment_context|INSTRUCTIONS|summary)>.*?</\1>",
                  "", text, flags=re.S)


def arguments(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return None
    return value if isinstance(value, dict) else None


def action(name, args, cwd, line, ts, call_id):
    files, edits = requested_files(name, args, cwd)
    short = name.split("__")[-1].split(".")[-1]
    file_observable = (bool(files) if short in ("Bash", "exec_command", "shell_command", "Read", "Edit",
                                               "Write", "MultiEdit", "NotebookEdit", "apply_patch") else None)
    return {"name": name, "args": args, "files": files, "edit_files": edits,
            "line": line, "request_line": line, "ts": ts, "id": call_id,
            "status": "requested", "completed_ts": None, "file_observable": file_observable}


def completion_status(item):
    result = item.get("result")
    if (item.get("status") in ("failed", "error", "declined", "cancelled")
            or item.get("exit_code") not in (None, 0)
            or isinstance(result, dict) and result.get("isError")):
        return "failed"
    return "completed" if item.get("status") == "completed" else "requested"


def claude(path, sid):
    diagnostics, groups, users, calls = Counter(), {}, {}, {}
    identities, seen_rows, unknown = set(), set(), []
    for line, row in records(path, diagnostics):
        identity = row.get("sessionId")
        if identity:
            identities.add(identity)
        if identity and identity != sid or excluded(row):
            diagnostics["excluded_context"] += 1
            continue
        stamp = epoch(row.get("timestamp"))
        if stamp is None:
            continue
        uid = row.get("uuid")
        if uid and uid in seen_rows:
            diagnostics["duplicate_record"] += 1
            continue
        if uid:
            seen_rows.add(uid)
        message = row.get("message")
        if not isinstance(message, dict) or excluded(message):
            continue
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        if row.get("type") == "user":
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            for block in results:
                call = calls.get(block.get("tool_use_id"))
                if call:
                    call.update(status="failed" if block.get("is_error") else "completed",
                                completed_ts=stamp, completion_line=line)
            origin = row.get("origin")
            human = (origin.get("kind") == "human" if isinstance(origin, dict)
                     else row.get("userType") == "external")
            if not results and human:
                users.setdefault(uid or str(line), stamp)
        if row.get("type") != "assistant":
            continue
        key = message.get("id") or uid or str(line)
        group = groups.setdefault(key, {"id": key, "ts": stamp, "line": line,
                                        "prose": [], "tools": []})
        group["ts"] = min(group["ts"], stamp)
        if isinstance(content, str):
            group["prose"].append({"text": clean_text(content), "line": line, "ts": stamp})
        for block in blocks:
            if not isinstance(block, dict) or excluded(block):
                continue
            if block.get("type") == "text":
                group["prose"].append({"text": clean_text(block.get("text")), "line": line, "ts": stamp})
            elif block.get("type") == "tool_use":
                args = arguments(block.get("input"))
                cid = block.get("id")
                if args is None or not isinstance(block.get("name"), str):
                    diagnostics["unobservable_operation"] += 1
                    unknown.append({"line": line, "ts": stamp, "group": key,
                                    "reason": "unobservable_operation"})
                    continue
                if cid and cid in calls:
                    continue
                tool = action(block["name"], args, row.get("cwd", ""), line, stamp, cid)
                group["tools"].append(tool)
                if cid:
                    calls[cid] = tool
    status = "resolved" if sid in identities else "identity_unknown"
    # Every block in one assistant message was underway at its first block.
    for group in groups.values():
        for tool in group["tools"]:
            tool["ts"] = group["ts"]
    for entry in unknown:
        entry["ts"] = groups[entry.pop("group")]["ts"]
    return timeline(path, sid, "claude-code", status, groups, users, diagnostics, unknown)


def codex(path, sid):
    diagnostics, groups, users = Counter(), {}, {}
    active, aliases, completed, starts, unknown = {}, {}, set(), {}, []
    identities, cwd, created, fork = set(), "", None, False
    for line, row in records(path, diagnostics):
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        stamp = epoch(row.get("timestamp"))
        if row.get("type") == "session_meta":
            identities.update(x for x in (payload.get("id"), payload.get("session_id")) if isinstance(x, str))
            cwd = absolute(payload.get("cwd")) or ""
            created = epoch(payload.get("timestamp"))
            fork = bool(payload.get("forked_from_id") or payload.get("parent_session_id")
                        or payload.get("forked_from_session_id"))
            continue
        if excluded(row) or excluded(payload) or stamp is None or created and stamp < created:
            diagnostics["excluded_context"] += 1
            continue
        if row.get("type") == "turn_context":
            cwd = absolute(payload.get("cwd")) or cwd
        typ = payload.get("type")
        if row.get("type") == "response_item":
            metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
            if typ == "message" and payload.get("role") == "user":
                kinds = metadata.get("content_item_kinds", []) if isinstance(metadata, dict) else []
                if "user.text" in kinds:
                    users.setdefault(payload.get("id") or str(line), stamp)
                elif not kinds:
                    diagnostics["unobservable_user_origin"] += 1
                else:
                    diagnostics["excluded_user_context"] += 1
            if (typ == "message" and payload.get("role") == "assistant"
                    or typ in ("function_call", "custom_tool_call")):
                key = payload.get("call_id") or payload.get("id") or str(line)
                group = groups.setdefault(key, {"id": key, "ts": stamp, "line": line,
                                                "prose": [], "tools": []})
                group["ts"] = min(group["ts"], stamp)
                if typ == "message":
                    if not group.get("response_prose"):
                        group["prose"] = [{"text": prose(payload.get("content")), "line": line, "ts": stamp}]
                        group["response_prose"] = True
                elif key not in active:
                    group["turn"] = metadata.get("turn_id") if isinstance(metadata, dict) else None
                    group["wrapper"] = typ == "custom_tool_call" and payload.get("name") in ("exec", "functions.exec")
                    if group["wrapper"]:
                        diagnostics["js_wrappers"] += 1
                    else:
                        args = ({"input": payload.get("input")} if typ == "custom_tool_call"
                                and payload.get("name", "").split(".")[-1] == "apply_patch"
                                else arguments(payload.get("arguments")))
                        if args is not None and isinstance(payload.get("name"), str):
                            group["tools"].append(action(payload["name"], args, cwd, line, stamp, key))
                        else:
                            unknown.append({"line": line, "ts": stamp, "reason": "unobservable_operation"})
                    active[key] = group
                    for alias in (payload.get("id"), payload.get("call_id")):
                        if alias:
                            aliases[alias] = key
            if typ in ("function_call_output", "custom_tool_call_output"):
                key = aliases.get(payload.get("call_id"), payload.get("call_id"))
                group = active.pop(key, None)
                if group and not group.get("wrapper"):
                    for tool in group["tools"]:
                        if tool["completed_ts"] is None:
                            output = arguments(payload.get("output"))
                            tool.update(status="failed" if output and output.get("isError") else "completed",
                                        completed_ts=stamp, completion_line=line)
                if group and group.get("wrapper") and not group["tools"]:
                    unknown.append({"line": group["line"], "ts": group["ts"],
                                    "reason": "unobservable_wrapper"})
        if row.get("type") != "event_msg" or typ not in ("item_started", "item_completed"):
            continue
        item = payload.get("item")
        if not isinstance(item, dict) or excluded(item):
            continue
        kind, iid = item.get("type"), item.get("id")
        if typ == "item_started":
            if iid:
                starts[iid] = (stamp, line)
            continue
        if kind == "AgentMessage" and iid:
            group = groups.setdefault(iid, {"id": iid, "ts": stamp, "line": line,
                                           "prose": [], "tools": []})
            group["ts"] = min(group["ts"], stamp)
            if not group["prose"]:
                group["prose"] = [{"text": prose(item.get("content")), "line": line, "ts": stamp}]
            continue
        if kind not in ("CommandExecution", "McpToolCall", "FileChange"):
            continue
        if not iid:
            unknown.append({"line": line, "ts": stamp, "reason": "completion_without_id"})
            continue
        if iid in completed:
            diagnostics["duplicate_completion"] += 1
            continue
        completed.add(iid)
        parent = item.get("parent_call_id") or payload.get("parent_call_id")
        direct_id = item.get("call_id") or payload.get("call_id") or iid
        key = (aliases.get(direct_id, direct_id) if direct_id in aliases or direct_id in groups
               else aliases.get(parent, parent))
        group = groups.get(key)
        if group is None:
            candidates = [g for g in active.values() if g.get("wrapper")
                          and (not g.get("turn") or not payload.get("turn_id")
                               or g["turn"] == payload["turn_id"])]
            if len(candidates) == 1:
                group = candidates[0]
        if group is None and iid in starts:
            start_ts, start_line = starts[iid]
            group = groups.setdefault(iid, {"id": iid, "ts": start_ts, "line": start_line,
                                           "prose": [], "tools": []})
        if group is None:
            unknown.append({"line": line, "ts": stamp, "reason": "unattributed_completion"})
            continue
        if not group.get("wrapper"):
            group["tools"] = []  # completion replaces the direct/UI intent
        actual = absolute(item.get("cwd"), cwd) or cwd
        if kind == "McpToolCall":
            args = arguments(item.get("arguments"))
            if args is None or not isinstance(item.get("tool"), str):
                unknown.append({"line": line, "ts": stamp, "reason": "unobservable_operation"})
                continue
            tool = action(item["tool"], args, actual, line, group["ts"], iid)
        elif kind == "FileChange":
            tool = action("apply_patch", {}, actual, line, group["ts"], iid)
            changes = item.get("changes")
            tool["files"] = sorted({p for raw in (changes if isinstance(changes, dict) else {})
                                    if (p := absolute(raw, actual))})
            tool["edit_files"] = tool["files"][:]
            tool["file_observable"] = isinstance(changes, dict)
        else:
            tool = action("Bash", {}, actual, line, group["ts"], iid)
            tool["files"] = completed_command(item, actual)
            for parsed in item.get("parsed_cmd") or []:
                if isinstance(parsed, dict) and parsed.get("type") == "read":
                    target = absolute(parsed.get("path"), actual)
                    if target and target not in tool["files"]:
                        tool["files"].append(target)
            tool["file_observable"] = bool(tool["files"])
        tool.update(status=completion_status(item), completed_ts=stamp,
                    request_line=group["line"], completion_only=bool(group.get("wrapper")))
        group["tools"].append(tool)
    for group in active.values():
        if group.get("wrapper") and not group["tools"]:
            unknown.append({"line": group["line"], "ts": group["ts"], "reason": "open_wrapper"})
    for entry in unknown:
        diagnostics[entry["reason"]] += 1
    status = ("resolved" if identities == {sid} else "ambiguous_identity" if identities else "identity_unknown")
    if fork and created is None:
        status = "unresolved_fork_prefix"
    return timeline(path, sid, "codex", status, groups, users, diagnostics, unknown)


def timeline(path, sid, harness, status, groups, users, diagnostics, unknown):
    return {"file": str(path), "session": sid, "harness": harness, "status": status,
            "groups": sorted(groups.values(), key=lambda g: g["ts"]),
            "users": sorted(users.values()), "diagnostics": dict(diagnostics), "unknown": unknown}


class TranscriptIndex:
    """Inventory by identity, never by project or newest mtime.

    Cache stamps include size and mtime, so an appended/resumed rollout is
    reread even when its creation date and session id have not changed.
    """

    def __init__(self, needed, claude_projects=None, codex_home=None):
        self.claude_root = Path(claude_projects or Path.home() / ".claude/projects").expanduser()
        self.codex_root = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
        self.sources, self.cache, self.diagnostics = defaultdict(list), {}, Counter()
        for harness, root, pattern in (("claude-code", self.claude_root, "*.jsonl"),
                                       ("codex", self.codex_root / "sessions", "rollout-*.jsonl")):
            try:
                for path in root.rglob(pattern):
                    if harness == "claude-code":
                        if path.stem in needed:
                            self.sources[path.stem].append((harness, path))
                    else:
                        for _, row in records(path, Counter()):
                            if row.get("type") == "session_meta":
                                payload = row.get("payload") or {}
                                for sid in {payload.get("id"), payload.get("session_id")} & needed:
                                    self.sources[sid].append((harness, path))
                                break
            except OSError:
                self.diagnostics["unreadable_inventory"] += 1

    def get(self, sid, explicit=None):
        if not isinstance(sid, str) or not sid or sid == "unknown":
            return {"status": "identity_unknown", "harness": "unknown"}
        sources = self.sources.get(sid, [])
        if explicit:
            sources = [("codex" if Path(explicit).name.startswith("rollout-") else "claude-code", Path(explicit))]
        if len(sources) != 1:
            return {"status": "missing_transcript" if not sources else "ambiguous_transcript", "harness": "unknown"}
        harness, path = sources[0]
        if harness == "claude-code" and path.stem != sid:
            return {"status": "ambiguous_identity", "harness": harness}
        try:
            stat = path.stat()
            stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            key = (path, sid)
            if key not in self.cache or self.cache[key][0] != stamp:
                parsed = (codex if harness == "codex" else claude)(path, sid)
                self.cache[key] = (stamp, parsed)
            return self.cache[key][1]
        except OSError:
            return {"status": "unreadable_transcript", "harness": harness}
