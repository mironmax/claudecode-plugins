"""Which coding harness an event came from, and the little that differs.

The server makes every decision. What varies by harness is how its events
arrive and how much a hook may carry, so this module is the one place that
knows harness names; everything else asks it (docs/harnesses/proposal.md).

Detection is structural, never a guess from payload fields: Codex mirrors
Claude Code's hook fields on purpose, so the same keys arrive from both.
  - Hook payloads carry transcript_path. Codex keeps its transcripts as
    $CODEX_HOME/sessions/.../rollout-*.jsonl; Claude Code under ~/.claude.
  - MCP calls carry the client's User-Agent; Codex's MCP client sends
    codex-mcp-client/<version>.
"""

import os
from dataclasses import dataclass

from core.constants import BOOTSTRAP_CHAR_BUDGET, CODEX_BOOTSTRAP_CHAR_BUDGET

CLAUDE_CODE = "claude-code"
CODEX = "codex"


@dataclass(frozen=True)
class Profile:
    name: str
    # Hook context ceiling for the session-start preload.
    preload_chars: int
    # Reads arrive only through the shell (no Read tool), so shell reads
    # stand in for reads when counting what is worth a capture nudge.
    shell_reads_count: bool
    # Said once, on a fresh kg_read, when no hook has ever reached the server
    # from this harness in this project: the tools work but the hooks do not.
    no_hooks_hint: str | None


PROFILES = {
    CLAUDE_CODE: Profile(CLAUDE_CODE, BOOTSTRAP_CHAR_BUDGET, False, None),
    CODEX: Profile(
        CODEX, CODEX_BOOTSTRAP_CHAR_BUDGET, True,
        "No memory preload reached this Codex session: the knowledge-graph "
        "plugin's hooks are not running. Codex keeps a plugin's hooks off until "
        "the user approves them — ask the user to run /hooks, trust the "
        "knowledge-graph hooks, and start a new session. Until then there is no "
        "preload, per-prompt recall or file recall; the kg_* tools work as usual.",
    ),
}


def from_transcript(transcript_path: str | None) -> str:
    """Harness of a hook event, from the transcript path it carries."""
    if transcript_path:
        name = os.path.basename(transcript_path)
        if name.startswith("rollout-") or f"{os.sep}.codex{os.sep}" in transcript_path:
            return CODEX
    return CLAUDE_CODE


def from_user_agent(user_agent: str | None) -> str:
    """Harness of an MCP call, from the client's User-Agent header."""
    if user_agent and user_agent.lower().startswith("codex"):
        return CODEX
    return CLAUDE_CODE


def profile(name: str | None) -> Profile:
    return PROFILES.get(name or CLAUDE_CODE, PROFILES[CLAUDE_CODE])
