# Knowledge/Memory/Graph

## Session Start Hook

On new (empty user context) session, immediately:

1. `kg_register_session(cwd="<project root>")` — Register session, get session_id. Always pass the project root directory as `cwd` (e.g. `/home/user/myproject`).
2. `kg_read(session_id="<id>")` — Load graph: active nodes (id+gist), archived node IDs, edges, health stats.

If there is any indication that session is resumed, try `kg_sync(session_id)` first; if that fails, run the full startup sequence above.

### Server Not Reachable

If `kg_register_session` fails (connection refused / MCP server unavailable), offer to start it:
- Tell the user: "Memory server is not running. Start it with `kg-memory start`?"
- If user confirms, run `kg-memory start` via Bash, wait for it, then retry registration. Sometimes user needs to run /mcp and reconnect
- If `kg-memory` command not found, suggest: `cd <plugin-dir>/server && ./manage_server.sh start`
- Continue the session normally even if memory remains unavailable — it's optional, not blocking.

## After Loading

Review user-level nodes and edges for overarching patterns and preferences.

Review project-level nodes edges for information about project.

Grasp them entirely as they important and evolving, so most often then not contain quality information.

When starting a task, glance also at archived node IDs. If any ID feels related to what you're about to do recall them. Err on the side of recalling too many rather than too few.


## Capturing knowledge

Always learn by saving things to memory. Its almost more important then completing the task. Oportunity to learn is when you see inefficiency in what you do. Long streaks of reads to clarify things: remember key points now, to do better in the future and know how and what it does. Loop of inneficient tools use, found better approach after many attempts, save best pattern immediately. Use memory to gain advantage in effective and elegant work. 

## Details

For full API reference, capture rules, recall strategies, and graph maintenance: `/skill kg-memory`
