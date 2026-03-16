# Knowledge Graph Visual Editor - User Guide

## Getting Started

### Starting the Editor

1. **Start MCP Server** (if not already running):
   ```bash
   kg-memory start
   ```

2. **Start Visual Editor** (recommended):
   ```bash
   kg-visual start
   ```

   Or manually:
   ```bash
   cd knowledge-graph/visual-editor/backend
   ../venv/bin/python server.py
   ```

   The `manage_visual.sh` script (symlinked as `kg-visual`) handles starting, stopping, and checking status of the visual editor.

3. **Open in Browser**:
   Navigate to: `http://localhost:3000`

## Features

### Viewing Graphs

- **User Graph**: Shows your personal knowledge across all projects
- **Project Graph**: Shows knowledge for a specific project
- Switch between levels using the radio buttons in the header

### Interacting with Nodes

#### Viewing Node Details
- **Click** any node to view its details in the bottom panel
- Details include:
  - Node ID
  - Level (user/project)
  - Description (gist)
  - Notes
  - Files/artifacts (touches)
  - Status badges (archived/orphaned)

#### Context Menu (Right-Click)
Right-click any node to open the context menu:

- **✏️ Edit Node** - Modify node properties
- **🗑️ Delete Node** - Remove node and connected edges
- **↩️ Recall** - Unarchive an archived node
- **🔗 Create Edge** - Create relationship to another node

### Creating Nodes

1. Click the **"➕ New Node"** button in the header
2. Fill in the form:
   - **Node ID**: Unique identifier (use kebab-case like `my-node-id`)
   - **Description**: One-line summary of the concept
   - **Notes** (optional): Detailed notes, one per line
   - **Touches** (optional): Related files/artifacts, one per line
3. Click **"Create"**
4. Node appears in the graph immediately

### Editing Nodes

1. **Right-click** the node
2. Select **"✏️ Edit Node"**
3. Modify any fields (ID cannot be changed)
4. Click **"Update"**
5. Changes apply immediately

### Deleting Nodes

1. **Right-click** the node
2. Select **"🗑️ Delete Node"**
3. Confirm the deletion
4. Node and all connected edges are removed

⚠️ **Warning**: Deletion is permanent and cannot be undone!

### Creating Edges

Edges represent relationships between nodes (e.g., "depends-on", "implements", "extends").

1. **Right-click** the **source node**
2. Select **"🔗 Create Edge"**
3. Fill in the form:
   - **From Node**: Pre-filled (the node you right-clicked)
   - **To Node ID**: Enter the target node's ID
   - **Relationship**: Describe the relationship (kebab-case like `depends-on`)
   - **Notes** (optional): Additional context
4. Click **"Create"**
5. Edge appears connecting the nodes

### Recalling Archived Nodes

When nodes are automatically archived (due to inactivity), they appear grayed out:

1. **Right-click** an archived node
2. Select **"↩️ Recall"**
3. Node is restored to active status

## Real-Time Updates

The editor uses WebSocket for live updates:

- **Connection Status**: Green dot = connected, Red dot = disconnected
- **Toast Notifications**: Small pop-ups show operation results
- **Auto-Sync**: Changes from other sessions appear automatically
- **Auto-Reconnect**: Reconnects if connection is lost

## Navigation

### Zoom Controls
- **➕ Button**: Zoom in
- **➖ Button**: Zoom out
- **⟲ Button**: Reset zoom and position
- **Mouse Wheel**: Scroll to zoom
- **Click + Drag**: Pan the graph

### Node Interaction
- **Click**: Select and view details
- **Drag**: Move node position (temporary)
- **Right-Click**: Open context menu

## Tips & Tricks

### Naming Conventions
- Use **kebab-case** for IDs and relationships
- Good: `user-authentication`, `api-endpoint`
- Bad: `User_Authentication`, `ApiEndpoint`

### Node Organization
- Keep descriptions concise (1-2 sentences)
- Use notes for detailed explanations
- List all relevant files in touches

### Edge Relationships
Common relationship types:
- `depends-on` - Dependency
- `implements` - Implementation relationship
- `extends` - Inheritance
- `uses` - Usage relationship
- `related-to` - General association

### Performance
- The graph auto-compacts when it grows large
- Archived nodes are hidden by default
- Only active nodes count toward token limits

## Keyboard Shortcuts

Currently, the editor uses mouse/touch interaction. Keyboard shortcuts coming in future updates.

## Troubleshooting

### Cannot Connect
- Ensure MCP server is running: `kg-memory status`
- Check visual editor is running on port 3000
- Verify no firewall blocking localhost connections

### WebSocket Disconnected
- Red connection indicator means WebSocket is down
- Editor will auto-reconnect every 5 seconds
- Manual refresh: Click the "🔄 Refresh" button

### Changes Not Appearing
- Check connection status (green dot)
- Try manual refresh
- Verify you're viewing the correct graph level (user/project)

### Modal Won't Close
- Click the "✕" button in modal header
- Click "Cancel" button
- Click outside the modal (on the dark overlay)

## Known Limitations

1. **Edge Creation**: Must type target node ID (no visual selection yet)
2. **No Undo**: All operations are immediate and permanent
3. **Single Selection**: Cannot select multiple nodes at once
4. **Context Menu**: May clip at screen edges

## Best Practices

### For User-Level Graphs
Store cross-project patterns and learnings:
- Programming paradigms
- Architectural patterns
- Common workflows
- Tool preferences

### For Project-Level Graphs
Store project-specific knowledge:
- Component relationships
- API endpoints
- Data flows
- Implementation decisions

### Node Hygiene
- Review and update nodes periodically
- Delete obsolete nodes
- Archive rarely-used nodes (system does this automatically)
- Keep relationships current

## Getting Help

- Check server logs: `/tmp/mcp_server.log`
- Check editor logs: `/tmp/visual_editor.log`
- See `README.md` for server management and configuration

## Minimum Requirements

- **Screen Width**: 1366px minimum (desktop/laptop)
- **Browser**: Modern browser with WebSocket support
- **Network**: Localhost access (no internet required)

## Version Information

- **Editor Version**: 0.1.0
- **MCP Server**: 0.7.0+
- **Transport**: Streamable HTTP + WebSocket

---

Happy graph editing! 🎉
