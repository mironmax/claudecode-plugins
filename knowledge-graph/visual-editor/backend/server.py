"""Visual Editor Backend - FastAPI server for knowledge graph visualization."""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import quote, urlsplit

# Import project discovery utilities
sys.path.insert(0, str(Path(__file__).parent))
from project_discovery import discover_projects

# Reuse kg_search's pure ranking and path helpers on the server's snapshot.
# This also works with an older running MCP server; no second search engine
# or direct access to its storage is needed.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from core.search import connection_paths, rank_nodes, search_terms
from core.utils import GIST_SCAN_LIMIT

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# MCP Server configuration
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8765")
# The memory server's live-update socket, on whatever host and port it runs.
MCP_WS_URL = "ws" + MCP_SERVER_URL.rstrip("/").removeprefix("http") + "/ws"
MCP_TIMEOUT = 30.0
EDITOR_PORT = int(os.getenv("EDITOR_PORT", "8766"))


def _plugin_version() -> str:
    """The editor versions with the plugin — read it from plugin.json."""
    try:
        plugin_json = Path(__file__).parent.parent.parent / ".claude-plugin" / "plugin.json"
        return json.loads(plugin_json.read_text())["version"]
    except Exception:
        return "unknown"


EDITOR_VERSION = _plugin_version()

app = FastAPI(title="Knowledge Graph Visual Editor", version=EDITOR_VERSION)

# CORS configuration (allow browser access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{EDITOR_PORT}", f"http://127.0.0.1:{EDITOR_PORT}"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Anti DNS-rebinding: a malicious page whose domain re-resolves to 127.0.0.1
# becomes same-origin to this editor (CORS no longer applies) and could read
# the whole graph through the proxy — but it still carries its own domain in
# the Host header. Only accept requests addressed to this machine.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])


def _origin_is_local(origin: str | None) -> bool:
    """Browsers do not apply CORS to WebSocket upgrades — check Origin manually.
    Absent Origin (non-browser client) is allowed; present must be local."""
    if not origin:
        return True
    parts = urlsplit(origin.strip().lower())
    return parts.scheme in ("http", "https") and parts.hostname in ("localhost", "127.0.0.1", "::1")


@app.middleware("http")
async def refuse_cross_site(request, call_next):
    """A cross-site page cannot read the proxy's answers, but its GETs and
    simple POSTs still reach the memory server (a read by id promotes a node).
    Browsers mark them with Sec-Fetch-Site or a foreign Origin; non-browser
    clients send neither. Same rule as the server's mcp_http/security.py."""
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site" or not _origin_is_local(origin):
        logger.warning(f"Rejected cross-site request: {request.url.path} (origin {origin!r})")
        return PlainTextResponse("Refused: cross-site request", status_code=403)
    return await call_next(request)

# Static files (frontend)
frontend_dir = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir / "static")), name="static")


# ============================================================================
# API Endpoints - Proxy to MCP Server
# ============================================================================

@app.get("/")
async def serve_index():
    """Serve the main HTML page."""
    index_path = frontend_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_path)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{MCP_SERVER_URL}/api/health")
            mcp_status = response.json() if response.status_code == 200 else {"status": "down"}
    except Exception as e:
        logger.error(f"MCP server health check failed: {e}")
        mcp_status = {"status": "down"}

    return {
        "status": "ok",
        "editor_version": EDITOR_VERSION,
        "mcp_server": mcp_status,
        # Prefer the running server's policy; older servers use this checkout's
        # canonical soft target instead of a separate frontend constant.
        "limits": mcp_status.get("limits", {"gist_target_chars": GIST_SCAN_LIMIT}),
    }


@app.get("/api/projects")
async def list_projects():
    """
    List all discovered Claude Code projects with metadata.

    Returns:
        List of projects with stats, sorted by last_used (most recent first)
    """
    try:
        projects = await discover_projects(MCP_SERVER_URL)
        return projects
    except Exception as e:
        logger.exception("Error discovering projects")
        raise HTTPException(status_code=500, detail="Failed to list projects")


@app.get("/api/graph")
async def get_graph(session_id: str | None = None, project_path: str | None = None):
    """
    Get the full knowledge graph from MCP server.

    Args:
        session_id: Optional session ID (for project-specific graphs)
        project_path: Optional project path (alternative to session_id)

    Returns:
        Combined user + project graph data in format:
        {
            "user": {"nodes": {...}, "edges": {...}},
            "project": {"nodes": {...}, "edges": {...}}
        }
    """
    try:
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            # No reload=true: the server's memory already holds every write,
            # and a forced reload would drop changes not yet on disk.
            params = {}
            if session_id:
                params["session_id"] = session_id
            if project_path:
                params["project_path"] = project_path

            response = await client.get(
                f"{MCP_SERVER_URL}/api/graph/read",
                params=params
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"MCP server error: {response.text}"
                )

            return response.json()

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="MCP server timeout")
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to MCP server at {MCP_SERVER_URL}"
        )
    except Exception as e:
        logger.exception("Error fetching graph from MCP server")
        raise HTTPException(status_code=500, detail="Failed to fetch graph")


def _search_record(node_id: str, node: dict, level: str, score: float,
                   terms: list[str]) -> dict:
    """Compact hit with evidence from the fields kg_search actually scans."""
    fields = {
        "ID": [node_id],
        "Description": [node.get("gist", "")],
        "Notes": node.get("notes", []),
        "Files": node.get("touches", []),
    }
    matched_fields = []
    excerpt = ""
    for label, values in fields.items():
        matching = [value for value in values if any(t in value.lower() for t in terms)]
        if not matching:
            continue
        matched_fields.append(label)
        if label in ("Notes", "Files") and not excerpt:
            value = matching[0]
            first_match = min(value.lower().find(t) for t in terms if t in value.lower())
            start = max(0, first_match - 45)
            excerpt = ("…" if start else "") + value[start:start + 180]
            if start + 180 < len(value):
                excerpt += "…"
    return {
        "id": node_id, "level": level, "gist": node.get("gist", ""),
        "archived": node.get("_archived", False), "orphaned": "_orphaned_ts" in node,
        "score": round(score, 4), "matched_fields": matched_fields, "excerpt": excerpt,
    }


@app.get("/api/search")
async def search_graph(query: str = Query(min_length=1, max_length=500),
                       level: Literal["user", "project"] = "user",
                       project_path: str | None = None):
    """Search every tier of the selected graph without recalling any nodes.

    kg_search's field weights, stemming, bigrams, RRF/IDF ranking and top-five
    connecting paths are shared directly. All remaining hits are included for
    browsing in the editor, rather than clipped to a text response budget.
    """
    if level == "project" and not project_path:
        raise HTTPException(status_code=400, detail="Select a project graph to search")
    query = query.strip()
    terms, bigrams = search_terms(query)
    if not terms:
        return {"top": [], "more": [], "connectors": [], "path_edges": [],
                "total": 0, "terms": []}
    snapshot = await get_graph(project_path=project_path if level == "project" else None)
    graph = snapshot.get(level) or {}
    node_data = graph.get("nodes", [])
    nodes = node_data if isinstance(node_data, dict) else {node["id"]: node for node in node_data}
    edge_data = graph.get("edges", [])
    edges = list(edge_data.values()) if isinstance(edge_data, dict) else edge_data
    scores, _meta = rank_nodes(nodes, terms, bigrams)
    records = [_search_record(nid, nodes[nid], level, score, terms)
               for nid, score in scores.items()]
    # kg_search ranks its rounded scores, including stable ties.
    records.sort(key=lambda record: record["score"], reverse=True)
    top = records[:5]
    paths = connection_paths([record["id"] for record in top], set(nodes),
                             edges)
    hit_ids = set(scores)
    connector_ids = dict.fromkeys(nid for edge in paths for nid in (edge["from"], edge["to"])
                                  if nid not in hit_ids)
    connectors = [{"id": nid, "level": level, "gist": nodes[nid].get("gist", "")}
                  for nid in connector_ids]
    return {"top": top, "more": records[5:], "connectors": connectors,
            "path_edges": paths, "total": len(records), "terms": terms}


# ============================================================================
# Write API Endpoints - Proxy to MCP Server
# ============================================================================

class NodeCreate(BaseModel):
    level: str
    id: str
    gist: str
    notes: list[str] | None = None
    touches: list[str] | None = None
    session_id: str | None = None
    # Resolves a project graph on the MCP server — the editor's session has no
    # project_path registered, so writes to project graphs need this (same
    # addressing as read/recall/delete).
    project_path: str | None = None

class EdgeCreate(BaseModel):
    level: str
    from_ref: str = Field(alias="from")
    to_ref: str = Field(alias="to")
    rel: str
    notes: list[str] | None = None
    session_id: str | None = None
    project_path: str | None = None

def _raise_upstream(response: httpx.Response):
    """Surface the MCP server's status + detail instead of a generic 500,
    so validation errors (400) and not-found (404) reach the editor UI."""
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)


@app.post("/api/nodes")
async def create_node(data: NodeCreate):
    """Create or update a node (proxy to MCP server)."""
    try:
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.post(
                f"{MCP_SERVER_URL}/api/nodes",
                json=data.model_dump(by_alias=True)
            )
            _raise_upstream(response)
            return response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="MCP server timeout")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=f"Cannot connect to MCP server")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating node")
        raise HTTPException(status_code=500, detail="Failed to create node")

@app.delete("/api/nodes/{level}/{node_id}")
async def delete_node(level: str, node_id: str, session_id: str | None = None,
                      project_path: str | None = None):
    """Delete a node (proxy to MCP server).

    project_path lets the editor delete a project node without a session registered
    against that project — mirrors read_node.
    """
    try:
        params = {}
        if session_id:
            params["session_id"] = session_id
        if project_path:
            params["project_path"] = project_path
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.delete(
                f"{MCP_SERVER_URL}/api/nodes/{level}/{node_id}",
                params=params
            )
            _raise_upstream(response)
            return response.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error deleting node")
        raise HTTPException(status_code=500, detail="Failed to delete node")

@app.post("/api/edges")
async def create_edge(data: EdgeCreate):
    """Create or update an edge (proxy to MCP server)."""
    try:
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.post(
                f"{MCP_SERVER_URL}/api/edges",
                json=data.model_dump(by_alias=True)
            )
            _raise_upstream(response)
            return response.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating edge")
        raise HTTPException(status_code=500, detail="Failed to create edge")

@app.delete("/api/edges/{level}/{from_id}/{to_id}/{rel}")
async def delete_edge(level: str, from_id: str, to_id: str, rel: str, session_id: str | None = None,
                      project_path: str | None = None):
    """Delete an edge (proxy to MCP server)."""
    try:
        params = {}
        if session_id:
            params["session_id"] = session_id
        if project_path:
            params["project_path"] = project_path
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.delete(
                f"{MCP_SERVER_URL}/api/edges/{level}/{from_id}/{to_id}/{rel}",
                params=params
            )
            _raise_upstream(response)
            return response.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error deleting edge")
        raise HTTPException(status_code=500, detail="Failed to delete edge")

@app.get("/api/nodes/{level}/{node_id}/score")
async def node_score(level: Literal["user", "project"], node_id: str,
                     session_id: str | None = None, project_path: str | None = None):
    """Proxy the live server's read-only scoring explanation."""
    params = {}
    if session_id:
        params["session_id"] = session_id
    if project_path:
        params["project_path"] = project_path
    try:
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.get(
                f"{MCP_SERVER_URL}/api/nodes/{level}/{quote(node_id, safe='')}/score", params=params,
            )
            _raise_upstream(response)
            return response.json()
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="MCP server timeout")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Cannot connect to MCP server")


@app.get("/api/nodes/{level}/{node_id}")
async def read_node(level: str, node_id: str, session_id: str | None = None,
                    project_path: str | None = None):
    """Read a single node — auto-promotes archived/orphaned to active (proxy to MCP server).

    project_path lets the editor recall a project node without a session registered
    against that project (its WebSocket session has none).
    """
    try:
        params = {}
        if session_id:
            params["session_id"] = session_id
        if project_path:
            params["project_path"] = project_path
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.get(
                f"{MCP_SERVER_URL}/api/nodes/{level}/{node_id}",
                params=params
            )
            _raise_upstream(response)
            return response.json()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error reading node")
        raise HTTPException(status_code=500, detail="Failed to read node")


# ============================================================================
# WebSocket Proxy
# ============================================================================

@app.websocket("/ws")
async def websocket_proxy(websocket: WebSocket, session_id: str | None = None):
    """WebSocket proxy to MCP server."""
    if not _origin_is_local(websocket.headers.get("origin")):
        logger.warning(f"Rejected WebSocket from origin: {websocket.headers.get('origin')}")
        await websocket.close(code=1008)
        return

    await websocket.accept()

    import websockets
    import asyncio

    try:
        params = f"?session_id={session_id}" if session_id else ""
        async with websockets.connect(f"{MCP_WS_URL}{params}") as mcp_ws:

            async def forward_to_mcp():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await mcp_ws.send(data)
                except WebSocketDisconnect:
                    pass

            async def forward_to_client():
                try:
                    async for message in mcp_ws:
                        await websocket.send_text(message)
                except:
                    pass

            await asyncio.gather(
                forward_to_mcp(),
                forward_to_client(),
                return_exceptions=True
            )
    except Exception as e:
        logger.error(f"WebSocket proxy error: {e}")
        await websocket.close()


if __name__ == "__main__":
    import uvicorn

    port = EDITOR_PORT
    host = os.getenv("EDITOR_HOST", "127.0.0.1")

    logger.info(f"Starting Visual Editor on http://{host}:{port}")
    logger.info(f"MCP Server: {MCP_SERVER_URL}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
