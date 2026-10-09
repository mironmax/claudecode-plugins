# Knowledge Graph Visual Editor

A web-based D3.js graph editor for the knowledge graph. Runs as a separate FastAPI server that proxies to the MCP server (port 8765) and serves the SPA frontend.

For end-user usage, see:
- **[VISUAL_EDITOR_GUIDE.md](../VISUAL_EDITOR_GUIDE.md)** — feature tour: layout, node interaction, inline editing, troubleshooting
- **[Wiki: Visual Editor](https://github.com/mironmax/kg-memory/wiki/Visual-Editor)** — same content, lives with the rest of the project docs

This README covers the codebase only.

## Architecture

```
Browser ─HTTP─► Visual Editor (FastAPI, port 8766)
                    │
                    ├─HTTP──► MCP Server REST API  (localhost:8765/api/*)
                    └─WS────► MCP Server WebSocket (localhost:8765/ws)
```

The visual editor stores no data. Reads and writes use the MCP server. Search
runs the shared `server/core/search.py` helpers on its read-only graph snapshot;
score explanations come from the server's live scorer and version metadata.
Real-time graph updates arrive via the WebSocket proxy.

## File Structure

```
visual-editor/
├── backend/
│   ├── server.py            # FastAPI app: REST proxy + WS proxy + static serving
│   └── project_discovery.py # Server-owned projects; legacy history fallback
├── frontend/
│   ├── index.html
│   └── static/
│       ├── css/style.css
│       └── js/app.js        # D3 force-directed graph, three-panel UI, inline editing
├── requirements.txt
├── tests/                  # API integration and UI state regression tests
└── README.md                # this file
```

## Run

`kg editor` starts it detached (and the memory server first, if needed), logging to `~/.local/state/knowledge-graph/visual_editor.log`; `kg editor stop` stops it. From a checkout, `knowledge-graph/cli/kg-dev editor` runs the same code.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `EDITOR_PORT` | `8766` | Frontend + API port |
| `EDITOR_HOST` | `127.0.0.1` | Bind address |
| `MCP_SERVER_URL` | `http://127.0.0.1:8765` | Where to proxy REST requests; the WebSocket proxy always connects to `ws://127.0.0.1:8765/ws` |

If you change `EDITOR_PORT`, the frontend's WebSocket URL auto-derives from `window.location` so the page stays self-consistent. CORS in `server.py` allows only `http://localhost:8766` and `http://127.0.0.1:8766`, whatever `EDITOR_PORT` is — accessing the editor from another origin would need an entry there.

## API Endpoints (Backend)

All under `http://localhost:$EDITOR_PORT`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Serve SPA |
| GET | `/api/health` | Editor + MCP server status and canonical gist length target |
| GET | `/api/projects` | Stored memory projects from the server (legacy history fallback) |
| GET | `/api/graph` | Read graph (proxies to MCP `/api/graph/read`, served from the server's memory) |
| GET | `/api/search` | All-tier search of the selected graph; kg_search ranking and top-five connecting paths |
| GET | `/api/nodes/{level}/{id}/score` | Read-only archival score and factors from the live scorer |
| POST | `/api/nodes` | Create/update node |
| DELETE | `/api/nodes/{level}/{id}` | Delete node |
| GET | `/api/nodes/{level}/{id}` | Read single node (auto-promotes archived/orphaned) |
| POST | `/api/edges` | Create/update edge |
| DELETE | `/api/edges/{level}/{from}/{to}/{rel}` | Delete edge |
| WS | `/ws` | WebSocket proxy to MCP `/ws` for live updates |

## Limitations

- Desktop only — minimum 1366px screen width
- Edge creation requires typing target node ID (no click-to-connect)
- No undo, no multi-select
- Live updates cover the user graph only; project-graph changes need Refresh
- Score explanations require a memory server with the score endpoint; search
  works with older servers through the existing graph snapshot endpoint

## Verification

With a Python environment containing both server and editor requirements:

```bash
python tests/test_api.py
node tests/test_ui.mjs
```

The API tests run the editor against the real memory REST app with in-process
ASGI transports and temporary storage. The UI tests cover filters, async
responses, selection, view controls, escaping, score rendering, and saving
over-target gists with server-supplied character counters without
browser dependencies. Visual layout still needs a browser check. CI runs both.

## License

Same as parent project — see `../LICENSE`.
