"""Visual Editor Backend - FastAPI server for knowledge graph visualization."""

import logging
import os
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Import project discovery utilities
sys.path.insert(0, str(Path(__file__).parent))
from project_discovery import discover_projects

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# MCP Server configuration
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8765")
MCP_TIMEOUT = 30.0

app = FastAPI(title="Knowledge Graph Visual Editor", version="0.1.0")

# CORS configuration (allow browser access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        mcp_status = {"status": "down", "error": str(e)}

    return {
        "status": "ok",
        "editor_version": "0.1.0",
        "mcp_server": mcp_status
    }


@app.get("/api/projects")
async def list_projects():
    """
    List all discovered Claude Code projects with metadata.

    Returns:
        List of projects with stats, sorted by last_used (most recent first)
    """
    try:
        projects = discover_projects()
        return projects
    except Exception as e:
        logger.exception("Error discovering projects")
        raise HTTPException(status_code=500, detail=str(e))


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
            # Use MCP server's REST API
            # Always reload from disk to ensure visual editor shows latest data
            params = {"reload": "true"}
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

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="MCP server timeout")
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to MCP server at {MCP_SERVER_URL}"
        )
    except Exception as e:
        logger.exception("Error fetching graph from MCP server")
        raise HTTPException(status_code=500, detail=str(e))


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

class EdgeCreate(BaseModel):
    level: str
    from_ref: str = Field(alias="from")
    to_ref: str = Field(alias="to")
    rel: str
    notes: list[str] | None = None
    session_id: str | None = None

@app.post("/api/nodes")
async def create_node(data: NodeCreate):
    """Create or update a node (proxy to MCP server)."""
    try:
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.post(
                f"{MCP_SERVER_URL}/api/nodes",
                json=data.model_dump(by_alias=True)
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="MCP server timeout")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=f"Cannot connect to MCP server")
    except Exception as e:
        logger.exception("Error creating node")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/nodes/{level}/{node_id}")
async def delete_node(level: str, node_id: str, session_id: str | None = None):
    """Delete a node (proxy to MCP server)."""
    try:
        params = {"session_id": session_id} if session_id else {}
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.delete(
                f"{MCP_SERVER_URL}/api/nodes/{level}/{node_id}",
                params=params
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.exception("Error deleting node")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/edges")
async def create_edge(data: EdgeCreate):
    """Create or update an edge (proxy to MCP server)."""
    try:
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.post(
                f"{MCP_SERVER_URL}/api/edges",
                json=data.model_dump(by_alias=True)
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.exception("Error creating edge")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/edges/{level}/{from_id}/{to_id}/{rel}")
async def delete_edge(level: str, from_id: str, to_id: str, rel: str, session_id: str | None = None):
    """Delete an edge (proxy to MCP server)."""
    try:
        params = {"session_id": session_id} if session_id else {}
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.delete(
                f"{MCP_SERVER_URL}/api/edges/{level}/{from_id}/{to_id}/{rel}",
                params=params
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.exception("Error deleting edge")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/nodes/{level}/{node_id}/recall")
async def recall_node(level: str, node_id: str, session_id: str | None = None):
    """Recall an archived node (proxy to MCP server)."""
    try:
        params = {"session_id": session_id} if session_id else {}
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
            response = await client.post(
                f"{MCP_SERVER_URL}/api/nodes/{level}/{node_id}/recall",
                params=params
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.exception("Error recalling node")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# WebSocket Proxy
# ============================================================================

@app.websocket("/ws")
async def websocket_proxy(websocket: WebSocket, session_id: str | None = None):
    """WebSocket proxy to MCP server."""
    await websocket.accept()

    import websockets
    import asyncio

    try:
        params = f"?session_id={session_id}" if session_id else ""
        async with websockets.connect(f"ws://127.0.0.1:8765/ws{params}") as mcp_ws:

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

    port = int(os.getenv("EDITOR_PORT", "3000"))
    host = os.getenv("EDITOR_HOST", "127.0.0.1")

    logger.info(f"Starting Visual Editor on http://{host}:{port}")
    logger.info(f"MCP Server: {MCP_SERVER_URL}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
