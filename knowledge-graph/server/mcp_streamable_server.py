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
from mcp_http.security import host_allowed
from core.exceptions import (
    KGError,
    NodeNotFoundError,
    SessionNotFoundError,
)
from core.utils import is_active, edge_is_live

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
                description="Read the knowledge graph. First call must include cwd to initialize session — returns session_id for subsequent use. Without id: returns all nodes (gist only) and edges from both user and project levels. With id: returns a single node's full content (gist + notes + touches). If the node is archived, it gets promoted to active.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "cwd": {
                            "type": "string",
                            "description": "Project root directory. Required on first call to initialize session and load project graph."
                        },
                        "id": {
                            "type": "string",
                            "description": "Node ID to read in full. Returns gist + notes + touches. Promotes archived nodes to active."
                        },
                        "level": {
                            "type": "string",
                            "enum": ["user", "project"],
                            "description": "Hint which graph the node is in. If omitted, searches both."
                        }
                    },
                    "required": ["cwd"]
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
                level = arguments.get("level")

                # First call with cwd: register session
                session_id = None
                if cwd:
                    project_root = str(Path(cwd).resolve())
                    result = session_manager.register(project_root)
                    session_id = result["session_id"]

                # Single node read
                if node_id:
                    if not session_id:
                        # Find session from recent registrations (cwd should have been passed)
                        return [TextContent(type="text", text="Error: cwd required on first kg_read call to initialize session")]
                    result = store.read_node(node_id, level=level, session_id=session_id)
                    node = result["node"]
                    node_level = result["level"]
                    was_archived = "promoted from archive" if result.get("was_archived") else "active"
                    return [TextContent(
                        type="text",
                        text=f"Node '{node_id}' ({node_level}, {was_archived}):\n\n{json.dumps(node, indent=2)}\n\nSession: {session_id}"
                    )]

                # Full graph read
                graphs = store.read_graphs(session_id)

                def format_graph_compact(level_label: str, nodes: list, edges: list) -> str:
                    """Compact text format: active nodes as id:gist, archived as id only, edges as triples.

                    Only *live* edges are shown — those with at least one active (or
                    artifact) endpoint, per core.utils.edge_is_live. An edge between two
                    archived nodes is a dangling string you cannot pull, so it is
                    suppressed; it reappears automatically once either end is promoted.
                    The SAME predicate drives token charging in TokenEstimator, so what
                    is rendered here is exactly what the compaction budget pays for.

                    Orphaned nodes (_orphaned_ts set) are invisible. Edge notes are
                    omitted — they appear only in kg_read(cwd, id) single-node output.
                    """
                    # edge_is_live keys off node dicts; build an id->node map for it.
                    node_map = {n["id"]: n for n in nodes}
                    active_ids = {nid for nid, n in node_map.items() if is_active(n)}
                    active = [n for n in nodes if not n.get("_archived")]
                    # Archived = archived but NOT orphaned
                    archived = [n for n in nodes if n.get("_archived") and "_orphaned_ts" not in n]

                    lines = [f"=== {level_label.upper()} — {len(active)} active, {len(archived)} archived ==="]

                    if active:
                        lines.append("ACTIVE:")
                        for n in active:
                            lines.append(f"  {n['id']}: {n.get('gist', '')}")

                    if archived:
                        lines.append("ARCHIVED (use kg_read with id to view full content):")
                        for n in archived:
                            lines.append(f"  {n['id']}")

                    live = [e for e in edges if edge_is_live(e, node_map, active_ids)]
                    if live:
                        lines.append("EDGES:")
                        for e in live:
                            # Notes omitted in full-graph view (see single-node read for details)
                            lines.append(f"  {e['from']} --{e['rel']}--> {e['to']}")

                    return "\n".join(lines)

                user_text = format_graph_compact("User Graph", graphs["user"]["nodes"], graphs["user"]["edges"])
                proj_text = format_graph_compact("Project Graph", graphs["project"]["nodes"], graphs["project"]["edges"])

                # Append health stats. Counts reflect what kg_read actually shows:
                # active nodes and live edges (the same edges rendered above), so the
                # numbers match the visible sections rather than raw on-disk totals.
                def health_line(nodes: list, edges: list) -> str:
                    node_map = {n["id"]: n for n in nodes}
                    active_ids = {nid for nid, n in node_map.items() if is_active(n)}
                    active = [n for n in nodes if not n.get("_archived")]
                    live = [e for e in edges if edge_is_live(e, node_map, active_ids)]
                    connected_ids = set()
                    for e in live:
                        connected_ids.add(e["from"])
                        connected_ids.add(e["to"])
                    orphans = [n for n in active if n["id"] not in connected_ids]
                    n_count = len(active)
                    e_count = len(live)
                    o_count = len(orphans)
                    o_pct = round(100 * o_count / n_count) if n_count else 0
                    avg_edges = round(e_count / n_count, 1) if n_count else 0
                    return f"HEALTH: {n_count} nodes, {e_count} edges, {o_count} orphans ({o_pct}%), avg {avg_edges} edges/node"

                user_health = health_line(graphs["user"]["nodes"], graphs["user"]["edges"])
                proj_health = health_line(graphs["project"]["nodes"], graphs["project"]["edges"])

                full_user = user_text + "\n" + user_health
                full_proj = proj_text + "\n" + proj_health
                total_chars = len(full_user) + len(full_proj)

                size_warning = ""
                if total_chars > 45000:
                    size_warning = (
                        f"\n\nNote: graph output is {total_chars} chars — getting large. "
                        f"A maintenance pass (/kg-maintain) would help keep it readable."
                    )

                session_line = f"\n\nSession: {session_id}" if session_id else ""

                return [
                    TextContent(type="text", text=full_user + "\n" + full_proj + size_warning + session_line),
                ]

            elif name == "kg_search":
                query_raw = arguments["query"]
                sid = arguments.get("session_id")
                if sid:
                    session_manager.increment_ops(sid)

                # RRF search lives in the store (which holds the lock during the
                # scan — the maintenance thread mutates node dicts concurrently).
                results = store.search(query_raw, session_id=sid)

                if not results:
                    suffix = "" if sid else " (no session_id — project graph searched best-effort; pass session_id from kg_read for accurate project search)"
                    return [TextContent(type="text", text=f"No nodes found matching '{query_raw}'{suffix}")]

                session_note = "" if sid else "\nNote: no session_id provided — project results are best-effort across all loaded graphs."
                return [TextContent(
                    type="text",
                    text=f"Found {len(results)} node(s) matching '{query_raw}' (ranked by RRF):{session_note}\n\n{json.dumps(results, indent=2)}"
                )]

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
        GRACE_PERIOD_DAYS, ORPHAN_GRACE_DAYS, MAX_TOKENS,
    )
    config = GraphConfig(
        max_tokens=int(os.getenv("KG_MAX_TOKENS", str(MAX_TOKENS))),
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

        # Shutdown (idempotent — the post-serve fallback may call it again)
        if store:
            store.shutdown()
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

    # After uvicorn exits, flush store (no-op if lifespan already did)
    if store:
        store.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
