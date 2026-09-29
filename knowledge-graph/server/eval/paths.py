"""Literal file requests in historical transcripts; no filesystem probes.

Reuse the production shell option grammar, but not its isfile/realpath gate:
a file that was read then may have been deleted or moved since. Paths are
lexically normalized, not resolved through today's symlinks. Shell source
and patches are data here; neither is ever executed.
"""

import os
import shlex
from urllib.parse import unquote, urlsplit

from core.file_requests import (_READ_COMMANDS, _SEPARATORS, _UNSAFE_CHARS,
                                 _lex, _operands, patch_files)


def absolute(value, cwd=""):
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    if value.startswith("file:"):
        uri = urlsplit(value)
        if uri.netloc not in ("", "localhost") or uri.query or uri.fragment:
            return None
        value = unquote(uri.path)
    value = os.path.expanduser(value)
    if not os.path.isabs(value):
        if not cwd or not os.path.isabs(cwd):
            return None
        value = os.path.join(cwd, value)
    return os.path.normpath(value)


def touch_path(value, project, level):
    if not isinstance(value, str) or not value.strip() or "://" in value:
        return None
    value = value.split()[0].split("#", 1)[0].rstrip("/")
    while ":" in value:
        head, _, tail = value.rpartition(":")
        if "/" in tail or not head:
            break
        value = head
    return absolute(value, project if level == "project" else "")


def shell_reads(command, cwd):
    """Conservative literal read operands, including files now absent."""
    if not isinstance(command, str) or "<<" in command:
        return []
    tokens = _lex(command)
    if not tokens:
        return []
    segments, current = [], []
    for token in tokens:
        if token in _SEPARATORS:
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    found, moved = set(), False
    for segment in segments:
        words, inputs, i = [], [], 0
        while i < len(segment):
            token = segment[i]
            if token and set(token) <= set("<>&"):
                if words and words[-1].isdigit():
                    words.pop()
                if i + 1 < len(segment):
                    if token == "<":
                        inputs.append(segment[i + 1])
                    i += 1
            else:
                words.append(token)
            i += 1
        while words and "=" in words[0] and not words[0].startswith(("-", "/", ".")):
            words.pop(0)
        if not words:
            continue
        cmd = os.path.basename(words[0])
        if cmd in ("cd", "pushd", "popd"):
            moved = True
            continue
        if cmd not in _READ_COMMANDS:
            continue
        operands = _operands(cmd, words[1:])
        if operands is None:
            continue
        for operand in operands + inputs:
            if not operand or operand == "-" or _UNSAFE_CHARS & set(operand):
                continue
            target = absolute(operand, "" if moved else cwd)
            if target:
                found.add(target)
    return sorted(found)


def requested_files(name, args, cwd):
    """(read/edit paths, edit paths). Tool names may carry MCP namespaces."""
    name = name.split("__")[-1].split(".")[-1]
    field = {"Read": "file_path", "Edit": "file_path", "Write": "file_path",
             "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}.get(name)
    if field:
        target = absolute(args.get(field), cwd)
        files = [target] if target else []
        return files, files if name != "Read" else []
    if name in ("Bash", "exec_command", "shell_command"):
        workdir = absolute(args.get("workdir"), cwd) or cwd
        return shell_reads(args.get("command", args.get("cmd")), workdir), []
    if name == "apply_patch":
        patch = args.get("input", args.get("command"))
        files = sorted({p for raw in patch_files(patch, cwd) if (p := absolute(raw, cwd))})
        return files, files
    return [], []


def completed_command(item, cwd):
    command = item.get("command")
    if isinstance(command, list) and all(isinstance(x, str) for x in command):
        command = (command[-1] if len(command) >= 3 and command[-2] in ("-c", "-lc")
                   else shlex.join(command))
    return shell_reads(command, cwd)
