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
ANTIGRAVITY = "antigravity-cli"


@dataclass(frozen=True)
class Profile:
    name: str
    # Hook context ceiling for the session-start preload.
    preload_chars: int
    # Reads arrive only through the shell (no Read tool), so shell reads
    # stand in for reads when counting what is worth a capture nudge.
    shell_reads_count: bool
    # Whether the Bash hook's cwd itself says where the command ran. Codex
    # reports the session directory; shell_context can recover actual cwd
    # from an exact-id completed rollout item, otherwise it remains unknown.
    shell_cwd_known: bool
    # Said once, on a fresh kg_read, when no hook has ever reached the server
    # from this harness in this project: the tools work but the hooks do not.
    no_hooks_hint: str | None


PROFILES = {
    CLAUDE_CODE: Profile(CLAUDE_CODE, BOOTSTRAP_CHAR_BUDGET, False, True, None),
    CODEX: Profile(
        CODEX, CODEX_BOOTSTRAP_CHAR_BUDGET, True, False,
        "No Codex memory hooks have been observed for this project. "
        "The plugin's hooks may need trust: Codex keeps them off until "
        "the user approves them — ask the user to run /hooks, trust the "
        "knowledge-graph hooks, and start a new session. With hooks off there is no "
        "preload, per-prompt recall or file recall; the kg_* tools work as usual.",
    ),
    ANTIGRAVITY: Profile(
        ANTIGRAVITY, BOOTSTRAP_CHAR_BUDGET, False, False,
        "No Antigravity memory hook has been observed for this conversation. "
        "Install the native knowledge-graph plugin, check agy -p /hooks and "
        "start a new conversation. Large KG replies require PreInvocation; "
        "see the plugin's Antigravity setup guide.",
    ),
}


def from_transcript(transcript_path: str | None) -> str:
    """Harness of a hook event, from the transcript path it carries."""
    if transcript_path:
        if f"{os.sep}.gemini{os.sep}antigravity-cli{os.sep}" in transcript_path:
            return ANTIGRAVITY
        name = os.path.basename(transcript_path)
        if name.startswith("rollout-") or f"{os.sep}.codex{os.sep}" in transcript_path:
            return CODEX
    return CLAUDE_CODE


def from_user_agent(user_agent: str | None) -> str:
    """Harness of an MCP call, from the client's User-Agent header."""
    if user_agent and user_agent.lower().startswith("codex"):
        return CODEX
    return CLAUDE_CODE


def antigravity_conversation(meta) -> str | None:
    """Every CLI MCP call carries this id; its Go User-Agent is ambiguous."""
    if hasattr(meta, "model_dump"):
        meta = meta.model_dump(by_alias=True)
    value = meta.get("antigravity.google/conversation_id") if isinstance(meta, dict) else None
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def hook_output(name: str, text: str) -> dict:
    if not text:
        return {}
    if name == ANTIGRAVITY:
        return {"injectSteps": [{"systemMessage": {"systemMessage": text}}]}
    raise ValueError(f"No native hook envelope for {name}")


def profile(name: str | None) -> Profile:
    return PROFILES.get(name or CLAUDE_CODE, PROFILES[CLAUDE_CODE])
