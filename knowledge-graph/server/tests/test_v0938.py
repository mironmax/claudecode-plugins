#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.38 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0938.py

Hook payloads reach the server as HTTP requests, so every path they carry is
untrusted input. This release contains each of them before it touches the
filesystem, and replaces the two regexes that ran over the raw prompt with
scanners that are linear on any input.

Covers:
  1. A transcript path is accepted only under the user's home and only .jsonl
  2. A tool event whose cwd lies outside home is ignored, not written
  3. The image-placeholder and quoted-path scanners match the old behaviour
  4. Both scanners stay fast on adversarial input (unclosed delimiters)
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from mcp_http.ambient import (  # noqa: E402
    _prompt_text,
    _strip_image_placeholders,
    _swallow_quoted_paths,
    handle_tool_event,
)
from mcp_http.session_manager import (  # noqa: E402
    recover_kg_sid_from_transcript,
    safe_transcript_path,
)

_PASS = _FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


def main():
    home = Path.home()
    inside = Path(tempfile.mkdtemp(prefix="kg-test-tr-", dir=str(home / ".cache")))
    outside = Path(tempfile.mkdtemp(prefix="kg-test-tr-"))   # system temp, not home

    # --- 1. transcript path containment ---------------------------------------
    print("transcript path:")
    good = inside / "t.jsonl"
    good.write_text('{"x":"Session: cafe0123"}\n')
    check("under home and .jsonl is accepted", safe_transcript_path(str(good)) == str(good.resolve()))
    check("recovery works through it", recover_kg_sid_from_transcript(str(good)) == "cafe0123")
    bad_out = outside / "t.jsonl"
    bad_out.write_text('{"x":"Session: cafe0123"}\n')
    check("outside home is refused", safe_transcript_path(str(bad_out)) is None)
    check("...and recovery returns None rather than reading it",
          recover_kg_sid_from_transcript(str(bad_out)) is None)
    not_jsonl = inside / "notes.txt"
    not_jsonl.write_text("Session: cafe0123\n")
    check("wrong suffix is refused", safe_transcript_path(str(not_jsonl)) is None)
    trav = str(inside / ".." / ".." / ".." / ".." / ".." / "etc" / "passwd")
    check("traversal out of home is refused", safe_transcript_path(trav) is None)

    # --- 2. tool event cwd containment ----------------------------------------
    print("tool event cwd:")
    r = handle_tool_event(None, None, {
        "cwd": str(outside), "tool_name": "WebFetch", "session_id": "cs",
        "tool_input": {"url": "https://example.invalid/x"}})
    check("cwd outside home -> None", r is None, r)
    check("...and no events file was created anywhere under the store",
          not list(Path(_TMP_STORAGE).rglob("tool_events.json")))

    # --- 3. scanners match the old regex behaviour ----------------------------
    print("scanners:")
    check("image placeholder removed",
          _strip_image_placeholders("look [Image: source: /a/b.png] here") == "look   here")
    check("unclosed placeholder left alone",
          _strip_image_placeholders("look [Image: source: /a/b.png") == "look [Image: source: /a/b.png")
    check("two placeholders", _strip_image_placeholders("[Image: a] x [Image: b]") == "  x  ")
    kept = []
    out = _swallow_quoted_paths("open '/home/u/my dir/notes.pdf' and \"/x/y/\" now", kept)
    check("quoted paths swallowed, basenames kept",
          out == "open   and   now" and kept == ["notes.pdf", "y"], (out, kept))
    kept = []
    out = _swallow_quoted_paths("say 'hello world' and \"no slash\"", kept)
    check("quotes without a slash are untouched",
          out == "say 'hello world' and \"no slash\"" and kept == [], (out, kept))
    kept = []
    out = _swallow_quoted_paths("it's a '/tmp/x' case", kept)
    check("an apostrophe pairs with the next quote as the regex did",
          out == "it's a   case" and kept == ["x"], (out, kept))
    check("end-to-end: a bare path prompt stays silent",
          _prompt_text("'/home/u/Downloads/a b.pdf'") is None)
    check("end-to-end: words survive alongside a path",
          _prompt_text("summarize '/home/u/Downloads/a b.pdf' please") is not None)

    # --- 4. adversarial input ---------------------------------------------------
    print("adversarial:")
    n = 200_000
    t0 = time.time()
    _strip_image_placeholders("[Image:" * (n // 7))
    _swallow_quoted_paths("'/" * (n // 2), [])
    _swallow_quoted_paths("'" * n, [])
    dt = time.time() - t0
    check(f"500K chars of unclosed delimiters scan in {dt:.2f}s (linear; regexes were quadratic)", dt < 4.0, dt)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
