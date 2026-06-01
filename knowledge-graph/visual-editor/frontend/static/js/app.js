/**
 * Knowledge Graph Visual Editor - Main Application
 *
 * D3.js force-directed graph visualization with CRUD operations
 */

// ============================================================================
// SVG Icon Templates
// ============================================================================

const ICONS = {
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
    pen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    recall: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>',
    link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
};

function icon(name, size = '') {
    const cls = size ? `icon icon-${size}` : 'icon';
    return `<span class="${cls}">${ICONS[name] || ''}</span>`;
}

// ============================================================================
// Configuration
// ============================================================================

const CONFIG = {
    apiBaseUrl: window.location.origin,
    mcpServerUrl: 'http://127.0.0.1:8765',
    refreshInterval: 30000,
    gistMaxLen: 120,
    simulation: {
        linkDistance: 120,
        linkStrength: 0.4,
        chargeStrength: -300,
        centerStrength: 0.3,
        collisionRadius: 45,
    },
    node: {
        radius: 8,
        radiusSelected: 12,
    },
};

// ============================================================================
// State Management
// ============================================================================

const state = {
    graphData: null,
    selectedNode: null,
    graphLevel: null,
    selectedProject: null,
    projects: [],
    simulation: null,
    zoom: null,
    sessionId: null,
    ws: null,
    contextNode: null,
    edgeCreationSource: null,
    // Track which field is currently being edited inline
    editingField: null,
};

// ============================================================================
// Utility Functions
// ============================================================================

function showElement(id) {
    document.getElementById(id)?.classList.remove('hidden');
}

function hideElement(id) {
    document.getElementById(id)?.classList.add('hidden');
}

function setConnectionStatus(status, text) {
    const statusDot = document.getElementById('connection-status');
    const statusText = document.getElementById('connection-text');
    statusDot.className = `status-dot status-${status}`;
    statusText.textContent = text;
}

function updateCurrentGraphLabel() {
    const label = document.getElementById('current-graph-label');
    if (!state.graphLevel) {
        label.innerHTML = 'No graph selected';
    } else if (state.graphLevel === 'user') {
        label.innerHTML = 'Viewing: <strong>User Graph</strong>';
    } else if (state.graphLevel === 'project' && state.selectedProject) {
        const proj = state.projects.find(p => p.project_path === state.selectedProject);
        const name = proj ? proj.display_name : state.selectedProject.split('/').pop();
        label.innerHTML = `Viewing: <strong>${escapeHtml(name)}</strong>`;
    } else {
        label.innerHTML = 'Select a project';
    }
}

function updateStats(nodeCount, edgeCount) {
    document.getElementById('node-count').textContent = `Nodes: ${nodeCount}`;
    document.getElementById('edge-count').textContent = `Edges: ${edgeCount}`;
}

function showError(message) {
    document.getElementById('error-message').textContent = message;
    hideElement('graph-loading');
    hideElement('graph-welcome');
    showElement('graph-error');
    setConnectionStatus('error', 'Disconnected');
}

// ============================================================================
// WebSocket Functions
// ============================================================================

function connectWebSocket() {
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProto}//${window.location.host}/ws`;
    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        setConnectionStatus('connected', 'Live');
    };

    state.ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        handleWebSocketMessage(message);
    };

    state.ws.onerror = () => {
        setConnectionStatus('error', 'Error');
    };

    state.ws.onclose = () => {
        setConnectionStatus('error', 'Offline');
        setTimeout(() => connectWebSocket(), 5000);
    };
}

function handleWebSocketMessage(message) {
    switch (message.type) {
        case 'connected':
            state.sessionId = message.session_id;
            break;
        case 'node_updated':
        case 'node_deleted':
        case 'edge_updated':
        case 'edge_deleted':
        case 'node_recalled':
            if (state.graphLevel && message.level === state.graphLevel) {
                loadGraph();
                showToast(formatUpdateMessage(message), 'success');
            }
            break;
    }
}

function formatUpdateMessage(message) {
    const actions = {
        'node_updated': `Node updated: ${message.node?.id}`,
        'node_deleted': `Node deleted: ${message.node_id}`,
        'edge_updated': `Edge updated: ${message.edge?.from} → ${message.edge?.to}`,
        'edge_deleted': `Edge deleted: ${message.from} → ${message.to}`,
        'node_recalled': `Node recalled: ${message.node?.id}`
    };
    return actions[message.type] || 'Graph updated';
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============================================================================
// API Functions
// ============================================================================

async function fetchProjects() {
    try {
        const response = await fetch(`${CONFIG.apiBaseUrl}/api/projects`);
        if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        return await response.json();
    } catch (error) {
        console.error('Error fetching projects:', error);
        return [];
    }
}

async function fetchGraphData() {
    try {
        let params = '';
        if (state.graphLevel === 'project' && state.selectedProject) {
            params = `?project_path=${encodeURIComponent(state.selectedProject)}`;
        }

        const response = await fetch(`${CONFIG.apiBaseUrl}/api/graph${params}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        return await response.json();
    } catch (error) {
        console.error('Error fetching graph data:', error);
        throw error;
    }
}

async function checkHealth() {
    try {
        const response = await fetch(`${CONFIG.apiBaseUrl}/api/health`);
        const health = await response.json();

        if (health.status === 'ok' && health.mcp_server?.status === 'ok') {
            setConnectionStatus('connected', 'Connected');
            return true;
        } else {
            setConnectionStatus('error', 'Server down');
            return false;
        }
    } catch (error) {
        setConnectionStatus('error', 'Unreachable');
        return false;
    }
}

// ============================================================================
// Project Selector Panel
// ============================================================================

async function loadProjects() {
    const entriesEl = document.getElementById('project-entries');
    const loadingEl = document.getElementById('project-loading');

    try {
        const projects = await fetchProjects();
        state.projects = projects;

        loadingEl.classList.add('hidden');
        entriesEl.innerHTML = '';

        projects.forEach(project => {
            const item = document.createElement('div');
            item.className = 'project-item';
            item.dataset.path = project.project_path;

            let meta = '';
            if (project.has_graph && project.node_count !== null) {
                meta = `${project.node_count}N · ${project.edge_count}E`;
            } else {
                meta = 'no graph';
            }

            item.innerHTML = `
                <span class="project-item-icon">${ICONS.folder}</span>
                <div class="project-item-info">
                    <div class="project-item-name" title="${escapeHtml(project.project_path)}">${escapeHtml(project.display_name)}</div>
                    <div class="project-item-meta">${escapeHtml(meta)}</div>
                </div>
            `;

            item.addEventListener('click', () => selectProject(project.project_path));
            entriesEl.appendChild(item);
        });

        if (projects.length === 0) {
            entriesEl.innerHTML = '<div class="project-section-label" style="padding-top:0.5rem;opacity:0.4;">No projects found</div>';
        }
    } catch (error) {
        console.error('Failed to load projects:', error);
        loadingEl.classList.add('hidden');
    }
}

function selectUserGraph() {
    // Deactivate all project items
    document.querySelectorAll('.project-item').forEach(el => el.classList.remove('active'));
    document.getElementById('project-item-user').classList.add('active');

    state.graphLevel = 'user';
    state.selectedProject = null;
    loadGraph();
}

function selectProject(projectPath) {
    document.querySelectorAll('.project-item').forEach(el => el.classList.remove('active'));
    const item = document.querySelector(`.project-item[data-path="${CSS.escape(projectPath)}"]`);
    if (item) item.classList.add('active');

    state.graphLevel = 'project';
    state.selectedProject = projectPath;
    loadGraph();
}

// ============================================================================
// Resize Handles
// ============================================================================

function initResizeHandles() {
    setupResizeHandle('resize-project', 'project-panel', 'left', '--project-panel-width', 140, 320);
    setupResizeHandle('resize-detail', 'detail-panel', 'right', '--detail-panel-width', 240, 600);
}

function setupResizeHandle(handleId, panelId, side, cssVar, minPx, maxPx) {
    const handle = document.getElementById(handleId);
    const panel = document.getElementById(panelId);
    if (!handle || !panel) return;

    let startX = 0;
    let startWidth = 0;

    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        startX = e.clientX;
        startWidth = panel.getBoundingClientRect().width;
        handle.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';

        function onMove(e) {
            let delta = e.clientX - startX;
            if (side === 'right') delta = -delta;
            const newWidth = Math.min(maxPx, Math.max(minPx, startWidth + delta));
            document.documentElement.style.setProperty(cssVar, `${newWidth}px`);
        }

        function onUp() {
            handle.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            // Restart simulation so graph fills new space
            if (state.simulation) state.simulation.alpha(0.3).restart();
        }

        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
}

// ============================================================================
// Data Transformation
// ============================================================================

function transformGraphData(rawData) {
    const nodes = [];
    const links = [];

    if (rawData.user?.nodes) {
        Object.values(rawData.user.nodes).forEach(node => {
            nodes.push({
                ...node,
                level: 'user',
                archived: node._archived || false,
                orphaned: node._orphaned_ts != null,
            });
        });
    }

    if (rawData.project?.nodes) {
        Object.values(rawData.project.nodes).forEach(node => {
            nodes.push({
                ...node,
                level: 'project',
                archived: node._archived || false,
                orphaned: node._orphaned_ts != null,
            });
        });
    }

    const nodeIds = new Set(nodes.map(n => n.id));

    if (rawData.user?.edges) {
        Object.values(rawData.user.edges).forEach(edge => {
            if (!nodeIds.has(edge.from) || !nodeIds.has(edge.to)) return;
            links.push({ ...edge, source: edge.from, target: edge.to, level: 'user' });
        });
    }

    if (rawData.project?.edges) {
        Object.values(rawData.project.edges).forEach(edge => {
            if (!nodeIds.has(edge.from) || !nodeIds.has(edge.to)) return;
            links.push({ ...edge, source: edge.from, target: edge.to, level: 'project' });
        });
    }

    return { nodes, links };
}

function applyLevelFilter(data, graphLevel) {
    if (!graphLevel) return { nodes: [], links: [] };

    const filteredNodes = data.nodes.filter(n => n.level === graphLevel);
    const nodeIds = new Set(filteredNodes.map(n => n.id));
    const filteredLinks = data.links.filter(
        l => nodeIds.has(l.source.id || l.source) && nodeIds.has(l.target.id || l.target)
    );

    return { nodes: filteredNodes, links: filteredLinks };
}

// ============================================================================
// Modal System
// ============================================================================

function openModal(title, content, actions) {
    const overlay = document.getElementById('modal-overlay');
    const container = document.getElementById('modal-container');

    container.innerHTML = `
        <div class="modal-header">
            <h3>${title}</h3>
            <button class="modal-close" onclick="closeModal()">${icon('close')}</button>
        </div>
        <div class="modal-body">${content}</div>
        <div class="modal-footer">${actions}</div>
    `;
    overlay.classList.remove('hidden');
}

function closeModal() {
    document.getElementById('modal-overlay').classList.add('hidden');
}

function openEditNodeModal(node = null) {
    const isEdit = node !== null;
    const title = isEdit ? `Edit Node: ${node.id}` : 'Create New Node';

    const content = `
        <form id="node-form">
            <div class="form-group">
                <label>Node ID</label>
                <input type="text" id="node-id" value="${isEdit ? escapeHtml(node.id) : ''}"
                       ${isEdit ? 'readonly' : ''} required placeholder="kebab-case-id">
            </div>
            <div class="form-group">
                <label>Description (Gist)</label>
                <textarea id="node-gist" rows="3" required>${isEdit ? escapeHtml(node.gist) : ''}</textarea>
            </div>
            <div class="form-group">
                <label>Notes (one per line)</label>
                <textarea id="node-notes" rows="5">${isEdit && node.notes ? node.notes.map(escapeHtml).join('\n') : ''}</textarea>
            </div>
            <div class="form-group">
                <label>Touches (files, one per line)</label>
                <textarea id="node-touches" rows="3">${isEdit && node.touches ? node.touches.map(escapeHtml).join('\n') : ''}</textarea>
            </div>
        </form>
    `;

    const actions = `
        <button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="submitNodeForm(${isEdit})">
            ${isEdit ? 'Update' : 'Create'}
        </button>
    `;

    openModal(title, content, actions);
}

async function submitNodeForm(isEdit) {
    const id = document.getElementById('node-id').value.trim();
    const gist = document.getElementById('node-gist').value.trim();
    const notesText = document.getElementById('node-notes').value.trim();
    const touchesText = document.getElementById('node-touches').value.trim();

    if (!id || !gist) {
        showToast('ID and Description required', 'error');
        return;
    }

    if (gist.length > CONFIG.gistMaxLen) {
        showToast(`Gist must be ≤${CONFIG.gistMaxLen} characters`, 'error');
        return;
    }

    if (!validateNodeId(id)) {
        showToast('Node ID must be lowercase kebab-case (letters, digits, hyphens)', 'error');
        return;
    }

    try {
        const response = await fetch(`${CONFIG.apiBaseUrl}/api/nodes`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                level: state.graphLevel,
                id: id,
                gist: gist,
                notes: notesText ? notesText.split('\n').filter(n => n.trim()) : null,
                touches: touchesText ? touchesText.split('\n').filter(t => t.trim()) : null,
                session_id: state.sessionId
            })
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        showToast(`Node ${isEdit ? 'updated' : 'created'}`, 'success');
        closeModal();
        await loadGraph();
    } catch (error) {
        showToast(`Failed: ${error.message}`, 'error');
    }
}

function validateNodeId(id) {
    return /^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/.test(id);
}

function startEdgeCreation(fromNode) {
    state.edgeCreationSource = fromNode;

    const content = `
        <form id="edge-form">
            <div class="form-group">
                <label>From Node</label>
                <input type="text" value="${escapeHtml(fromNode.id)}" readonly>
            </div>
            <div class="form-group">
                <label>To Node ID</label>
                <input type="text" id="edge-to" required placeholder="target-node-id">
            </div>
            <div class="form-group">
                <label>Relationship</label>
                <input type="text" id="edge-rel" required placeholder="kebab-case-rel">
            </div>
            <div class="form-group">
                <label>Notes (optional)</label>
                <textarea id="edge-notes" rows="3"></textarea>
            </div>
        </form>
    `;

    openModal('Create Edge', content, `
        <button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="submitEdgeForm()">Create</button>
    `);
}

async function submitEdgeForm() {
    const to = document.getElementById('edge-to').value.trim();
    const rel = document.getElementById('edge-rel').value.trim();
    const notesText = document.getElementById('edge-notes').value.trim();

    if (!to || !rel) {
        showToast('To Node and Relationship required', 'error');
        return;
    }

    try {
        await fetch(`${CONFIG.apiBaseUrl}/api/edges`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                level: state.graphLevel,
                from: state.edgeCreationSource.id,
                to: to,
                rel: rel,
                notes: notesText ? notesText.split('\n').filter(n => n.trim()) : null,
                session_id: state.sessionId
            })
        });

        showToast('Edge created', 'success');
        closeModal();
        await loadGraph();
    } catch (error) {
        showToast(`Failed: ${error.message}`, 'error');
    }
}

function confirmDeleteNode(node) {
    openModal('Confirm Deletion', `
        <p>Delete node <strong>${escapeHtml(node.id)}</strong>?</p>
        <p style="color: var(--warning-color); margin-top: 0.5rem;">Connected edges will also be deleted.</p>
    `, `
        <button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn btn-danger" onclick="deleteNode('${escapeHtml(node.id)}')">Delete</button>
    `);
}

// Build the query string for single-node API calls (read/recall/delete).
// A project node must be resolved by project_path: the editor's WebSocket session
// is not registered against any project, so session_id alone can't find it on the
// MCP server. The graph load uses the same project_path mechanism.
function nodeApiQuery() {
    const params = new URLSearchParams();
    if (state.sessionId) params.set('session_id', state.sessionId);
    if (state.graphLevel === 'project' && state.selectedProject) {
        params.set('project_path', state.selectedProject);
    }
    const qs = params.toString();
    return qs ? `?${qs}` : '';
}

async function deleteNode(nodeId) {
    try {
        const response = await fetch(
            `${CONFIG.apiBaseUrl}/api/nodes/${state.graphLevel}/${encodeURIComponent(nodeId)}${nodeApiQuery()}`,
            {method: 'DELETE'}
        );
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        showToast('Node deleted', 'success');
        closeModal();
        state.selectedNode = null;
        showDetailEmpty();
        await loadGraph();
    } catch (error) {
        showToast(`Failed: ${error.message}`, 'error');
    }
}

async function recallNode(node) {
    try {
        // Reading a node via the REST API auto-promotes it from archived/orphaned to active
        // (same path the MCP kg_read(cwd, id) tool uses).
        const response = await fetch(
            `${CONFIG.apiBaseUrl}/api/nodes/${state.graphLevel}/${encodeURIComponent(node.id)}${nodeApiQuery()}`
        );
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        showToast('Node recalled', 'success');
        await loadGraph();
    } catch (error) {
        showToast(`Failed: ${error.message}`, 'error');
    }
}

// ============================================================================
// Inline Field Editing
// ============================================================================

// field: 'gist' | 'notes' | 'touches'
function startInlineEdit(field) {
    if (state.editingField === field) return;
    state.editingField = field;

    const node = state.selectedNode;
    if (!node) return;

    // Re-render so the target field becomes an editor
    renderNodeDetails(node);
}

function cancelInlineEdit() {
    state.editingField = null;
    if (state.selectedNode) renderNodeDetails(state.selectedNode);
}

async function saveInlineEdit(field) {
    const node = state.selectedNode;
    if (!node) return;

    let gist = node.gist;
    let notes = node.notes ? [...node.notes] : null;
    let touches = node.touches ? [...node.touches] : null;

    if (field === 'gist') {
        const val = document.getElementById('inline-gist')?.value.trim();
        if (!val) { showToast('Gist cannot be empty', 'error'); return; }
        if (val.length > CONFIG.gistMaxLen) {
            showToast(`Gist must be ≤${CONFIG.gistMaxLen} characters`, 'error');
            return;
        }
        gist = val;
    } else if (field === 'notes') {
        const raw = document.getElementById('inline-notes')?.value ?? '';
        notes = raw.split('\n').map(l => l.trim()).filter(l => l.length > 0);
        if (notes.length === 0) notes = null;
    } else if (field === 'touches') {
        const raw = document.getElementById('inline-touches')?.value ?? '';
        touches = raw.split('\n').map(l => l.trim()).filter(l => l.length > 0);
        if (touches.length === 0) touches = null;
    }

    try {
        const response = await fetch(`${CONFIG.apiBaseUrl}/api/nodes`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                level: state.graphLevel,
                id: node.id,
                gist,
                notes,
                touches,
                session_id: state.sessionId
            })
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        // Optimistically update the in-memory node so re-render looks right immediately
        node.gist = gist;
        node.notes = notes;
        node.touches = touches;

        showToast('Saved', 'success');
        state.editingField = null;
        renderNodeDetails(node);

        // Reload graph in background to sync labels etc.
        loadGraph();
    } catch (error) {
        showToast(`Failed: ${error.message}`, 'error');
    }
}

// ============================================================================
// Context Menu
// ============================================================================

let contextMenu = null;

function createContextMenu() {
    const menu = document.createElement('div');
    menu.id = 'context-menu';
    menu.className = 'context-menu hidden';
    menu.innerHTML = `
        <div class="context-menu-item" data-action="edit">${icon('edit')} Edit Node</div>
        <div class="context-menu-item" data-action="delete">${icon('trash')} Delete Node</div>
        <div class="context-menu-item" data-action="recall">${icon('recall')} Recall</div>
        <div class="context-menu-divider"></div>
        <div class="context-menu-item" data-action="create-edge">${icon('link')} Create Edge</div>
    `;
    document.body.appendChild(menu);

    menu.addEventListener('click', (e) => {
        const item = e.target.closest('[data-action]');
        if (item) {
            handleContextMenuAction(item.dataset.action);
            hideContextMenu();
        }
    });

    return menu;
}

function showContextMenu(x, y, node) {
    if (!contextMenu) contextMenu = createContextMenu();
    state.contextNode = node;
    contextMenu.style.left = `${x}px`;
    contextMenu.style.top = `${y}px`;
    contextMenu.classList.remove('hidden');
}

function hideContextMenu() {
    if (contextMenu) contextMenu.classList.add('hidden');
}

function handleContextMenuAction(action) {
    const node = state.contextNode;
    if (!node) return;

    switch (action) {
        case 'edit': openEditNodeModal(node); break;
        case 'delete': confirmDeleteNode(node); break;
        case 'recall':
            // Both archived and orphaned nodes are recallable (read promotes either
            // back to active). Active nodes have nothing to recall.
            if (node.archived || node.orphaned) recallNode(node);
            else showToast('Node is already active', 'info');
            break;
        case 'create-edge': startEdgeCreation(node); break;
    }
}

document.addEventListener('click', () => hideContextMenu());

// ============================================================================
// D3.js Visualization
// ============================================================================

function initializeGraph() {
    const svg = d3.select('#graph-svg');
    const container = svg.append('g');

    state.zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => {
            container.attr('transform', event.transform);
        });

    svg.call(state.zoom);

    const width = document.getElementById('graph-container').clientWidth;
    const height = document.getElementById('graph-container').clientHeight;

    state.simulation = d3.forceSimulation()
        .force('link', d3.forceLink().id(d => d.id).distance(CONFIG.simulation.linkDistance).strength(CONFIG.simulation.linkStrength))
        .force('charge', d3.forceManyBody().strength(CONFIG.simulation.chargeStrength))
        .force('center', d3.forceCenter(width / 2, height / 2).strength(CONFIG.simulation.centerStrength))
        .force('collision', d3.forceCollide().radius(CONFIG.simulation.collisionRadius))
        .force('x', d3.forceX(width / 2).strength(0.05))
        .force('y', d3.forceY(height / 2).strength(0.05));

    return { svg, container };
}

function renderGraph(graphData) {
    const { svg, container } = state.svgElements || initializeGraph();

    if (!state.svgElements) {
        state.svgElements = { svg, container };
    }

    container.selectAll('*').remove();

    const filteredData = applyLevelFilter(graphData, state.graphLevel);

    updateStats(filteredData.nodes.length, filteredData.links.length);

    if (filteredData.nodes.length === 0) {
        showEmptyState('No nodes to display');
        return;
    }

    const degreeMap = {};
    filteredData.nodes.forEach(n => degreeMap[n.id] = 0);
    filteredData.links.forEach(l => {
        const src = l.source.id || l.source;
        const tgt = l.target.id || l.target;
        if (degreeMap[src] !== undefined) degreeMap[src]++;
        if (degreeMap[tgt] !== undefined) degreeMap[tgt]++;
    });
    const maxDegree = Math.max(1, ...Object.values(degreeMap));

    filteredData.nodes.forEach(n => {
        const degree = degreeMap[n.id] || 0;
        n._radius = CONFIG.node.radius * (1 + 0.5 * Math.sqrt(degree / maxDegree));
        const gistLen = (n.gist || '').length;
        const notesLen = (n.notes || []).reduce((sum, note) => sum + note.length, 0);
        n._contentWeight = Math.min(gistLen + notesLen, 1000);
    });
    const maxContent = Math.max(1, ...filteredData.nodes.map(n => n._contentWeight));

    const link = container.append('g')
        .selectAll('line')
        .data(filteredData.links)
        .enter()
        .append('line')
        .attr('class', 'link')
        .attr('stroke-width', 1.5);

    const linkLabel = container.append('g')
        .selectAll('text')
        .data(filteredData.links)
        .enter()
        .append('text')
        .attr('class', 'link-label')
        .text(d => d.rel);

    const node = container.append('g')
        .selectAll('circle')
        .data(filteredData.nodes)
        .enter()
        .append('circle')
        .attr('class', d => {
            const classes = ['node', `node-${d.level}`];
            if (d.archived) classes.push('node-archived');
            if (d.orphaned) classes.push('node-orphan');
            return classes.join(' ');
        })
        .attr('r', d => d._radius)
        .on('click', (event, d) => handleNodeClick(event, d))
        .on('contextmenu', (event, d) => {
            event.preventDefault();
            showContextMenu(event.pageX, event.pageY, d);
        })
        .call(d3.drag()
            .on('start', dragStarted)
            .on('drag', dragged)
            .on('end', dragEnded));

    const nodeLabel = container.append('g')
        .selectAll('text')
        .data(filteredData.nodes)
        .enter()
        .append('text')
        .attr('class', d => {
            let cls = 'node-label';
            if (d.archived) cls += ' node-label-archived';
            if (d.orphaned) cls += ' node-label-orphan';
            return cls;
        })
        .attr('dy', d => -(d._radius + 6))
        .text(d => truncateText(d.id, 20));

    state.simulation.force('charge', d3.forceManyBody().strength(d => {
        const contentRatio = d._contentWeight / maxContent;
        return CONFIG.simulation.chargeStrength * (1 + contentRatio);
    }));
    state.simulation.force('collision', d3.forceCollide().radius(d => d._radius + 4));

    state.simulation
        .nodes(filteredData.nodes)
        .on('tick', () => {
            link
                .attr('x1', d => d.source.x)
                .attr('y1', d => d.source.y)
                .attr('x2', d => d.target.x)
                .attr('y2', d => d.target.y);

            linkLabel
                .attr('x', d => (d.source.x + d.target.x) / 2)
                .attr('y', d => (d.source.y + d.target.y) / 2);

            node
                .attr('cx', d => d.x)
                .attr('cy', d => d.y);

            nodeLabel
                .attr('x', d => d.x)
                .attr('y', d => d.y);
        });

    state.simulation.force('link').links(filteredData.links);
    state.simulation.alpha(1).restart();
}

function showEmptyState(message) {
    const container = state.svgElements?.container;
    if (!container) return;

    container.selectAll('*').remove();

    const width = document.getElementById('graph-container').clientWidth;
    const height = document.getElementById('graph-container').clientHeight;

    container.append('text')
        .attr('x', width / 2)
        .attr('y', height / 2)
        .attr('text-anchor', 'middle')
        .style('fill', 'var(--text-secondary)')
        .style('font-size', '0.9375rem')
        .text(message);
}

// ============================================================================
// Connections Section Builder
// ============================================================================

function buildConnectionsSection(node) {
    if (!state.graphData) return '';

    const links = state.graphData.links.filter(l => {
        const src = l.source.id || l.source;
        const tgt = l.target.id || l.target;
        return (src === node.id || tgt === node.id) && l.level === node.level;
    });

    if (links.length === 0) return '';

    const outgoing = links.filter(l => (l.source.id || l.source) === node.id);
    const incoming = links.filter(l => (l.target.id || l.target) === node.id);

    function linkRow(l, direction) {
        const other = direction === 'out'
            ? (l.target.id || l.target)
            : (l.source.id || l.source);
        const arrow = direction === 'out' ? '→' : '←';
        return `<li class="conn-row">
            <span class="conn-arrow">${arrow}</span>
            <span class="conn-rel">${escapeHtml(l.rel)}</span>
            <span class="conn-peer" onclick="selectNodeById('${escapeHtml(other)}')" title="Click to select">${escapeHtml(other)}</span>
        </li>`;
    }

    const rows = [
        ...outgoing.map(l => linkRow(l, 'out')),
        ...incoming.map(l => linkRow(l, 'in')),
    ].join('');

    return `
        <div class="detail-section">
            <h3>Connections <span class="conn-count">${links.length}</span></h3>
            <ul class="detail-list conn-list">${rows}</ul>
        </div>
    `;
}

function selectNodeById(nodeId) {
    if (!state.graphData) return;
    const node = state.graphData.nodes.find(n => n.id === nodeId && n.level === state.graphLevel);
    if (!node) return;
    state.selectedNode = node;
    state.editingField = null;
    d3.selectAll('.node').classed('selected', false);
    d3.selectAll('.node').filter(d => d.id === nodeId).classed('selected', true);
    renderNodeDetails(node);
}

// ============================================================================
// Event Handlers
// ============================================================================

function handleNodeClick(event, node) {
    d3.selectAll('.node').classed('selected', false);
    d3.select(event.target).classed('selected', true);

    state.selectedNode = node;
    state.editingField = null;
    renderNodeDetails(node);
}

function showDetailEmpty() {
    document.getElementById('detail-content').innerHTML = `
        <div class="empty-state">
            <span class="icon icon-xl">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5"/></svg>
            </span>
            <p>Click a node to view details</p>
        </div>
    `;
}

function renderNodeDetails(node) {
    const container = document.getElementById('detail-content');
    const ef = state.editingField;

    // ---- Gist field ----
    const gistHtml = ef === 'gist'
        ? `<div class="inline-edit-wrap">
               <textarea id="inline-gist" rows="3" maxlength="${CONFIG.gistMaxLen}">${escapeHtml(node.gist)}</textarea>
               <div class="inline-edit-meta">
                   <span class="char-counter" id="gist-counter">${node.gist.length}/${CONFIG.gistMaxLen}</span>
                   <div class="inline-edit-actions">
                       <button class="btn btn-xs" onclick="cancelInlineEdit()">Cancel</button>
                       <button class="btn btn-xs btn-primary" onclick="saveInlineEdit('gist')">${icon('check','sm')} Save</button>
                   </div>
               </div>
           </div>`
        : `<div class="detail-value">${escapeHtml(node.gist)}</div>`;

    const gistEditBtn = ef === 'gist' ? '' :
        `<button class="edit-btn" onclick="startInlineEdit('gist')" title="Edit gist">
             <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
         </button>`;

    // ---- Notes field ----
    const notesRaw = node.notes ? node.notes.join('\n') : '';
    const notesHtml = ef === 'notes'
        ? `<div class="inline-edit-wrap">
               <textarea id="inline-notes" rows="6" placeholder="One note per line">${escapeHtml(notesRaw)}</textarea>
               <div class="inline-edit-meta">
                   <span></span>
                   <div class="inline-edit-actions">
                       <button class="btn btn-xs" onclick="cancelInlineEdit()">Cancel</button>
                       <button class="btn btn-xs btn-primary" onclick="saveInlineEdit('notes')">${icon('check','sm')} Save</button>
                   </div>
               </div>
           </div>`
        : (node.notes && node.notes.length > 0
            ? `<ul class="detail-list">${node.notes.map(n => `<li>${escapeHtml(n)}</li>`).join('')}</ul>`
            : `<div class="detail-value readonly" style="font-style:italic;opacity:0.5">No notes</div>`);

    const notesEditBtn = ef === 'notes' ? '' :
        `<button class="edit-btn" onclick="startInlineEdit('notes')" title="Edit notes">
             <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
         </button>`;

    // ---- Touches field ----
    const touchesRaw = node.touches ? node.touches.join('\n') : '';
    const touchesHtml = ef === 'touches'
        ? `<div class="inline-edit-wrap">
               <textarea id="inline-touches" rows="4" placeholder="One file path per line">${escapeHtml(touchesRaw)}</textarea>
               <div class="inline-edit-meta">
                   <span></span>
                   <div class="inline-edit-actions">
                       <button class="btn btn-xs" onclick="cancelInlineEdit()">Cancel</button>
                       <button class="btn btn-xs btn-primary" onclick="saveInlineEdit('touches')">${icon('check','sm')} Save</button>
                   </div>
               </div>
           </div>`
        : (node.touches && node.touches.length > 0
            ? `<ul class="detail-list">${node.touches.map(f => `<li><code>${escapeHtml(f)}</code></li>`).join('')}</ul>`
            : `<div class="detail-value readonly" style="font-style:italic;opacity:0.5">No files</div>`);

    const touchesEditBtn = ef === 'touches' ? '' :
        `<button class="edit-btn" onclick="startInlineEdit('touches')" title="Edit files">
             <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
         </button>`;

    container.innerHTML = `
        <div class="node-detail">
            <div class="detail-section">
                <h3>Identity</h3>
                <div class="detail-field">
                    <div class="detail-field-header">
                        <span class="detail-label">ID</span>
                    </div>
                    <div class="detail-value"><code>${escapeHtml(node.id)}</code></div>
                </div>
                <div class="detail-field">
                    <div class="detail-field-header">
                        <span class="detail-label">Status</span>
                    </div>
                    <div class="detail-value">
                        <span class="badge badge-${node.level}">${node.level}</span>
                        ${node.archived ? '<span class="badge badge-archived" style="margin-left:0.25rem">Archived</span>' : ''}
                        ${node.orphaned ? '<span class="badge badge-orphaned" style="margin-left:0.25rem">Orphaned</span>' : ''}
                    </div>
                </div>
            </div>

            <div class="detail-section">
                <h3>Description</h3>
                <div class="detail-field">
                    <div class="detail-field-header">
                        <span class="detail-label">Gist</span>
                        ${gistEditBtn}
                    </div>
                    ${gistHtml}
                </div>
            </div>

            <div class="detail-section">
                <h3>Notes</h3>
                <div class="detail-field">
                    <div class="detail-field-header">
                        <span class="detail-label">Entries</span>
                        ${notesEditBtn}
                    </div>
                    ${notesHtml}
                </div>
            </div>

            <div class="detail-section">
                <h3>Files &amp; Artifacts</h3>
                <div class="detail-field">
                    <div class="detail-field-header">
                        <span class="detail-label">Touches</span>
                        ${touchesEditBtn}
                    </div>
                    ${touchesHtml}
                </div>
            </div>

            ${buildConnectionsSection(node)}
        </div>
    `;

    // Wire up live char counter for gist
    if (ef === 'gist') {
        const ta = document.getElementById('inline-gist');
        const counter = document.getElementById('gist-counter');
        if (ta && counter) {
            const updateCounter = () => {
                const len = ta.value.length;
                counter.textContent = `${len}/${CONFIG.gistMaxLen}`;
                counter.classList.toggle('over-limit', len > CONFIG.gistMaxLen);
            };
            ta.addEventListener('input', updateCounter);
            updateCounter(); // apply immediately so existing over-limit values show red
            ta.focus();
            ta.setSelectionRange(ta.value.length, ta.value.length);
        }
    } else if (ef === 'notes') {
        document.getElementById('inline-notes')?.focus();
    } else if (ef === 'touches') {
        document.getElementById('inline-touches')?.focus();
    }
}

function dragStarted(event, d) {
    if (!event.active) state.simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
}

function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
}

function dragEnded(event, d) {
    if (!event.active) state.simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
}

// ============================================================================
// Main Application Logic
// ============================================================================

async function loadGraph() {
    if (!state.graphLevel) return;
    if (state.graphLevel === 'project' && !state.selectedProject) return;

    try {
        hideElement('graph-error');
        hideElement('graph-welcome');
        showElement('graph-loading');

        const rawData = await fetchGraphData();
        state.graphData = transformGraphData(rawData);

        renderGraph(state.graphData);

        hideElement('graph-loading');
        updateCurrentGraphLabel();

        // If a node was selected before reload, try to keep it selected
        if (state.selectedNode) {
            const refreshed = state.graphData.nodes.find(n => n.id === state.selectedNode.id && n.level === state.selectedNode.level);
            if (refreshed) {
                state.selectedNode = refreshed;
                if (!state.editingField) renderNodeDetails(refreshed);
            }
        }
    } catch (error) {
        console.error('Failed to load graph:', error);
        showError(`Failed to load graph: ${error.message}`);
    }
}

function showWelcome() {
    if (state.svgElements?.container) {
        state.svgElements.container.selectAll('*').remove();
    }
    hideElement('graph-loading');
    hideElement('graph-error');
    showElement('graph-welcome');
    updateStats(0, 0);
    updateCurrentGraphLabel();
}

async function initialize() {
    console.log('Initializing Knowledge Graph Visual Editor...');

    const healthy = await checkHealth();
    if (!healthy) {
        showError('Cannot connect to MCP server. Please ensure the server is running.');
        return;
    }

    connectWebSocket();

    await loadProjects();

    // Wire up project panel user-graph click
    document.getElementById('project-item-user').addEventListener('click', selectUserGraph);

    hideElement('graph-loading');
    showWelcome();

    // Header controls
    document.getElementById('refresh-btn').addEventListener('click', () => {
        if (state.graphLevel) loadGraph();
    });
    document.getElementById('retry-btn').addEventListener('click', loadGraph);
    document.getElementById('create-node-btn').addEventListener('click', () => {
        if (!state.graphLevel) {
            showToast('Select a graph first', 'warning');
            return;
        }
        openEditNodeModal();
    });

    document.getElementById('zoom-in-btn').addEventListener('click', () => {
        state.svgElements?.svg.transition().call(state.zoom.scaleBy, 1.3);
    });
    document.getElementById('zoom-out-btn').addEventListener('click', () => {
        state.svgElements?.svg.transition().call(state.zoom.scaleBy, 0.7);
    });
    document.getElementById('zoom-reset-btn').addEventListener('click', () => {
        state.svgElements?.svg.transition().call(state.zoom.transform, d3.zoomIdentity);
    });

    // Resize handles
    initResizeHandles();

    // Mobile detection
    updateScreenSize();
    window.addEventListener('resize', updateScreenSize);

    console.log('Editor initialized successfully');
}

function updateScreenSize() {
    const width = window.innerWidth;
    document.getElementById('current-width').textContent = width;
}

function truncateText(text, maxLength) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength - 3) + '...';
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================================
// Global Exports (for inline onclick handlers)
// ============================================================================

window.closeModal = closeModal;
window.submitNodeForm = submitNodeForm;
window.submitEdgeForm = submitEdgeForm;
window.deleteNode = deleteNode;
window.openEditNodeModal = openEditNodeModal;
window.startInlineEdit = startInlineEdit;
window.cancelInlineEdit = cancelInlineEdit;
window.saveInlineEdit = saveInlineEdit;
window.selectNodeById = selectNodeById;

// ============================================================================
// Entry Point
// ============================================================================

document.addEventListener('DOMContentLoaded', initialize);
