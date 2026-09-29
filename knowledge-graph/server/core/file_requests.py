"""Literal file-request grammar shared by hooks and offline evaluation.

Only the hook's bash_read_files probes file existence. The lexical helpers
have no live-store or HTTP-package dependencies.
"""

import os
import shlex

from .constants import FILE_RECALL_MAX_BASH_FILES

_PATH_TOOLS = {"Read": "file_path", "Edit": "file_path", "Write": "file_path",
               "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}

_READ_COMMANDS = frozenset({"cat", "head", "tail", "less", "sed", "grep", "jq", "nl", "rg"})
_SEPARATORS = frozenset({"|", "||", "&", "&&", ";", "\n", "(", ")", "|&", ";;"})
# Short options that consume the next word, per command.
_ARG_OPTS = {
    "head": set("nc"),
    "tail": set("ncs"),
    "sed": set("ef"),
    "grep": set("efmABCdD"),
    "jq": set("f"),
    "cat": set(), "less": set(),
}
# jq long options and how many words each consumes.
_JQ_LONG_ARGS = {"--arg": 2, "--argjson": 2, "--slurpfile": 2, "--rawfile": 2,
                 "--indent": 1, "--from-file": 1, "--tab": 0}
_UNSAFE_CHARS = set("$`*?[]{}")

# New commands use an explicit option grammar: an unknown option may consume
# the next word, so treating that word as a filename would invent a read.
_RG_ARG_LONG = {"--regexp", "--file", "--glob", "--iglob", "--type", "--type-not",
                "--context", "--before-context", "--after-context", "--max-count",
                "--max-columns", "--max-depth", "--max-filesize", "--encoding",
                "--engine", "--threads", "--sort", "--sortr", "--color"}
_RG_FLAG_LONG = {"--line-number", "--no-line-number", "--with-filename", "--no-filename",
                 "--ignore-case", "--case-sensitive", "--smart-case", "--fixed-strings",
                 "--word-regexp", "--line-regexp", "--invert-match", "--only-matching",
                 "--count", "--count-matches", "--files-with-matches", "--files-without-match",
                 "--quiet", "--hidden", "--no-ignore", "--no-ignore-vcs", "--no-heading",
                 "--heading", "--pcre2", "--multiline", "--multiline-dotall", "--text",
                 "--json", "--no-messages", "--stats", "--no-config"}
_NL_ARG_LONG = {"--body-numbering", "--header-numbering", "--footer-numbering",
                "--section-delimiter", "--line-increment", "--join-blank-lines",
                "--number-format", "--number-separator", "--starting-line-number",
                "--number-width"}


def _explicit_operands(cmd, words):
    """nl/rg reads with known options and explicit file operands only."""
    long_args = _RG_ARG_LONG if cmd == "rg" else _NL_ARG_LONG
    long_flags = _RG_FLAG_LONG if cmd == "rg" else {"--no-renumber"}
    short_args = set("efgtTABCmMEj") if cmd == "rg" else set("bdfhilnsvw")
    short_flags = set("nNHilsSIFwxcqoaUPuL0") if cmd == "rg" else {"p"}
    operands, pattern_given, opts_done, i = [], False, False, 0
    while i < len(words):
        word = words[i]
        i += 1
        if opts_done or not word.startswith("-") or word == "-":
            operands.append(word)
            continue
        if word == "--":
            opts_done = True
            continue
        if word.startswith("--"):
            name, sep, _ = word.partition("=")
            if name in long_args:
                pattern_given |= cmd == "rg" and name in ("--regexp", "--file")
                if not sep:
                    if i == len(words):
                        return None
                    i += 1
            elif name not in long_flags or sep:
                return None
            continue
        flags = word[1:]
        for k, char in enumerate(flags):
            if char in short_args:
                pattern_given |= cmd == "rg" and char in "ef"
                if k == len(flags) - 1:
                    if i == len(words):
                        return None
                    i += 1
                break
            if char not in short_flags:
                return None
    return operands[1:] if cmd == "rg" and not pattern_given else operands


def _lex(command: str) -> list[str] | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None                    # unbalanced quotes


def _operands(cmd: str, words: list[str]) -> list[str] | None:
    """The file operands of one read command, or None when it is not a read
    (sed without -n, sed -i, jq --args)."""
    if cmd in ("nl", "rg"):
        return _explicit_operands(cmd, words)
    arg_opts = _ARG_OPTS[cmd]
    operands: list[str] = []
    script_given = False               # sed -e/-f, grep -e/-f, jq -f
    quiet = False                      # sed -n
    i, opts_done = 0, False
    while i < len(words):
        w = words[i]
        i += 1
        if opts_done or not w.startswith("-") or w == "-":
            operands.append(w)
            continue
        if w == "--":
            opts_done = True
            continue
        if w.startswith("--"):
            name = w.split("=", 1)[0]
            if cmd == "jq":
                if name in ("--args", "--jsonargs"):
                    return None
                if name == "--from-file":
                    script_given = True
                if "=" not in w:
                    i += _JQ_LONG_ARGS.get(name, 0)
            elif cmd == "sed" and name in ("--in-place",):
                return None
            elif cmd == "sed" and name in ("--quiet", "--silent"):
                quiet = True
            elif cmd in ("sed", "grep") and name in ("--expression", "--file", "--regexp"):
                script_given = True
                if "=" not in w:
                    i += 1
            continue
        if cmd in ("head", "tail") and w[1:].isdigit():
            continue                   # head -20
        flags = w[1:]
        for k, ch in enumerate(flags):
            if cmd == "sed" and ch == "i":
                return None
            if cmd == "sed" and ch == "n":
                quiet = True
            if ch in arg_opts:
                if (cmd in ("sed", "grep") and ch in "ef") or (cmd == "jq" and ch == "f"):
                    script_given = True
                if k == len(flags) - 1:
                    i += 1             # the argument is the next word
                break                  # else it is attached: -n5, -e'p'
    if cmd == "sed" and not quiet:
        return None
    if cmd in ("sed", "grep", "jq") and not script_given:
        operands = operands[1:]        # the script / pattern / filter
    return operands


def bash_read_files(command: str, cwd: str | None, *, diagnostics: dict | None = None) -> list[str]:
    """Absolute paths of existing files a Bash command plainly reads.

    Conservative by construction: only cat/head/tail/less/sed -n/grep/jq/nl/rg at
    the head of a pipeline segment, only operands without shell expansion,
    relative operands only while no `cd` has run, and only paths that exist
    as regular files. Missing a file is fine; inventing one is not.
    """
    if not isinstance(command, str) or "<<" in command:
        return []
    tokens = _lex(command)
    if not tokens:
        return []
    segments, cur = [], []
    for tok in tokens:
        if tok in _SEPARATORS:
            segments.append(cur)
            cur = []
        else:
            cur.append(tok)
    segments.append(cur)

    found: list[str] = []
    unresolved = False
    moved = False                      # a cd ran: relative operands are ambiguous
    for seg in segments:
        words, redirect_in = [], []
        k = 0
        while k < len(seg):
            tok = seg[k]
            if tok and set(tok) <= set("<>&"):
                # A redirect: its fd number is already in words, its target next.
                if words and words[-1].isdigit():
                    words.pop()
                if k + 1 < len(seg):
                    if tok == "<":
                        redirect_in.append(seg[k + 1])
                    k += 1
            else:
                words.append(tok)
            k += 1
        while words and "=" in words[0] and not words[0].startswith(("-", "/", ".")):
            words.pop(0)               # FOO=bar cmd
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
        for op in operands + redirect_in:
            if op == "-" or not op or _UNSAFE_CHARS & set(op):
                continue
            if op.startswith("~"):
                op = os.path.expanduser(op)
            if not os.path.isabs(op):
                if moved or cwd is None:
                    unresolved = True
                    continue
                op = os.path.join(cwd, op)
            path = os.path.realpath(op)
            if os.path.isfile(path) and path not in found:
                found.append(path)
            if len(found) >= FILE_RECALL_MAX_BASH_FILES:
                return found
    if diagnostics is not None and unresolved:
        diagnostics["outcome"] = "unresolved_cwd"
    return found


def file_targets(tool: str, tool_input: dict, cwd: str, *, shell_cwd_known: bool = True,
                 diagnostics: dict | None = None) -> list[str]:
    """Absolute paths the tool call touched; empty for anything untracked."""
    field = _PATH_TOOLS.get(tool)
    if field:
        target = tool_input.get(field)
        if not isinstance(target, str) or not target:
            return []
        target = os.path.expanduser(target)
        return [target if os.path.isabs(target) else os.path.join(cwd, target)]
    if tool == "Bash":
        workdir = tool_input.get("workdir")
        if isinstance(workdir, str) and os.path.isabs(workdir):
            return bash_read_files(tool_input.get("command"), workdir, diagnostics=diagnostics)
        return bash_read_files(tool_input.get("command"), cwd if shell_cwd_known else None,
                               diagnostics=diagnostics)
    if tool == "apply_patch":
        return patch_files(tool_input.get("command") or tool_input.get("input"), cwd)
    return []


# Codex edits files through apply_patch; the payload carries the patch text,
# and each file it touches is named on one header line.
_PATCH_HEADERS = ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: ")


def patch_files(patch, cwd: str) -> list[str]:
    """Absolute paths an apply_patch call adds, updates, deletes or moves to."""
    if not isinstance(patch, str):
        return []
    found = []
    for line in patch.splitlines():
        for header in _PATCH_HEADERS:
            if line.startswith(header):
                target = os.path.expanduser(line[len(header):].strip())
                if target:
                    path = target if os.path.isabs(target) else os.path.join(cwd, target)
                    if path not in found:
                        found.append(path)
    return found
