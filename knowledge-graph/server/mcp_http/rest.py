"""REST API + WebSocket endpoint for the visual editor.

Extracted from the server entrypoint so it can be constructed in-process by
tests (FastAPI TestClient) — the endpoint wiring is where every shipped bug
has lived, so it is the layer most worth exercising directly.

No authentication by design: the trust boundary is "processes on this
machine". The umbrella ASGI dispatcher enforces Host validation (anti
DNS-rebinding) for all routes; the WebSocket endpoint additionally checks
Origin here because browsers do not apply CORS to WebSocket upgrades.
"""

import logging

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from core.constants import project_namespace
from core.exceptions import KGError, NodeNotFoundError, SessionNotFoundError
from .security import origin_allowed

logger = logging.getLogger(__name__)


class NodeCreateRequest(BaseModel):
    level: str
    id: str
    gist: str
    notes: list[str] | None = None
    touches: list[str] | None = None
    session_id: str | None = None
    # Resolves a project graph directly — required by the visual editor, whose
    # session has no project_path registered. Same addressing as read/delete.
    project_path: str | None = None


class EdgeCreateRequest(BaseModel):
    level: str
    from_: str = Field(alias="from")
    to: str
    rel: str
    notes: list[str] | None = None
    session_id: str | None = None
    project_path: str | None = None


class ProgressSetRequest(BaseModel):
    task_id: str
    state: dict
    level: str = "user"
    session_id: str | None = None


def create_rest_api(store, session_manager, connection_manager, version: str) -> FastAPI:
    """Build the REST/WebSocket FastAPI app over a store + session manager."""

    rest_api = FastAPI(title="Knowledge Graph REST API", version=version)

    @rest_api.get("/api/health")
    async def rest_health():
        """REST API health check."""
        return {
            "status": "ok",
            "version": version,
            "transport": "streamable-http",
            "active_sessions": session_manager.count(),
            "loaded_graphs": len(store.graphs)
        }

    @rest_api.get("/api/graph/read")
    async def rest_read_graphs(session_id: str | None = None, project_path: str | None = None, reload: bool = False):
        """Read all graphs. Pass reload=true to force re-read from disk."""
        try:
            return store.read_graphs(session_id=session_id, project_path=project_path, force_reload=reload)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @rest_api.post("/api/sessions/register")
    async def rest_register_session(project_path: str | None = None):
        """Register a new session. Used by visual editor."""
        return session_manager.register(project_path)

    @rest_api.get("/api/session_bootstrap")
    async def rest_session_bootstrap(project_path: str):
        """Session-start memory preload, used by the SessionStart hook.

        Registers a session and returns a compact core render capped at
        BOOTSTRAP_CHAR_BUDGET — hook additionalContext stays inline only up to
        ~10K chars (measured; tool results tolerate ~50K), so the preload
        carries the top-scored gists and the loud kg_read renders the rest
        without repeating them. "context" is the final injectable text
        (instruction header included); "text" is the graph body alone for
        older hooks that compose their own header. The rendered ids seed the
        session's preloaded + seen sets — that is what kg_read dedups against.
        """
        from .read_format import build_bootstrap
        try:
            reg = session_manager.register(project_path)
            session_id = reg["session_id"]
            graphs = store.read_graphs(session_id)
            scores = store.scores_for_read(session_id)
            result = build_bootstrap(graphs, scores, session_id)
            session_manager.set_preloaded(session_id, result["shown_ids"])
            session_manager.mark_seen(session_id, result["shown_ids"])
            return {
                "session_id": session_id,
                "context": result["context"],
                "text": result["text"],
                "stats": result["stats"],
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"session_bootstrap failed: {e}")
            raise HTTPException(status_code=500, detail="bootstrap failed")

    @rest_api.get("/api/session_state")
    async def rest_session_state(project_path: str):
        """Lightweight session state for the kg-remind hook.

        The hook keys its deterministic nudge on full_read_done: until the
        session has made the loud full-graph kg_read, every prompt carries the
        reminder that the preload is partial. Resolves the newest live session
        for the project path (the hook always runs inside the newest one).
        Returns found=False rather than 404 so the bash caller can branch on
        JSON alone without inspecting HTTP status.
        """
        try:
            hit = session_manager.find_by_project_path(project_path)
            if not hit:
                return {"found": False}
            sid, data = hit
            return {
                "found": True,
                "session_id": sid,
                "full_read_done": bool(data.get("full_read_ts")),
                "preloaded_count": len(data.get("preloaded_ids", [])),
            }
        except Exception as e:
            logger.error(f"session_state failed: {e}")
            raise HTTPException(status_code=500, detail="session_state failed")

    # ========================================================================
    # Ambient memory endpoints (per-event hooks post their raw stdin JSON;
    # responses are ready-to-print hook output — the bash side never parses)
    # ========================================================================

    @rest_api.post("/api/prompt_context")
    async def rest_prompt_context(payload: dict):
        """UserPromptSubmit: full-read nudge or prompt-matched recall.

        {} means "nothing deterministic to say" — the hook falls back to its
        staged random pools. Any hint of hookSpecificOutput in the body is
        printed by the hook verbatim.
        """
        from .ambient import build_prompt_recall
        try:
            text = build_prompt_recall(
                store, session_manager,
                payload.get("cwd") or "", payload.get("prompt") or "",
            )
        except Exception:
            logger.exception("prompt_context failed")
            return {}
        if not text:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": text,
            }
        }

    @rest_api.post("/api/tool_event")
    async def rest_tool_event(payload: dict):
        """PostToolUse (Read|WebFetch|WebSearch): count the target; nudge
        capture only on proven re-derivation of an uncovered target."""
        from .ambient import handle_tool_event
        try:
            text = handle_tool_event(store, session_manager, payload)
        except Exception:
            logger.exception("tool_event failed")
            return {}
        if not text:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": text,
            }
        }

    # ========================================================================
    # Write API Endpoints
    # ========================================================================

    @rest_api.post("/api/nodes")
    async def rest_create_node(data: NodeCreateRequest):
        """Create or update a node."""
        try:
            return store.put_node(
                level=data.level,
                node_id=data.id,
                gist=data.gist,
                notes=data.notes,
                touches=data.touches,
                session_id=data.session_id,
                project_path=data.project_path,
            )
        except (KGError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            logger.exception("Error creating node")
            raise HTTPException(status_code=500, detail="Failed to create node")

    @rest_api.delete("/api/nodes/{level}/{node_id}")
    async def rest_delete_node(level: str, node_id: str, session_id: str | None = None,
                               project_path: str | None = None):
        """Delete a node.

        project_path resolves a project node without a registered session (editor) —
        mirrors rest_read_node.
        """
        try:
            return store.delete_node(node_id, level=level, session_id=session_id,
                                     project_path=project_path)
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except (KGError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            logger.exception("Error deleting node")
            raise HTTPException(status_code=500, detail="Failed to delete node")

    @rest_api.post("/api/edges")
    async def rest_create_edge(data: EdgeCreateRequest):
        """Create or update an edge."""
        try:
            return store.put_edge(
                level=data.level,
                from_ref=data.from_,
                to_ref=data.to,
                rel=data.rel,
                notes=data.notes,
                session_id=data.session_id,
                project_path=data.project_path,
            )
        except (KGError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            logger.exception("Error creating edge")
            raise HTTPException(status_code=500, detail="Failed to create edge")

    @rest_api.delete("/api/edges/{level}/{from_id}/{to_id}/{rel}")
    async def rest_delete_edge(level: str, from_id: str, to_id: str, rel: str,
                               session_id: str | None = None,
                               project_path: str | None = None):
        """Delete an edge."""
        try:
            return store.delete_edge(
                from_ref=from_id,
                to_ref=to_id,
                rel=rel,
                level=level,
                session_id=session_id,
                project_path=project_path,
            )
        except (KGError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            logger.exception("Error deleting edge")
            raise HTTPException(status_code=500, detail="Failed to delete edge")

    # ========================================================================
    # Progress & Stats Endpoints
    # ========================================================================

    @rest_api.get("/api/progress/{task_id}")
    async def rest_get_progress(task_id: str, level: str = "user", session_id: str | None = None):
        """Get progress for a task."""
        try:
            return store.get_progress(task_id, level, session_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @rest_api.post("/api/progress")
    async def rest_set_progress(data: ProgressSetRequest):
        """Set progress for a task."""
        try:
            return store.set_progress(data.task_id, data.state, data.level, data.session_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @rest_api.get("/api/sessions/{session_id}/stats")
    async def rest_session_stats(session_id: str):
        """Get session statistics."""
        try:
            stats = session_manager.get_stats(session_id)
            user_graph = store.graphs.get("user", {"nodes": {}, "edges": {}})
            stats["graphs"] = {
                "user": {"nodes": len(user_graph["nodes"]), "edges": len(user_graph["edges"])}
            }
            try:
                pp = session_manager.get_project_path(session_id)
                if pp:
                    pk = project_namespace(pp)
                    if pk in store.graphs:
                        pg = store.graphs[pk]
                        stats["graphs"]["project"] = {"nodes": len(pg["nodes"]), "edges": len(pg["edges"])}
            except Exception:
                pass
            return stats
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @rest_api.get("/api/nodes/{level}/{node_id}")
    async def rest_read_node(level: str, node_id: str, session_id: str | None = None,
                             project_path: str | None = None):
        """Read a single node's full content. Promotes archived nodes to active.

        project_path is an alternative to session_id for resolving a project node
        (used by the visual editor, whose session has no registered project path).
        """
        try:
            return store.read_node(node_id, level=level, session_id=session_id,
                                   project_path=project_path)
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except (KGError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            logger.exception("Error reading node")
            raise HTTPException(status_code=500, detail="Failed to read node")

    # ========================================================================
    # WebSocket Endpoint
    # ========================================================================

    @rest_api.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, session_id: str | None = None):
        """WebSocket endpoint for real-time graph updates.

        Browsers do not apply CORS to WebSocket upgrades, so without the Origin
        check any web page could connect and receive every graph broadcast.
        Absent Origin (non-browser clients, e.g. the editor backend proxy) is
        allowed; a present Origin must be local.
        """
        if not origin_allowed(websocket.headers.get("origin")):
            logger.warning(f"Rejected WebSocket from origin: {websocket.headers.get('origin')}")
            await websocket.close(code=1008)
            return

        if not session_id:
            session_result = session_manager.register(None)
            session_id = session_result["session_id"]

        await connection_manager.connect(websocket, session_id)

        try:
            await connection_manager.send_personal(session_id, {
                "type": "connected",
                "session_id": session_id
            })

            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await connection_manager.send_personal(session_id, {"type": "pong"})

        except WebSocketDisconnect:
            connection_manager.disconnect(session_id)
            logger.info(f"WebSocket disconnected: {session_id}")

    return rest_api
