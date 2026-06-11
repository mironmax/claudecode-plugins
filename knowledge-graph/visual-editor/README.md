# Knowledge Graph Visual Editor

A web-based D3.js graph editor for the knowledge graph. Runs as a separate FastAPI server that proxies to the MCP server (port 8765) and serves the SPA frontend.

For end-user usage, see:
- **[VISUAL_EDITOR_GUIDE.md](../VISUAL_EDITOR_GUIDE.md)** — feature tour: layout, node interaction, inline editing, troubleshooting
- **[Wiki: Visual Editor](https://github.com/mironmax/claudecode-plugins/wiki/Visual-Editor)** — same content, lives with the rest of the project docs

This README covers the codebase only.

## Architecture

```
Browser ─HTTP─► Visual Editor (FastAPI, port 3000)
                    │
                    ├─HTTP──► MCP Server REST API  (localhost:8765/api/*)
                    └─WS────► MCP Server WebSocket (localhost:8765/ws)
```

The visual editor stores no data. All reads and writes are proxied to the MCP server. Real-time graph updates arrive via the WebSocket proxy.

## File Structure

```
visual-editor/
├── backend/
│   ├── server.py            # FastAPI app: REST proxy + WS proxy + static serving
│   └── project_discovery.py # Scans ~/.claude/projects/ for /api/projects
├── frontend/
│   ├── index.html
│   └── static/
│       ├── css/style.css
│       └── js/app.js        # D3 force-directed graph, three-panel UI, inline editing
├── manage_visual.sh         # start | stop | restart | status | logs
├── requirements.txt
└── README.md                # this file
```

## Run

The bundled `manage_visual.sh` is the canonical entry point. End users get it as `kg-visual` after running `install_command.sh`. From the source tree:

```bash
./manage_visual.sh start    # daemonize, log to ~/.local/state/knowledge-graph/visual_editor.log
./manage_visual.sh status
./manage_visual.sh logs
./manage_visual.sh stop
```

Requires the MCP server (`kg-memory start`) to be up.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `EDITOR_PORT` | `3000` | Frontend + API port |
| `EDITOR_HOST` | `127.0.0.1` | Bind address |
| `MCP_SERVER_URL` | `http://127.0.0.1:8765` | Where to proxy |

If you change `EDITOR_PORT`, the frontend's WebSocket URL auto-derives from `window.location` so the page stays self-consistent. CORS in `server.py` is set up for same-origin only — exposing on a different port and accessing from another origin would need an entry there.

## API Endpoints (Backend)

All under `http://localhost:$EDITOR_PORT`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Serve SPA |
| GET | `/api/health` | Editor + MCP server status |
| GET | `/api/projects` | Discovered Claude Code projects |
| GET | `/api/graph` | Read graph (proxies to MCP `/api/graph/read?reload=true`) |
| POST | `/api/nodes` | Create/update node |
| DELETE | `/api/nodes/{level}/{id}` | Delete node |
| GET | `/api/nodes/{level}/{id}` | Read single node (auto-promotes archived/orphaned) |
| POST | `/api/edges` | Create/update edge |
| DELETE | `/api/edges/{level}/{from}/{to}/{rel}` | Delete edge |
| WS | `/ws` | WebSocket proxy to MCP `/ws` for live updates |

## Limitations

- Desktop only — minimum 1366px screen width
- Edge creation requires typing target node ID (no click-to-connect)
- No undo, no multi-select, no in-graph search

## License

Same as parent project — see `../LICENSE`.
