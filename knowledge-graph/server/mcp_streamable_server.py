#!/usr/bin/env python3
"""
MCP Streamable HTTP Server for Knowledge Graph
Uses Streamable HTTP transport (replaces deprecated SSE).
"""

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import Tool, TextContent
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.websockets import WebSocketClose

# Add server directory to path
sys.path.insert(0, str(Path(__file__).parent))

from version import __version__
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.websocket import ConnectionManager
from mcp_http.rest import create_rest_api
from mcp_http.read_format import build_full_read, format_node_full, format_search
from mcp_http.security import host_allowed
from core.autocommit import AutoCommitter
from core.exceptions import (
    KGError,
    NodeNotFoundError,
    SessionNotFoundError,
)

# Configure logging
log_level = os.getenv("KG_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Global state
store: MultiProjectGraphStore | None = None
session_manager: HTTPSessionManager | None = None
connection_manager: ConnectionManager | None = None
mcp_server: Server | None = None


def create_mcp_server() -> Server:
    """Create and configure MCP server with all tools."""
    server = Server("knowledge-graph-mcp")

    # ========================================================================
    # Tool Definitions
    # ========================================================================

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List all available tools."""
        return [
            Tool(
                name="kg_read",
                description="Read the knowledge graph. First call: pass cwd to initialize the session — the result includes session_id; pass that session_id on every later call (cwd then optional). Without id/ids: full graph — active nodes (gist), archived anchors (id only), live edges — always fits inline. With id or ids: full node content (gist + notes + touches + the node's edges); archived nodes get promoted to active. Reading several related nodes via ids in ONE call is cheaper than sequential single reads.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "cwd": {
                            "type": "string",
                            "description": "Project root directory. Required on the FIRST call (initializes session, loads project graph). Optional afterwards when session_id is passed."
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session ID from the first kg_read. Pass on every subsequent call — reuses the session instead of registering a new one per read."
                        },
                        "id": {
                            "type": "string",
                            "description": "Node ID to read in full. Returns gist + notes + touches + edges. Promotes archived nodes to active."
                        },
                        "ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Several node IDs to read in full in one call (batch crumb-following). Prefer over sequential single-id reads."
                        },
                        "level": {
                            "type": "string",
                            "enum": ["user", "project"],
                            "description": "Hint which graph the node is in. If omitted, searches both."
                        }
                    },
                    "required": []
                }
            ),
            Tool(
                name="kg_search",
                description="Full-text search across node IDs, gists, and notes in both user and project graphs. Returns matching nodes with full content. Use before kg_put_node to check for duplicates. Use when a problem feels familiar — memory likely has the answer.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search term (case-insensitive, matched against node id, gist, notes, touches)"
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session ID (to include project graph in search)"
                        }
                    },
                    "required": ["query"]
                }
            ),
            Tool(
                name="kg_put_node",
                description="Create or update a node. level determines storage: 'user' for cross-project wisdom, 'project' for codebase-specific knowledge. If node ID exists, fields are merged (omitted fields unchanged). Search before creating to avoid duplicates. Connect with kg_put_edge after — unconnected nodes risk archival.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID from kg_read"
                        },
                        "level": {
                            "type": "string",
                            "enum": ["user", "project"],
                            "description": "Storage level"
                        },
                        "id": {
                            "type": "string",
                            "description": "Node ID (kebab-case)"
                        },
                        "gist": {
                            "type": "string",
                            "description": "Compressed headline — what this node captures"
                        },
                        "notes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Rationale, constraints, 'why' — recalled on demand"
                        },
                        "touches": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Related file paths or artifact references"
                        }
                    },
                    "required": ["session_id", "level", "id", "gist"]
                }
            ),
            Tool(
                name="kg_put_edge",
                description="Create or update a relationship between two nodes or file paths. Prefer edges over new nodes — relationships are cheaper and reuse existing concepts. Edges protect connected nodes from archival.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID from kg_read"
                        },
                        "level": {
                            "type": "string",
                            "enum": ["user", "project"],
                            "description": "Storage level"
                        },
                        "from": {
                            "type": "string",
                            "description": "Source node ID or file path"
                        },
                        "to": {
                            "type": "string",
                            "description": "Target node ID or file path"
                        },
                        "rel": {
                            "type": "string",
                            "description": "Relationship type (kebab-case)"
                        },
                        "notes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Additional context for this relationship"
                        }
                    },
                    "required": ["session_id", "level", "from", "to", "rel"]
                }
            ),
            Tool(
                name="kg_delete_node",
                description="Delete a node by ID and all its connected edges. Automatically finds which graph the node is in.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID from kg_read"
                        },
                        "id": {
                            "type": "string",
                            "description": "Node ID to delete"
                        }
                    },
                    "required": ["session_id", "id"]
                }
            ),
            Tool(
                name="kg_delete_edge",
                description="Delete a specific edge by its from, to, and rel key. Automatically finds which graph the edge is in.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID from kg_read"
                        },
                        "from": {
                            "type": "string",
                            "description": "Source node ID"
                        },
                        "to": {
                            "type": "string",
                            "description": "Target node ID"
                        },
                        "rel": {
                            "type": "string",
                            "description": "Relationship type"
                        }
                    },
                    "required": ["session_id", "from", "to", "rel"]
                }
            ),
            Tool(
                name="kg_useful",
                description="Mark the nodes that actually HELPED this session — explicit usefulness endorsement that feeds archival scoring (useful nodes stay active longer). Up to 5 per session, one vote per node; endorsement, not traffic — reads don't count. Call this toward the END of the session (wrap-up), judging against actual results: which knowledge demonstrably changed the outcome — not what merely seemed promising mid-flight.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID from kg_read/preload"
                        },
                        "ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Node IDs that proved genuinely useful (session budget: 5 total)"
                        }
                    },
                    "required": ["session_id", "ids"]
                }
            ),
            Tool(
                name="kg_sync",
                description="Pull changes made by other sessions since your last sync. Returns new/updated nodes and edges. Call after subagents finish, periodically in long sessions, or before decisions depending on shared knowledge.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID from kg_read"
                        }
                    },
                    "required": ["session_id"]
                }
            ),
            Tool(
                name="kg_progress",
                description="Track multi-step task progress across context compaction and session boundaries. Call with task_id only to read current state. Add state to write. Progress persists to disk.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID from kg_read"
                        },
                        "task_id": {
                            "type": "string",
                            "description": "Task identifier (e.g. 'scout', 'extract')"
                        },
                        "state": {
                            "type": "object",
                            "description": "Progress state to persist. Omit to read current state."
                        },
                        "level": {
                            "type": "string",
                            "enum": ["user", "project"],
                            "description": "Storage level (default: user)"
                        }
                    },
                    "required": ["session_id", "task_id"]
                }
            ),
        ]

    # ========================================================================
    # Tool Handlers
    # ========================================================================

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Handle tool calls."""
        global store, session_manager

        try:
            if name == "kg_read":
                cwd = arguments.get("cwd")
                node_id = arguments.get("id")
                node_ids = arguments.get("ids")
                level = arguments.get("level")
                sid_arg = arguments.get("session_id")

                # Resolve the session. A valid caller-supplied session_id is
                # reused as-is — no re-registration, no sessions.json fsync per
                # crumb read. Only the true first call (cwd, no session) mints a
                # session. lookup() is deliberately non-mutating: an unknown or
                # path-less session_id falls through to cwd registration rather
                # than being silently auto-created without a project path.
                session_id = None
                if sid_arg:
                    info = session_manager.lookup(sid_arg)
                    if info and info.get("project_path"):
                        session_id = sid_arg
                        session_manager.increment_ops(session_id)
                if not session_id:
                    if not cwd:
                        return [TextContent(
                            type="text",
                            text="Error: pass cwd on the first kg_read call (or a valid session_id from it)."
                        )]
                    project_root = str(Path(cwd).resolve())
                    result = session_manager.register(project_root)
                    session_id = result["session_id"]

                # Single or batch node read — full content, compact text.
                ids = list(node_ids) if node_ids else ([node_id] if node_id else None)
                if ids:
                    blocks = []
                    read_ok = []
                    for nid in ids:
                        try:
                            result = store.read_node(nid, level=level, session_id=session_id)
                            blocks.append(format_node_full(nid, result))
                            read_ok.append(nid)
                        except NodeNotFoundError:
                            blocks.append(f"▸ {nid}: NOT FOUND (try kg_search — it reaches all tiers)")
                    session_manager.mark_seen(session_id, read_ok)
                    return [TextContent(
                        type="text",
                        text="\n\n".join(blocks) + f"\n\nSession: {session_id}"
                    )]

                # Full graph read — rendering + inline-guarantee degradation
                # ladder live in mcp_http.read_format (shared line rendering
                # with the estimator: render == charge, exact characters).
                # Gists the session-start preload already put in context render
                # as id-only anchors — the budget goes to what the compact
                # preload had to drop.
                graphs = store.read_graphs(session_id)
                scores = store.scores_for_read(session_id)
                preloaded = session_manager.get_preloaded(session_id)
                # Every active gist is now in the session's context — searches
                # won't re-dump notes for them without an explicit node read.
                shown = [
                    n["id"]
                    for lvl in ("user", "project")
                    for n in graphs[lvl]["nodes"]
                    if not n.get("_archived") and "_orphaned_ts" not in n
                ]
                session_manager.mark_seen(session_id, shown)
                return [TextContent(
                    type="text",
                    text=build_full_read(graphs, scores, session_id, preloaded=preloaded),
                )]

            elif name == "kg_search":
                query_raw = arguments["query"]
                sid = arguments.get("session_id")
                seen = set()
                if sid:
                    session_manager.increment_ops(sid)
                    seen = session_manager.get_seen(sid)

                # RRF search lives in the store (which holds the lock during the
                # scan — the maintenance thread mutates node dicts concurrently).
                result = store.search(query_raw, session_id=sid, seen=seen)

                if result["total"] == 0:
                    suffix = "" if sid else " (no session_id — project graph searched best-effort; pass session_id from kg_read for accurate project search)"
                    return [TextContent(type="text", text=f"No nodes found matching '{query_raw}'{suffix}")]

                session_note = "" if sid else " [no session_id — project results best-effort across all loaded graphs]"
                text = format_search(query_raw, result, session_note)

                # These gists are now in the session's context — future searches
                # show them as one-line reminders, never re-dump their notes.
                if sid:
                    shown = (
                        [r["id"] for r in result["top"]]
                        + [m["id"] for m in result["more"]]
                        + [c["id"] for c in result["connectors"]]
                    )
                    session_manager.mark_seen(sid, shown)

                return [TextContent(type="text", text=text)]

            elif name == "kg_put_node":
                sid = arguments["session_id"]
                session_manager.increment_ops(sid)
                result = store.put_node(
                    level=arguments["level"],
                    node_id=arguments["id"],
                    gist=arguments["gist"],
                    notes=arguments.get("notes"),
                    touches=arguments.get("touches"),
                    session_id=sid
                )
                return [TextContent(type="text", text=f"Node '{arguments['id']}' saved to {arguments['level']} graph")]

            elif name == "kg_put_edge":
                sid = arguments["session_id"]
                session_manager.increment_ops(sid)
                result = store.put_edge(
                    level=arguments["level"],
                    from_ref=arguments["from"],
                    to_ref=arguments["to"],
                    rel=arguments["rel"],
                    notes=arguments.get("notes"),
                    session_id=sid
                )
                return [TextContent(
                    type="text",
                    text=f"Edge {arguments['from']}->{arguments['to']}:{arguments['rel']} saved to {arguments['level']} graph"
                )]

            elif name == "kg_useful":
                sid = arguments["session_id"]
                session_manager.increment_ops(sid)
                result = store.mark_useful(arguments["ids"], sid)
                parts = []
                if result["accepted"]:
                    parts.append("Marked useful: " + ", ".join(result["accepted"]))
                parts.extend(f"Skipped {nid}: {why}" for nid, why in result["rejected"].items())
                parts.append(f"{result['remaining']} like(s) remaining this session.")
                return [TextContent(type="text", text="\n".join(parts))]

            elif name == "kg_delete_node":
                sid = arguments["session_id"]
                session_manager.increment_ops(sid)
                result = store.delete_node(
                    node_id=arguments["id"],
                    session_id=sid
                )
                return [TextContent(
                    type="text",
                    text=f"Deleted node '{arguments['id']}' and {result['edges_deleted']} connected edges from {result['level']} graph"
                )]

            elif name == "kg_delete_edge":
                sid = arguments["session_id"]
                session_manager.increment_ops(sid)
                result = store.delete_edge(
                    from_ref=arguments["from"],
                    to_ref=arguments["to"],
                    rel=arguments["rel"],
                    session_id=sid
                )
                status = "deleted" if result["deleted"] else "not found"
                return [TextContent(
                    type="text",
                    text=f"Edge {status}: {arguments['from']}->{arguments['to']}:{arguments['rel']}"
                )]

            elif name == "kg_sync":
                session_id = arguments["session_id"]
                session_manager.increment_ops(session_id)
                sync_ts = session_manager.get_sync_ts(session_id)
                updates = store.get_sync_diff(session_id, sync_ts)

                # Advance sync timestamp
                session_manager.mark_synced(session_id)

                user_updates = len(updates["user"]["nodes"]) + len(updates["user"]["edges"])
                proj_updates = len(updates["project"]["nodes"]) + len(updates["project"]["edges"])

                if user_updates == 0 and proj_updates == 0:
                    return [TextContent(type="text", text="No updates from other sessions")]

                def format_sync_compact(level_label: str, diff: dict) -> str:
                    lines = [f"{level_label}: {len(diff['nodes'])} node changes, {len(diff['edges'])} edge changes"]
                    for nid, node in diff["nodes"].items():
                        archived = " [archived]" if node.get("_archived") else ""
                        lines.append(f"  node {nid}{archived}: {node.get('gist', '')[:100]}")
                    for eid, edge in diff["edges"].items():
                        lines.append(f"  edge {edge['from']} --{edge['rel']}--> {edge['to']}")
                    return "\n".join(lines)

                return [TextContent(
                    type="text",
                    text="Updates from other sessions:\n\n"
                        + format_sync_compact("User", updates["user"]) + "\n"
                        + format_sync_compact("Project", updates["project"])
                )]

            elif name == "kg_progress":
                sid = arguments["session_id"]
                session_manager.increment_ops(sid)
                task_id = arguments["task_id"]
                state = arguments.get("state")
                level = arguments.get("level", "user")

                if state is not None:
                    # Write mode
                    store.set_progress(task_id, state, level, sid)
                    return [TextContent(type="text", text=f"Progress saved for task '{task_id}'")]
                else:
                    # Read mode
                    result = store.get_progress(task_id, level, sid)
                    if not result:
                        return [TextContent(type="text", text=f"No progress found for task '{task_id}'")]
                    return [TextContent(
                        type="text",
                        text=f"Progress for '{task_id}':\n{json.dumps(result, indent=2)}"
                    )]

            else:
                raise ValueError(f"Unknown tool: {name}")

        except NodeNotFoundError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        except SessionNotFoundError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        except KGError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        except Exception as e:
            logger.error(f"Tool error: {e}", exc_info=True)
            return [TextContent(type="text", text=f"Internal error: {str(e)}")]

    return server


async def main():
    """Main entry point."""
    global store, session_manager, connection_manager, mcp_server

    # Load configuration
    from core.constants import (
        get_storage_root, user_graph_path,
        GRACE_PERIOD_DAYS, ORPHAN_GRACE_DAYS,
    )
    # The size budget is a fixed invariant (MAX_CHARS_PER_LEVEL), deliberately
    # NOT env-configurable: the inline guarantee's arithmetic depends on it.
    # The old KG_MAX_TOKENS override is gone.
    config = GraphConfig(
        orphan_grace_days=int(os.getenv("KG_ORPHAN_GRACE_DAYS", str(ORPHAN_GRACE_DAYS))),
        grace_period_days=int(os.getenv("KG_GRACE_PERIOD_DAYS", str(GRACE_PERIOD_DAYS))),
        save_interval=int(os.getenv("KG_SAVE_INTERVAL", "30")),
        storage_root=get_storage_root(),
        user_path=user_graph_path(),
    )

    session_manager = HTTPSessionManager()
    connection_manager = ConnectionManager()

    # Broadcast callback for WebSocket
    async def broadcast_callback(project_path: str | None, message: dict, exclude_session: str | None):
        await connection_manager.broadcast_to_project(
            project_path, message, exclude_session, session_manager
        )

    store = MultiProjectGraphStore(config, session_manager, broadcast_callback)
    mcp_server = create_mcp_server()

    # Periodic git auto-commit of the storage root (KG_AUTOCOMMIT_INTERVAL,
    # default 900s, 0 disables). Runs inside the server so history accumulates
    # even when the process dies with the machine and no managed stop ever runs.
    autocommitter = AutoCommitter(config.storage_root)
    autocommitter.start()

    # Create Streamable HTTP session manager
    mcp_session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        event_store=None,  # No resumability for now
        json_response=True,  # Use JSON responses (Streamable HTTP standard)
        stateless=True,  # Allow stateless connections (Claude Code compatible)
    )

    # REST API + WebSocket for the visual editor (see mcp_http/rest.py)
    rest_api = create_rest_api(store, session_manager, connection_manager, __version__)

    port = int(os.getenv("KG_HTTP_PORT", "8765"))
    host = os.getenv("KG_HTTP_HOST", "127.0.0.1")

    # Create Starlette app with custom ASGI routing
    async def app_asgi(scope, receive, send):
        """ASGI app that routes between MCP, REST API, and health endpoints."""
        path = scope.get("path", "")

        # Anti DNS-rebinding: every HTTP/WebSocket request must address this
        # machine by a local hostname. A malicious page whose domain re-resolves
        # to 127.0.0.1 becomes same-origin to this server (CORS no longer
        # applies), but it still carries the attacker's domain in Host — reject.
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers") or [])
            host_header = headers.get(b"host", b"").decode("latin-1")
            if not host_allowed(host_header, configured_host=host):
                logger.warning(f"Rejected request with non-local Host: {host_header!r}")
                if scope["type"] == "websocket":
                    await WebSocketClose(code=1008)(scope, receive, send)
                else:
                    response = PlainTextResponse("Misdirected Request: Host must be local", status_code=421)
                    await response(scope, receive, send)
                return

        if path == "/health":
            # MCP health check (simple)
            response = JSONResponse({
                "status": "ok",
                "version": __version__,
                "transport": "streamable-http",
                "active_sessions": session_manager.count(),
                "loaded_graphs": len(store.graphs)
            })
            await response(scope, receive, send)
        elif path.startswith("/api/") or path == "/ws":
            # REST API endpoints and WebSocket (for visual editor)
            await rest_api(scope, receive, send)
        elif path == "/":
            # MCP Streamable HTTP requests
            await mcp_session_manager.handle_request(scope, receive, send)
        else:
            # 404 for other paths
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(scope):
        """Manage application lifespan."""
        logger.info("Starting MCP Streamable HTTP Server...")

        # Start MCP session manager
        async with mcp_session_manager.run():
            logger.info("MCP session manager running")
            yield

        # Shutdown (idempotent — the post-serve fallback may call it again).
        # Order matters: flush the store to disk first, then make a final
        # best-effort commit so it captures the flushed state.
        if store:
            store.shutdown()
        autocommitter.stop(final_commit=True)
        logger.info("Server stopped")

    # Wrap ASGI app with lifespan
    class AppWithLifespan:
        async def __call__(self, scope, receive, send):
            if scope["type"] == "lifespan":
                async with lifespan(scope):
                    while True:
                        message = await receive()
                        if message["type"] == "lifespan.startup":
                            await send({"type": "lifespan.startup.complete"})
                        elif message["type"] == "lifespan.shutdown":
                            await send({"type": "lifespan.shutdown.complete"})
                            return
            else:
                await app_asgi(scope, receive, send)

    app = AppWithLifespan()

    logger.info(f"MCP Streamable HTTP endpoint: http://{host}:{port}/")
    logger.info(f"Health check: http://{host}:{port}/health")

    import uvicorn

    # Run uvicorn server
    config_uvi = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level.lower()
    )
    server_uvi = uvicorn.Server(config_uvi)

    # Signal handlers for graceful shutdown — trigger uvicorn's shutdown
    # instead of sys.exit() so connections drain properly
    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, initiating graceful shutdown...")
        server_uvi.should_exit = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    await server_uvi.serve()

    # After uvicorn exits, flush store + final commit (no-op if lifespan already did)
    if store:
        store.shutdown()
    autocommitter.stop(final_commit=True)


if __name__ == "__main__":
    asyncio.run(main())
