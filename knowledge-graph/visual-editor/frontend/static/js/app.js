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
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    recall: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>',
    link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
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
    graphLevel: null,       // null = nothing selected, 'user' or 'project'
    selectedProject: null,
    projects: [],
    simulation: null,
    zoom: null,
    sessionId: null,
    ws: null,
    contextNode: null,
    edgeCreationSource: null,
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
    const wsUrl = `ws://${window.location.hostname}:3000/ws`;
    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        console.log('WebSocket connected');
        setConnectionStatus('connected', 'Live');
    };

    state.ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        handleWebSocketMessage(message);
    };

    state.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        setConnectionStatus('error', 'Error');
    };

    state.ws.onclose = () => {
        console.log('WebSocket disconnected');
        setConnectionStatus('error', 'Offline');
        setTimeout(() => connectWebSocket(), 5000);
    };
}

function handleWebSocketMessage(message) {
    console.log('WebSocket message:', message);

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
        'edge_updated': `Edge updated: ${message.edge?.from} \u2192 ${message.edge?.to}`,
        'edge_deleted': `Edge deleted: ${message.from} \u2192 ${message.to}`,
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
// Project Management
// ============================================================================

async function loadProjects() {
    try {
        const projects = await fetchProjects();
        state.projects = projects;

        const selector = document.getElementById('project-selector');
        selector.innerHTML = '<option value="">Select a project...</option>';

        projects.forEach(project => {
            const option = document.createElement('option');
            option.value = project.project_path;

            let label = project.display_name;
            if (project.has_graph && project.node_count !== null) {
                label += ` (${project.node_count}N \u00b7 ${project.edge_count}E)`;
            } else {
                label += ' (no graph)';
            }

            option.textContent = label;
            option.title = project.project_path;
            selector.appendChild(option);
        });

        console.log(`Loaded ${projects.length} projects`);
    } catch (error) {
        console.error('Failed to load projects:', error);
    }
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

async function deleteNode(nodeId) {
    try {
        await fetch(
            `${CONFIG.apiBaseUrl}/api/nodes/${state.graphLevel}/${encodeURIComponent(nodeId)}?session_id=${state.sessionId || ''}`,
            {method: 'DELETE'}
        );
        showToast('Node deleted', 'success');
        closeModal();
        await loadGraph();
    } catch (error) {
        showToast(`Failed: ${error.message}`, 'error');
    }
}

async function recallNode(node) {
    try {
        await fetch(
            `${CONFIG.apiBaseUrl}/api/nodes/${state.graphLevel}/${encodeURIComponent(node.id)}/recall?session_id=${state.sessionId || ''}`,
            {method: 'POST'}
        );
        showToast('Node recalled', 'success');
        await loadGraph();
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
            if (node.archived) recallNode(node);
            else showToast('Node is not archived', 'warning');
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

    // --- Compute per-node metrics ---
    // Degree: count of connections
    const degreeMap = {};
    filteredData.nodes.forEach(n => degreeMap[n.id] = 0);
    filteredData.links.forEach(l => {
        const src = l.source.id || l.source;
        const tgt = l.target.id || l.target;
        if (degreeMap[src] !== undefined) degreeMap[src]++;
        if (degreeMap[tgt] !== undefined) degreeMap[tgt]++;
    });
    const maxDegree = Math.max(1, ...Object.values(degreeMap));

    // Content weight: rough char count of gist + notes
    filteredData.nodes.forEach(n => {
        const degree = degreeMap[n.id] || 0;
        // Radius: base 8, scales up to 12 (1.5x) via sqrt of normalized degree
        n._radius = CONFIG.node.radius * (1 + 0.5 * Math.sqrt(degree / maxDegree));
        // Content weight: gist length + notes length (capped)
        const gistLen = (n.gist || '').length;
        const notesLen = (n.notes || []).reduce((sum, note) => sum + note.length, 0);
        n._contentWeight = Math.min(gistLen + notesLen, 1000); // cap at 1000 chars
    });
    const maxContent = Math.max(1, ...filteredData.nodes.map(n => n._contentWeight));

    // Links
    const link = container.append('g')
        .selectAll('line')
        .data(filteredData.links)
        .enter()
        .append('line')
        .attr('class', 'link')
        .attr('stroke-width', 1.5);

    // Link labels
    const linkLabel = container.append('g')
        .selectAll('text')
        .data(filteredData.links)
        .enter()
        .append('text')
        .attr('class', 'link-label')
        .text(d => d.rel);

    // Nodes — radius varies by connection count
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

    // Node labels — offset scales with radius
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

    // --- Simulation with per-node forces ---
    // Charge: content-heavy nodes pull 1.5–2.5x harder
    state.simulation.force('charge', d3.forceManyBody().strength(d => {
        const contentRatio = d._contentWeight / maxContent; // 0..1
        return CONFIG.simulation.chargeStrength * (1 + contentRatio);
    }));
    // Collision: match visual radius + padding
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
// Event Handlers
// ============================================================================

function handleNodeClick(event, node) {
    d3.selectAll('.node').classed('selected', false);
    d3.select(event.target).classed('selected', true);

    state.selectedNode = node;
    renderNodeDetails(node);
}

function renderNodeDetails(node) {
    const container = document.getElementById('detail-content');

    const html = `
        <div class="node-detail">
            <div class="detail-section">
                <h3>Node Information</h3>
                <div class="detail-field">
                    <div class="detail-label">ID</div>
                    <div class="detail-value"><code>${escapeHtml(node.id)}</code></div>
                </div>
                <div class="detail-field">
                    <div class="detail-label">Level</div>
                    <div class="detail-value">
                        <span class="badge badge-${node.level}">${node.level}</span>
                        ${node.archived ? '<span class="badge badge-archived">Archived</span>' : ''}
                        ${node.orphaned ? '<span class="badge badge-orphaned">Orphaned</span>' : ''}
                    </div>
                </div>
                <div class="detail-field">
                    <div class="detail-label">Description</div>
                    <div class="detail-value">${escapeHtml(node.gist)}</div>
                </div>
            </div>

            ${node.notes && node.notes.length > 0 ? `
                <div class="detail-section">
                    <h3>Notes</h3>
                    <ul class="detail-list">
                        ${node.notes.map(note => `<li>${escapeHtml(note)}</li>`).join('')}
                    </ul>
                </div>
            ` : ''}

            ${node.touches && node.touches.length > 0 ? `
                <div class="detail-section">
                    <h3>Files & Artifacts</h3>
                    <ul class="detail-list">
                        ${node.touches.map(file => `<li><code>${escapeHtml(file)}</code></li>`).join('')}
                    </ul>
                </div>
            ` : ''}
        </div>
    `;

    container.innerHTML = html;
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

    // For project level, need a selected project
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
    } catch (error) {
        console.error('Failed to load graph:', error);
        showError(`Failed to load graph: ${error.message}`);
    }
}

function showWelcome() {
    // Clear any existing graph
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

    // Check server health
    const healthy = await checkHealth();
    if (!healthy) {
        showError('Cannot connect to MCP server. Please ensure the server is running.');
        return;
    }

    // Connect WebSocket
    connectWebSocket();

    // Load projects
    await loadProjects();

    // Show welcome state (no graph loaded by default)
    hideElement('graph-loading');
    showWelcome();

    // Event listeners
    document.getElementById('refresh-btn').addEventListener('click', () => {
        if (state.graphLevel) loadGraph();
    });
    document.getElementById('retry-btn').addEventListener('click', loadGraph);
    document.getElementById('create-node-btn').addEventListener('click', () => {
        if (!state.graphLevel) {
            showToast('Select a graph level first', 'warning');
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

    // Level selector
    document.getElementById('level-user').addEventListener('change', (e) => {
        if (e.target.checked) {
            state.graphLevel = 'user';
            document.getElementById('project-selector').disabled = true;
            loadGraph();
        }
    });

    document.getElementById('level-project').addEventListener('change', (e) => {
        if (e.target.checked) {
            state.graphLevel = 'project';
            const selector = document.getElementById('project-selector');
            selector.disabled = false;
            if (selector.value) {
                state.selectedProject = selector.value;
                loadGraph();
            } else {
                updateCurrentGraphLabel();
            }
        }
    });

    document.getElementById('project-selector').addEventListener('change', (e) => {
        state.selectedProject = e.target.value;
        if (state.graphLevel === 'project' && state.selectedProject) {
            loadGraph();
        }
    });

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

// ============================================================================
// Entry Point
// ============================================================================

document.addEventListener('DOMContentLoaded', initialize);
