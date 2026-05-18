# Knowledge Graph Visual Editor - User Guide

## Getting Started

### Starting the Editor

1. **Start MCP Server** (if not already running):
   ```bash
   kg-memory start
   ```

2. **Start Visual Editor**:
   ```bash
   kg-visual start
   ```

3. **Open in Browser**: `http://localhost:3000`

### Management Commands

```bash
kg-visual start     # Start (detached)
kg-visual stop      # Stop
kg-visual restart   # Stop + start
kg-visual status    # Check if running + URL
kg-visual logs      # Tail live logs
```

---

## Layout

The editor has three resizable panels:

```
┌──────────────┬─────────────────────────────┬──────────────────┐
│  GRAPHS      │         GRAPH               │   DETAILS        │
│  (left ~10%) │      (center, flex)         │  (right ~30%)    │
│              │                             │                  │
│  User Graph  │   D3 force-directed         │  Identity        │
│  ─────────── │   canvas                    │  Description     │
│  project-a   │                             │  Notes           │
│  project-b   │                             │  Files           │
│  project-c   │                             │  Connections     │
└──────────────┴─────────────────────────────┴──────────────────┘
```

Drag the thin divider bars between panels to resize them.

---

## Selecting a Graph

Click any entry in the **left panel**:

- **User Graph** — cross-project knowledge (preferences, patterns, principles)
- **Project entries** — project-specific knowledge; shows node/edge counts

The selected entry is highlighted with a blue left border. The header shows which graph is active.

---

## Node Interaction

### Viewing Details

**Click** any node to open its details in the right panel. The panel shows:

| Section | Contents |
|---|---|
| Identity | ID (read-only), status badges (level, archived, orphaned) |
| Description | Gist — the one-line summary |
| Notes | Detailed notes, one per entry |
| Files & Artifacts | Touches — related file paths |
| Connections | All edges: outgoing (→) and incoming (←) |

Click any peer name in **Connections** to jump selection to that node.

### Inline Editing

Hover over the **Description**, **Notes**, or **Files** section header — a pen icon (✎) appears. Click it to edit inline:

- **Gist**: Single-line textarea. Hard limit: **120 characters** (counter turns red if over; Save is blocked until under limit).
- **Notes**: Multi-line textarea, one note per line.
- **Touches**: Multi-line textarea, one file path per line.

Click **Save** to write the change immediately, or **Cancel** to discard.

> **Why ID and status are read-only**: Renaming a node ID would orphan all its edges (edges reference IDs directly). Status (archived/orphaned) is managed by the compaction scorer, not manual input.

### Context Menu (Right-Click)

Right-click any node:

- **Edit Node** — Full modal editor (all fields in one form)
- **Delete Node** — Removes node and all connected edges (permanent, no undo)
- **Recall** — Unarchive an archived node
- **Create Edge** — Start an edge from this node to another

---

## Creating Nodes

Click **+ New Node** in the graph toolbar:

- **Node ID**: kebab-case, e.g. `my-concept` (lowercase letters, digits, hyphens)
- **Gist**: One-line summary, ≤120 characters
- **Notes**: Optional, one per line
- **Touches**: Optional file paths, one per line

---

## Creating Edges

1. Right-click a node → **Create Edge**
2. Enter the **target node ID** and a **relationship label** (kebab-case, e.g. `depends-on`)
3. Optionally add notes
4. Click **Create**

Common relationship types: `depends-on`, `implements`, `extends`, `uses`, `instance-of`, `related-to`, `documents`, `fixes`.

---

## Navigation

| Action | How |
|---|---|
| Select node | Left-click |
| Pan | Click + drag on background |
| Zoom | Scroll wheel, or +/− buttons |
| Reset zoom | ⟲ button |
| Context menu | Right-click node |
| Move node (temp) | Drag node |

---

## Connection Status Indicator

The dot in the top-right corner shows the WebSocket state:

- **● Live** (green) — WebSocket connected; graph updates automatically when Claude writes memory in any terminal session. No need to press Refresh.
- **● Offline** (red) — WebSocket dropped; auto-reconnects every 5 seconds. Changes still save correctly — you just won't see them until reconnect or Refresh.
- **● Server down** (red) — MCP server unreachable; reads and writes will fail.

If you see persistent Offline/Server down: run `kg-memory status` and `kg-memory start` if needed.

---

## Node States

| Appearance | Meaning |
|---|---|
| Green filled | Active |
| Dark grey, dashed border, 50% opacity | Archived (infrequently used) |
| Hollow, dotted border, 60% opacity | Orphaned (no edges) |
| Gold ring | Selected |

Node size scales with connection count — hub nodes appear larger.

---

## Troubleshooting

**"Cannot connect to MCP server"**
```bash
kg-memory status
kg-memory start   # if not running
```

**Persistent Offline indicator**
```bash
kg-memory restart
```
Then reload the browser tab.

**Graph not loading / empty**
- Check you selected a graph in the left panel
- For project graphs: the project must have at least one node (use Claude to capture some first)
- Check logs: `kg-visual logs`

**Changes not appearing**
- Check the connection status indicator
- Press **Refresh** in the header
- If WebSocket is Live, changes from Claude sessions arrive automatically

**Modal won't close**
- Click the ✕ button, or **Cancel**, or click the dark overlay behind the modal

---

## Known Limitations

- **Edge creation**: Must type target node ID — no click-to-connect yet
- **No undo**: All operations are immediate and permanent
- **Single selection**: Cannot multi-select nodes
- **No search**: Browse the graph visually
- **Desktop only**: Minimum 1366px screen width required

---

## Minimum Requirements

- Screen width: 1366px+
- Modern browser with WebSocket support (Chrome, Firefox, Safari)
- MCP server running on localhost:8765
