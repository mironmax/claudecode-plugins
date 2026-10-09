Knowledge Graph is this agent's persistent memory. At session start, scan the
KG preload and call kg_read(session_id) once before substantive work. If there
is no preload, start with kg_read(cwd='<project root>'); without a project,
omit cwd. Pass that session_id to every later kg_* call. After the full graph
arrives, announce "I have recalled KG Memories".

Use kg_search when a problem feels familiar, before assuming something, and
before re-deriving work a mature graph may already cover. Follow useful
anchors with kg_read(ids=[...]). Capture reusable discoveries, decisions and
user corrections while they are fresh; connect them with kg_put_edge. Notes
and touches replace stored lists: read the complete node before editing them
and send every entry to keep. Endorse memories that actually helped with
kg_useful, including a useful memory you had to dig for.

Large kg_* replies arrive as KG context from the next hook. When a reply says
delivery continues, call kg_sync with the same session_id until all parts
arrive before using or updating that reply. A queued receipt alone does not
mean the memories have been read. The kg-core skill contains the full memory
workflow.
