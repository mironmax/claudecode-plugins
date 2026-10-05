# Antigravity CLI implementation

The first iteration is implemented on `codex/antigravity-cli`, on top of
PR #39. The [setup and development guide](../../knowledge-graph/ANTIGRAVITY.md)
records its scope, read-path contract and remaining release gates.

Follow-up probes on `agy 1.2.15` found the valid per-tool eager switch:
`tools: {kg_read: {eager: true}, ...}`. It exposes descriptions and delivers a
10 KB MCP reply whole, but 40 KB/60 KB replies still truncate. No supported
tool-output size/token setting was found in `/config` or official docs.
`PreInvocation` delivered 40 KB intact, so v1 uses the proposed hook handoff.

The native [KG smoke](tools/antigravity-probe/kg_smoke.py) runs an installed
plugin with a real CLI binary, a local mock Gemini model and scratch graphs.
It checks full-graph delivery, a large Unicode node across multiple chunks,
continuation, file recall and committed session state. The
[HTTP boundary tests](../../knowledge-graph/server/tests/test_antigravity.py)
cover identity, deferred bookkeeping, stale writes, restarts and queue bounds.
Real-model use, compaction/fork/clear, quota and other Antigravity surfaces
remain unvalidated.
