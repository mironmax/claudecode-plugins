"""Experimental CLI adapter: conversation identity and persistent hook context.

No maintenance runner is selected here. Claude Code/Codex keep their existing
REST routes, hooks and tool replies. A native agy hook only carries JSON;
normalization, retrieval and delivery decisions belong to this server.
"""

import hashlib
import json
import os
import random
import stat
import time

from mcp.types import CallToolResult, TextContent

from . import harness
from .delivery import DeferredView, INLINE_BYTES
from .session_manager import safe_transcript_path

PROMPT_BYTES = 256 * 1024
TOOLS = {
    "view_file": ("Read", {"file_path": "AbsolutePath"}),
    "write_to_file": ("Write", {"file_path": "TargetFile"}),
    "replace_file_content": ("Edit", {"file_path": "TargetFile"}),
    "multi_replace_file_content": ("MultiEdit", {"file_path": "TargetFile"}),
    "run_command": ("Bash", {"command": "CommandLine", "workdir": "Cwd"}),
    "read_url_content": ("WebFetch", {"url": "Url"}),
    "search_web": ("WebSearch", {"query": "query"}),
}


def _result(text, error=False):
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=error)


async def call_with_delivery(store, manager, call_tool, name, arguments, conversation_id):
    """Bind by MCP metadata, then route every tool's output through one path."""
    hit = manager.find_by_claude_sid(conversation_id)
    sid_arg = arguments.get("session_id")
    first = hit is None
    if hit is None:
        if sid_arg or name != "kg_read":
            return _result("This Antigravity conversation has no KG session. "
                           "Call kg_read(cwd='<project root>') without session_id first.", True)
        try:
            reg = manager.register(arguments.get("cwd"), claude_sid=conversation_id,
                                   harness=harness.ANTIGRAVITY)
        except ValueError as error:
            return _result(str(error), True)
        hit = reg["session_id"], manager.lookup(reg["session_id"])
    sid, data = hit
    if data.get("harness") != harness.ANTIGRAVITY or (sid_arg and sid_arg != sid):
        return _result("session_id belongs to another conversation. "
                       f"Use session_id='{sid}' for this Antigravity conversation.", True)
    arguments = dict(arguments, session_id=sid)
    view = DeferredView(manager)
    content = await call_tool(name, arguments, harness.ANTIGRAVITY, view=view)
    text = "\n\n".join(block.text for block in content)
    if first and not data.get("agy_hooks_seen"):
        text += "\n\n" + harness.profile(harness.ANTIGRAVITY).no_hooks_hint
    if len(text.encode("utf-8")) <= INLINE_BYTES:
        view.commit(store)
        return _result(text)
    if not data.get("agy_hooks_seen"):
        return _result(
            f"{name} produced a reply too large for Antigravity's MCP output. "
            "It has not been marked as read or synced. "
            + harness.profile(harness.ANTIGRAVITY).no_hooks_hint, True)
    if not manager.queue_context(sid, text, name, view.effects):
        return _result("KG delivery queue is full; this reply has not been marked as "
                       "read or synced. Call kg_sync to drain the pending context, "
                       "then retry with fewer node ids if needed.", True)
    return _result(f"{name} reply queued for this conversation (Session: {sid}). "
                   "The next PreInvocation hook delivers it as KG context. If it "
                   "has multiple parts, follow its continuation instruction before "
                   "using or updating the memories.")


def latest_prompt(transcript_path):
    """Bounded tail of the actual human record; never injected USER_MESSAGE."""
    if not isinstance(transcript_path, str):
        return "", None, 0
    resolved = safe_transcript_path(transcript_path)
    if not resolved:
        return "", None, 0
    try:
        fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return "", None, 0
            start = max(0, info.st_size - PROMPT_BYTES)
            stream.seek(start)
            tail = stream.read(PROMPT_BYTES)
        if start:
            tail = tail.partition(b"\n")[2]
        tail = tail[:tail.rfind(b"\n") + 1]
        for line in reversed(tail.splitlines()):
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if not isinstance(row, dict) or row.get("type") != "USER_INPUT":
                continue
            content = row.get("content")
            if not isinstance(content, str):
                continue
            begin = content.find("<USER_REQUEST>")
            end = content.find("</USER_REQUEST>", begin + 14)
            if begin < 0 or end < 0:
                return "", None, info.st_size
            prompt = content[begin + len("<USER_REQUEST>"):end].strip()
            key = f"{resolved}:{row.get('step_index')}:{row.get('created_at')}:" + hashlib.sha256(
                prompt.encode("utf-8")).hexdigest()[:16]
            return prompt, key, info.st_size
    except (OSError, ValueError):
        pass
    return "", None, 0


def normalize_event(payload, cwd):
    event = {"cwd": cwd, "session_id": payload.get("conversationId"),
             "transcript_path": payload.get("transcriptPath"), "harness": harness.ANTIGRAVITY}
    call = payload.get("toolCall") or {}
    if not isinstance(call, dict) or call.get("name") not in TOOLS:
        return event
    args = call.get("args") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    tool, fields = TOOLS[call["name"]]
    event.update(tool_name=tool, tool_input={dst: args.get(src) for dst, src in fields.items()})
    # An absent Cwd must not turn a run_command into a read in the workspace.
    if tool == "Bash" and not event["tool_input"].get("workdir"):
        event["tool_input"]["workdir"] = ""
    return event


def _pulse(size):
    """Same attention duties and depth thresholds as the original hook pools."""
    if size < 200000:
        pool = ("About to search files or web? Check KG first — kg_search may already have the answer.",
                "About to make an assumption? kg_search first. If missing, state it and capture it.")
    elif size > 2500000:
        pool = ("Any node gist gone stale or vague after using it? Sharpen it while context is still live.",
                "Any edges missing between nodes you've used today? One edge makes both nodes far more durable.")
    else:
        pool = ("KG capture pulse: did the last exchange reveal anything worth keeping? Write it before moving on.",
                "Did user express a preference, style, or constraint? Capture it as a user-level node now.")
    return random.choice(pool)


def handle_event(store, manager, event_name, payload):
    """Return internal delivery metadata and the exact native hook envelope."""
    if event_name not in ("SessionStart", "PreInvocation", "PostToolUse"):
        return {"output": {}}
    cid = payload.get("conversationId")
    if not isinstance(cid, str) or not cid or len(cid) > 128:
        return {"output": {}}
    transcript = payload.get("transcriptPath")
    # Shared plugin config can be loaded by IDE/desktop too: v1 is CLI only.
    if transcript and harness.from_transcript(transcript) != harness.ANTIGRAVITY:
        return {"output": {}}
    roots = payload.get("workspacePaths") or []
    cwd = roots[0] if isinstance(roots, list) and roots and isinstance(roots[0], str) else None
    if cwd and not os.path.isabs(cwd):
        return {"output": {}}
    hit = manager.find_by_claude_sid(cid)
    if hit is None:
        reg = manager.register(cwd, claude_sid=cid, harness=harness.ANTIGRAVITY)
        hit = reg["session_id"], manager.lookup(reg["session_id"])
    sid, data = hit
    if data.get("harness") != harness.ANTIGRAVITY:
        return {"output": {}}
    if cwd and not data.get("project_path"):
        manager.attach_project(sid, cwd)
    cwd = data.get("project_path") or cwd
    manager.note_antigravity_hook(sid)

    if event_name != "PostToolUse" and "preloaded_ids" not in data and not manager.has_pending_context(sid, "preload"):
        from .read_format import build_bootstrap
        at = time.time()
        graphs = store.read_graphs(sid)
        scores = store.scores_for_read(sid)
        result = build_bootstrap(graphs, scores, sid, debt=store.maintenance_debt(sid),
                                 budget=harness.profile(harness.ANTIGRAVITY).preload_chars)
        view = DeferredView(manager)
        view.set_preloaded(sid, result["shown_ids"])
        view.mark_seen(sid, result["shown_ids"], via="preload", at=at)
        manager.queue_context(sid, result["context"], "preload", view.effects)

    if event_name == "PostToolUse":
        if not payload.get("error"):
            from .ambient import handle_tool_event
            view = DeferredView(manager)
            text = handle_tool_event(store, view, normalize_event(payload, cwd))
            if text:
                manager.queue_context(sid, text, "file recall", view.effects)
        return {"output": {}}  # This event cannot inject.

    if event_name == "PreInvocation" and payload.get("invocationNum") == 0:
        prompt, key, size = latest_prompt(transcript)
        fresh = manager.note_antigravity_hook(sid, key)
        # Drain earlier replies even when a denied tool ended the last turn.
        # Do not add a full-read nudge while the read itself is in flight.
        if fresh and not manager.has_pending_context(sid):
            from .ambient import _prompt_text, build_prompt_recall
            view = DeferredView(manager)
            text = build_prompt_recall(store, view, cwd or "", prompt,
                                       claude_sid=cid, transcript_path=transcript)
            if not text and _prompt_text(prompt):
                text = _pulse(size)
            if text:
                manager.queue_context(sid, text, "prompt recall", view.effects)
    packet = manager.prepare_context(sid)
    if not packet:
        return {"output": {}}
    return {"output": harness.hook_output(harness.ANTIGRAVITY, packet["text"]),
            "delivery_id": packet["id"]}
