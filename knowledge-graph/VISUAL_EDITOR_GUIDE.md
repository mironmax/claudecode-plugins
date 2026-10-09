# Knowledge Graph Visual Editor - User Guide

## Getting Started

### Starting the Editor

```bash
kg editor        # starts the memory server and the editor if needed, opens http://localhost:8766
kg editor stop
```

Logs: `~/.local/state/knowledge-graph/visual_editor.log` (in `port-<N>/` there
when the memory server uses another `KG_HTTP_PORT`).

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

Projects come from the memory server's stored graphs and their project-path
metadata, so projects used only by Codex appear too. A project whose directory
is missing is shown as unavailable. The editor falls back to Claude history
only when the server lacks the projects endpoint (an older server) or cannot
be reached.

---

## Node Interaction

### Viewing Details

**Click** any node to open its details in the right panel. The panel shows:

| Section | Contents |
|---|---|
| Identity | ID (read-only), status badges (level, archived, orphaned) |
| Description | Gist — the one-line summary |
| Archival score | Total score, factor ranks and contributions, expandable raw calculation |
| Notes | Detailed notes, one per entry |
| Files & Artifacts | Touches — related file paths |
| Connections | All edges: outgoing (→) and incoming (←) |

Click any peer name in **Connections** to jump selection to that node.

### Archival Score

The card uses the memory server's live scorer. Its three factors are
**recency (25%)**, **connectedness (40%)**, and **usefulness (35%)**. Each
factor is ranked within the eligible pool in this graph; equal raw values
share a percentile. The weighted contributions add up to a score from 0 to 1.
Higher scores stay longer, with archival and refill also depending on the
graph's context budget.

Expand **Raw values and calculation** to see the latest write, read and credit
time (recency is the latest of the three; an endorsement, a repeat or a
maintenance credit each count),
incoming and outgoing connection counts and weights, the hub floor, and
explicit endorsements with their 90-day decay half-life.

Active nodes show the score used for archival among eligible active nodes.
Archived nodes show the score used for refill among eligible active and
archived nodes. Nodes in the fresh tier (the newest work, up to 30% of the
level's budget) have a marked preview of their score once newer work pushes
them out; orphaned nodes have a preview as if recalled to active. The card names its comparison pool and never recalls a node.

### Inline Editing

Hover over the **Description**, **Notes**, or **Files** section header — a pen icon (✎) appears. Click it to edit inline:

- **Gist**: A textarea with a live character counter. The server's current target is **300 characters**. Longer gists remain editable and can be saved; the counter turns red and says “over target.” The target comes from the server, and character counts match its Unicode counting.
- **Notes**: Multi-line textarea, one note per line.
- **Touches**: Multi-line textarea, one file path per line.

Click **Save** to write the change immediately, or **Cancel** to discard.

> **ID and status are read-only in this form.** Use `kg_rename_node` to rename a node safely; it updates references. Archival and orphaning are managed by the memory server, with **Recall** available for archived nodes.

### Context Menu (Right-Click)

Right-click any node:

- **Edit Node** — Full modal editor (all fields in one form)
- **Delete Node** — Removes node and all connected edges (permanent, no undo)
- **Recall** — Unarchive an archived node
- **Create Edge** — Start an edge from this node to another

---

## Views and Search

The **Visible nodes** and **All nodes** choices stay above the canvas:

- **Visible nodes** is the default: active nodes and their immediate
  neighbors. Archived neighbors appear dimmed. **Include orphaned** adds every
  orphaned node to this view.
- **All nodes** shows every stored node, including archived and orphaned
  nodes. The footer reports how many nodes are shown; edges are counted as
  drawn, hidden, or dangling when an endpoint is unavailable in this graph.

Type in the search field, press **Search**, or use **Ctrl / ⌘ K** to focus it.
Search uses `kg_search`'s matching and ranking over IDs, descriptions, notes,
and file references. It searches every tier of the **selected graph**,
regardless of the current view.

The right panel lists matches in ranked order with matching text highlighted.
The canvas shows the top five numbered matches and their connecting paths.
**Show all matches** adds the remaining hits to the canvas. Select any result
to reveal it and inspect its details, including a node hidden by the default
view. Search, selection, view changes, and score inspection are read-only.

**Clear** or **Escape** in the search field returns to the previous graph
view. Choosing **Visible nodes** or **All nodes** also exits search. Switching
graphs clears the query so results always belong to the selected graph.

---

## Creating Nodes

Click **+ New Node** in the graph toolbar:

- **Node ID**: kebab-case, e.g. `my-concept` (lowercase letters, digits, hyphens)
- **Gist**: A concise summary, ideally within the server's 300-character target. The form warns above the target and still allows saving.
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
| Reset zoom | the Reset zoom button |
| Context menu | Right-click node |
| Move node (temp) | Drag node |

---

## Connection Status Indicator

The dot in the top-right corner shows the WebSocket state:

- **● Live** (green) — WebSocket connected; changes an agent makes to the **user** graph appear automatically. Changes to a **project** graph do not arrive live yet (a known gap: the editor's connection is not tied to a project) — press **Refresh** to see them.
- **● Offline** (red) — WebSocket dropped; auto-reconnects every 5 seconds. Changes still save correctly — you just won't see them until reconnect or Refresh.
- **● Server down** (red) — MCP server unreachable; reads and writes will fail.

If you see persistent Offline/Server down: run `kg status` and `kg start` if needed.

---

## Node States

| Appearance | Meaning |
|---|---|
| Green filled | Active |
| Dark grey, dashed border, 50% opacity | Archived (infrequently used) |
| Hollow, dotted border, 60% opacity | Orphaned (excluded from normal recall; may retain stored edges) |
| Gold ring | Selected |

Node size scales with connection count — hub nodes appear larger.

---

## Troubleshooting

**"Cannot connect to MCP server"**
```bash
kg status
kg start   # if not running
```

**Persistent Offline indicator**
```bash
kg restart
```
Then reload the browser tab.

**Graph not loading / empty**
- Check you selected a graph in the left panel
- For project graphs: capture some project memory first, then refresh. Check that the stored project directory still exists and that the editor can reach the server's `/api/projects` endpoint
- Check logs: `~/.local/state/knowledge-graph/visual_editor.log` (or `port-<N>/visual_editor.log`)

**Changes not appearing**
- Check the connection status indicator
- Press **Refresh** in the header
- If WebSocket is Live, user-graph changes from agent sessions arrive automatically; project-graph changes need **Refresh**

**Modal won't close**
- Press **Escape**, click the ✕ button or **Cancel**, or click the dark overlay behind the modal

---

## Known Limitations

- **Edge creation**: Must type target node ID — no click-to-connect yet
- **No undo**: All operations are immediate and permanent
- **Single selection**: Cannot multi-select nodes
- **Desktop only**: Minimum 1366px screen width required

---

## Minimum Requirements

- Screen width: 1366px+
- Modern browser with WebSocket support (Chrome, Firefox, Safari)
- MCP server running on localhost:8765
