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
    gistTargetLen: null, // Supplied by /api/health; a warning, never an edit cap.
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
    // Raw edge counts as stored on the server, per level, before any
    // endpoint-presence filtering — the basis for the honest edge count.
    rawEdgeTotals: { user: 0, project: 0 },
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
    // 'default': active nodes + one-hop neighbours (readable on a mature
    // graph). 'full': every node at this level, the original behaviour.
    viewMode: 'default',
    // Only affects 'default' view — 'full' always shows orphaned nodes.
    showOrphaned: false,
    graphRequestId: 0,
    nodeScoreCache: new Map(),
    revealedNodeIds: new Set(),
    fitNextRender: false,
    search: {
        query: '', result: null, loading: false, error: null,
        requestId: 0, controller: null, timer: null, showAllMatches: false,
    },
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

// Honest counts (roadmap 08): the header names every stored edge, whether
// drawn, hidden by the default view, or dangling (an endpoint missing from
// this level entirely — a deleted node, or an edge that crossed levels).
function updateStats({ nodesShown = 0, nodesTotal = 0, edgesDrawn = 0,
                       edgesDangling = 0, edgesHiddenByView = 0, edgesTotal = 0 } = {}) {
    const nodeCountEl = document.getElementById('node-count');
    nodeCountEl.textContent = nodesShown === nodesTotal
        ? `Nodes: ${nodesShown}`
        : `Nodes: ${nodesShown} of ${nodesTotal}`;

    const edgeCountEl = document.getElementById('edge-count');
    const extras = [];
    if (edgesDangling) extras.push(`${edgesDangling} dangling`);
    if (edgesHiddenByView) extras.push(`${edgesHiddenByView} hidden`);
    edgeCountEl.textContent = extras.length
        ? `Edges: ${edgesDrawn} drawn, ${extras.join(', ')}`
        : `Edges: ${edgesDrawn}`;
    edgeCountEl.title = extras.length ? `${edgesTotal} stored total` : '';
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
        case 'node_renamed':
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
        'node_recalled': `Node recalled: ${message.node?.id}`,
        'node_renamed': `Node renamed: ${message.old_id} → ${message.node?.id}`
    };
    return actions[message.type] || 'Graph updated';
}

// Extract a useful error message from a failed API response (the server sends
// {"detail": ...} with real validation messages on 400s).
async function errDetail(response) {
    try {
        const data = await response.json();
        if (data.detail) return `HTTP ${response.status}: ${data.detail}`;
    } catch (e) { /* non-JSON body */ }
    return `HTTP ${response.status}`;
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
        const target = health.limits?.gist_target_chars ?? health.mcp_server?.limits?.gist_target_chars;
        CONFIG.gistTargetLen = Number.isInteger(target) && target > 0 ? target : null;

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
            // No project_path on record (legacy graph, _meta never stamped
            // it): shown, per roadmap 08, but not openable — there is no
            // path to send the server, and a slug is never guessed as one.
            const noPath = !project.project_path;
            const missingFolder = project.project_path && project.path_exists === false;

            const item = document.createElement('div');
            item.className = noPath ? 'project-item project-item-nopath' : 'project-item';
            item.dataset.path = project.project_path || '';

            let meta = '';
            if (project.has_graph && project.node_count !== null) {
                meta = `${project.node_count}N · ${project.edge_count}E`;
            } else {
                meta = 'no graph';
            }
            if (noPath) meta += ' · path unknown';
            else if (missingFolder) meta += ' · folder removed';

            const titleText = project.project_path || `${project.display_name} — no path on record`;

            item.innerHTML = `
                <span class="project-item-icon">${ICONS.folder}</span>
                <div class="project-item-info">
                    <div class="project-item-name" title="${escapeHtml(titleText)}">${escapeHtml(project.display_name)}</div>
                    <div class="project-item-meta">${escapeHtml(meta)}</div>
                </div>
            `;

            item.addEventListener('click', () => {
                if (noPath) {
                    showToast('No path on record for this graph — nothing to open it with', 'warning');
                    return;
                }
                selectProject(project.project_path);
            });
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
    resetGraphSelection();
    loadGraph();
}

function selectProject(projectPath) {
    document.querySelectorAll('.project-item').forEach(el => el.classList.remove('active'));
    const item = document.querySelector(`.project-item[data-path="${CSS.escape(projectPath)}"]`);
    if (item) item.classList.add('active');

    state.graphLevel = 'project';
    state.selectedProject = projectPath;
    resetGraphSelection();
    loadGraph();
}

function resetGraphSelection() {
    clearSearch(false);
    state.graphData = null;
    state.nodeScoreCache.clear();
    state.selectedNode = null;
    state.editingField = null;
    state.fitNextRender = true;
    state.simulation?.stop();
    state.svgElements?.container.selectAll('*').remove();
    showDetailEmpty();
    updateCurrentGraphLabel();
    updateViewModeButton();
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
        l => l.level === graphLevel && nodeIds.has(l.source.id || l.source) && nodeIds.has(l.target.id || l.target)
    );

    return { nodes: filteredNodes, links: filteredLinks };
}

// Default readable view (roadmap 08): active nodes plus their one-hop
// neighbours; archived neighbours are kept (dimmed by CSS). Orphaned nodes
// are hidden, even as neighbours, unless showOrphaned is on — then every
// orphan is added, not only those adjacent to an active node (most orphans
// are not). `full` view skips this entirely, since it already shows everything.
function applyDefaultViewFilter(levelData, showOrphaned) {
    const activeIds = new Set(
        levelData.nodes.filter(n => !n.archived && !n.orphaned).map(n => n.id)
    );
    const keepIds = new Set(activeIds);
    levelData.links.forEach(l => {
        const src = l.source.id || l.source;
        const tgt = l.target.id || l.target;
        if (activeIds.has(src)) keepIds.add(tgt);
        if (activeIds.has(tgt)) keepIds.add(src);
    });

    const nodes = levelData.nodes.filter(n =>
        n.orphaned ? showOrphaned : keepIds.has(n.id)
    );

    const nodeIdSet = new Set(nodes.map(n => n.id));
    const links = levelData.links.filter(l => {
        const src = l.source.id || l.source;
        const tgt = l.target.id || l.target;
        return nodeIdSet.has(src) && nodeIdSet.has(tgt);
    });

    return { nodes, links };
}

// Search is a read-only view over the full snapshot, independent of tier
// visibility. Every path endpoint is kept, even if it is a lower-ranked hit.
function applySearchViewFilter(levelData, result, showAllMatches, revealedIds) {
    const hits = showAllMatches ? [...result.top, ...result.more] : result.top;
    const keepIds = new Set([...hits.map(n => n.id), ...revealedIds]);
    result.path_edges.forEach(edge => {
        keepIds.add(edge.from);
        keepIds.add(edge.to);
    });
    const nodes = levelData.nodes.filter(n => keepIds.has(n.id));
    const nodeIds = new Set(nodes.map(n => n.id));
    const links = levelData.links.filter(l =>
        nodeIds.has(l.source.id || l.source) && nodeIds.has(l.target.id || l.target)
    );
    return { nodes, links };
}

function graphScopeKey() {
    return `${state.graphLevel}:${state.selectedProject || ''}`;
}

function cancelSearchRequest() {
    clearTimeout(state.search.timer);
    state.search.controller?.abort();
    state.search.requestId++;
}

function clearSearch(render = true) {
    cancelSearchRequest();
    Object.assign(state.search, {
        query: '', result: null, loading: false, error: null,
        controller: null, timer: null, showAllMatches: false,
    });
    state.revealedNodeIds.clear();
    document.getElementById('graph-search-input').value = '';
    hideElement('clear-search-btn');
    hideElement('search-results-panel');
    updateViewModeButton();
    if (render && state.graphData) renderGraph(state.graphData);
}

function scheduleSearch() {
    const query = document.getElementById('graph-search-input').value.trim();
    if (!query) { clearSearch(); return; }
    cancelSearchRequest();
    state.search.query = query;
    state.search.result = null;
    state.search.loading = true;
    state.search.error = null;
    state.revealedNodeIds.clear();
    renderSearchResults();
    updateViewModeButton();
    state.search.timer = setTimeout(runSearch, 300);
}

async function runSearch() {
    const query = document.getElementById('graph-search-input').value.trim();
    if (!query) { clearSearch(); return; }
    if (!state.graphData || !state.graphLevel) return;

    cancelSearchRequest();
    const requestId = state.search.requestId;
    const scope = graphScopeKey();
    const controller = new AbortController();
    Object.assign(state.search, {
        query, controller, result: null, loading: true, error: null,
    });
    state.revealedNodeIds.clear();
    renderSearchResults();
    updateViewModeButton();
    const params = new URLSearchParams({ query, level: state.graphLevel });
    if (state.graphLevel === 'project') params.set('project_path', state.selectedProject);

    try {
        const response = await fetch(`${CONFIG.apiBaseUrl}/api/search?${params}`, { signal: controller.signal });
        if (!response.ok) throw new Error(await errDetail(response));
        const result = await response.json();
        if (requestId !== state.search.requestId || scope !== graphScopeKey()) return;
        state.search.result = result;
        state.search.loading = false;
        state.search.controller = null;
        state.fitNextRender = true;
        renderSearchResults();
        renderGraph(state.graphData);
    } catch (error) {
        if (error.name === 'AbortError' || requestId !== state.search.requestId || scope !== graphScopeKey()) return;
        state.search.loading = false;
        state.search.error = error.message;
        state.search.controller = null;
        renderSearchResults();
        renderGraph(state.graphData);
    }
}

// Build markup from escaped slices, rather than applying a regex to HTML.
function highlightSearchText(value, terms) {
    const text = String(value || '');
    const lower = text.toLowerCase();
    const ranges = [];
    terms.forEach(term => {
        if (!term) return;
        let start = lower.indexOf(term);
        while (start !== -1) {
            ranges.push([start, start + term.length]);
            start = lower.indexOf(term, start + term.length);
        }
    });
    ranges.sort((a, b) => a[0] - b[0]);
    const merged = [];
    ranges.forEach(range => {
        const previous = merged[merged.length - 1];
        if (previous && range[0] <= previous[1]) previous[1] = Math.max(previous[1], range[1]);
        else merged.push([...range]);
    });
    let cursor = 0;
    let html = '';
    merged.forEach(([start, end]) => {
        html += escapeHtml(text.slice(cursor, start)) + `<mark>${escapeHtml(text.slice(start, end))}</mark>`;
        cursor = end;
    });
    return html + escapeHtml(text.slice(cursor));
}

function renderSearchResults() {
    if (!state.search.query) { hideElement('search-results-panel'); return; }
    showElement('search-results-panel');
    showElement('clear-search-btn');
    const list = document.getElementById('search-results');
    const summary = document.getElementById('search-result-summary');
    const help = document.getElementById('search-result-help');
    list.replaceChildren();
    hideElement('search-all-matches-btn');
    hideElement('search-result-help');
    if (state.search.loading) { summary.textContent = 'Searching all nodes…'; return; }
    if (state.search.error) {
        summary.textContent = `Search failed: ${state.search.error}. Press Search to retry.`;
        return;
    }
    const result = state.search.result;
    if (!result) return;
    summary.textContent = result.total
        ? `${result.total} ${result.total === 1 ? 'match' : 'matches'} · all node tiers`
        : 'No matches. Try a different term or another graph.';
    if (!result.total) return;
    showElement('search-result-help');
    help.textContent = 'Numbered nodes are the top matches; other nodes connect them. Select a result to inspect it.';
    if (result.more.length) {
        showElement('search-all-matches-btn');
        const allBtn = document.getElementById('search-all-matches-btn');
        allBtn.textContent = state.search.showAllMatches ? 'Show top 5' : 'Show all matches';
        allBtn.setAttribute('aria-pressed', String(state.search.showAllMatches));
    }
    [...result.top, ...result.more].forEach((hit, index) => {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'search-result';
        row.dataset.nodeId = hit.id;
        row.classList.toggle('active', hit.id === state.selectedNode?.id);
        row.setAttribute('aria-pressed', String(hit.id === state.selectedNode?.id));
        const status = hit.orphaned ? 'Orphaned' : hit.archived ? 'Archived' : 'Active';
        row.innerHTML = `
            <span class="search-result-title"><span class="search-result-rank">${index + 1}</span><span class="search-result-id">${highlightSearchText(hit.id, result.terms)}</span></span>
            <span class="search-result-gist">${highlightSearchText(hit.gist, result.terms)}</span>
            <span class="search-result-meta">${status} · ${escapeHtml(hit.matched_fields.join(', '))}</span>
            ${hit.excerpt ? `<span class="search-result-excerpt">${highlightSearchText(hit.excerpt, result.terms)}</span>` : ''}`;
        row.addEventListener('click', () => selectNodeById(hit.id));
        list.appendChild(row);
    });
}

function updateSearchSelection() {
    document.querySelectorAll('.search-result').forEach(row => {
        const selected = row.dataset.nodeId === state.selectedNode?.id;
        row.classList.toggle('active', selected);
        row.setAttribute('aria-pressed', String(selected));
    });
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

function gistCounterText(value) {
    // Match Python's len(): an emoji outside the BMP counts as one character.
    const len = Array.from(value).length;
    const target = CONFIG.gistTargetLen;
    return target ? `${len}/${target}${len > target ? ' · over target' : ''}` : `${len} ${len === 1 ? 'character' : 'characters'}`;
}

function bindGistCounter(textareaId, counterId) {
    const textarea = document.getElementById(textareaId);
    const counter = document.getElementById(counterId);
    if (!textarea || !counter) return;
    const update = () => {
        const len = Array.from(textarea.value).length;
        counter.textContent = gistCounterText(textarea.value);
        counter.classList.toggle('over-limit', CONFIG.gistTargetLen !== null && len > CONFIG.gistTargetLen);
    };
    textarea.addEventListener('input', update);
    update();
}

function openEditNodeModal(node = null) {
    const isEdit = node !== null;
    const title = isEdit ? `Edit Node: ${escapeHtml(node.id)}` : 'Create New Node';

    const content = `
        <form id="node-form">
            <div class="form-group">
                <label>Node ID</label>
                <input type="text" id="node-id" value="${isEdit ? escapeHtml(node.id) : ''}"
                       ${isEdit ? 'readonly' : ''} required placeholder="kebab-case-id">
            </div>
            <div class="form-group">
                <label>Description (Gist)</label>
                <textarea id="node-gist" rows="3" required aria-describedby="node-gist-counter node-gist-help">${isEdit ? escapeHtml(node.gist) : ''}</textarea>
                <span class="char-counter" id="node-gist-counter" aria-live="polite">${gistCounterText(isEdit ? node.gist : '')}</span>
                <p class="gist-guidance" id="node-gist-help">Longer gists can still be saved. Move detail to Notes for faster scanning.</p>
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
    bindGistCounter('node-gist', 'node-gist-counter');
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
                session_id: state.sessionId,
                ...projectPathBody()
            })
        });

        if (!response.ok) throw new Error(await errDetail(response));

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
        const response = await fetch(`${CONFIG.apiBaseUrl}/api/edges`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                level: state.graphLevel,
                from: state.edgeCreationSource.id,
                to: to,
                rel: rel,
                notes: notesText ? notesText.split('\n').filter(n => n.trim()) : null,
                session_id: state.sessionId,
                ...projectPathBody()
            })
        });

        if (!response.ok) throw new Error(await errDetail(response));

        showToast('Edge created', 'success');
        closeModal();
        await loadGraph();
    } catch (error) {
        showToast(`Failed: ${error.message}`, 'error');
    }
}

function confirmDeleteNode(node) {
    // The node id is bound via a listener, never interpolated into an inline
    // onclick — entity-escaping cannot make data safe inside a JS-in-attribute
    // context (attributes are entity-decoded before the JS runs).
    openModal('Confirm Deletion', `
        <p>Delete node <strong>${escapeHtml(node.id)}</strong>?</p>
        <p style="color: var(--warning-color); margin-top: 0.5rem;">Connected edges will also be deleted.</p>
    `, `
        <button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn btn-danger" id="confirm-delete-btn">Delete</button>
    `);
    document.getElementById('confirm-delete-btn')
        .addEventListener('click', () => deleteNode(node.id));
}

// project_path for write request bodies — a project graph must be resolved by
// path: the editor's WebSocket session is not registered against any project,
// so session_id alone can't address it on the MCP server.
function projectPathBody() {
    return (state.graphLevel === 'project' && state.selectedProject)
        ? { project_path: state.selectedProject }
        : {};
}

// Build the query string for single-node API calls (read/recall/delete).
// Same project_path mechanism as projectPathBody, in query-string form.
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
                session_id: state.sessionId,
                ...projectPathBody()
            })
        });

        if (!response.ok) throw new Error(await errDetail(response));

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

    const levelData = applyLevelFilter(graphData, state.graphLevel);

    // Dangling: edges stored for this level whose endpoint transformGraphData
    // / applyLevelFilter could not resolve (a deleted node, or an edge that
    // crossed levels) — the honesty gap the readability rework must not hide.
    const totalStoredEdges = state.rawEdgeTotals[state.graphLevel] || 0;
    const dangling = Math.max(0, totalStoredEdges - levelData.links.length);

    // Degree from ALL non-orphaned edges at this level, independent of which
    // view is currently rendered, so a hub's radius does not change when the
    // view toggles — only which nodes are visible changes.
    const nodesById = new Map(levelData.nodes.map(n => [n.id, n]));
    const degreeMap = {};
    levelData.nodes.forEach(n => degreeMap[n.id] = 0);
    levelData.links.forEach(l => {
        const src = l.source.id || l.source;
        const tgt = l.target.id || l.target;
        if (nodesById.get(src)?.orphaned || nodesById.get(tgt)?.orphaned) return;
        if (degreeMap[src] !== undefined) degreeMap[src]++;
        if (degreeMap[tgt] !== undefined) degreeMap[tgt]++;
    });
    const maxDegree = Math.max(1, ...Object.values(degreeMap));

    let viewData = state.search.result
        ? applySearchViewFilter(levelData, state.search.result, state.search.showAllMatches, state.revealedNodeIds)
        : state.viewMode === 'full' ? levelData : applyDefaultViewFilter(levelData, state.showOrphaned);
    // Connection links in Details can also reveal a node outside the default
    // view. This changes only the canvas, never a node's archival state.
    if (!state.search.result && state.revealedNodeIds.size) {
        const ids = new Set([...viewData.nodes.map(n => n.id), ...state.revealedNodeIds]);
        viewData = {
            nodes: levelData.nodes.filter(n => ids.has(n.id)),
            links: levelData.links.filter(l => ids.has(l.source.id || l.source) && ids.has(l.target.id || l.target)),
        };
    }
    const hiddenByView = levelData.links.length - viewData.links.length;
    const searchHits = new Map([...(state.search.result?.top || []), ...(state.search.result?.more || [])]
        .map((hit, index) => [hit.id, index + 1]));
    const pathKeys = new Set((state.search.result?.path_edges || []).map(e => `${e.from}\0${e.to}\0${e.rel}`));

    updateViewModeButton(viewData);

    updateStats({
        nodesShown: viewData.nodes.length,
        nodesTotal: levelData.nodes.length,
        edgesDrawn: viewData.links.length,
        edgesDangling: dangling,
        edgesHiddenByView: hiddenByView,
        edgesTotal: totalStoredEdges,
    });

    if (viewData.nodes.length === 0) {
        state.simulation.stop();
        showEmptyState(state.search.result
            ? 'No search matches — try another term'
            : levelData.nodes.length > 0 ? 'No visible nodes — choose All nodes to see everything' : 'No nodes to display');
        return;
    }

    viewData.nodes.forEach(n => {
        const degree = degreeMap[n.id] || 0;
        n._radius = CONFIG.node.radius * (1 + 0.5 * Math.sqrt(degree / maxDegree));
        const gistLen = (n.gist || '').length;
        const notesLen = (n.notes || []).reduce((sum, note) => sum + note.length, 0);
        n._contentWeight = Math.min(gistLen + notesLen, 1000);
    });
    const maxContent = Math.max(1, ...viewData.nodes.map(n => n._contentWeight));

    const link = container.append('g')
        .selectAll('line')
        .data(viewData.links)
        .enter()
        .append('line')
        .attr('class', d => pathKeys.has(`${d.source.id || d.source}\0${d.target.id || d.target}\0${d.rel}`)
            ? 'link link-search-path' : 'link')
        .attr('stroke-width', 1.5);

    const linkLabel = container.append('g')
        .selectAll('text')
        .data(viewData.links)
        .enter()
        .append('text')
        .attr('class', 'link-label')
        .text(d => d.rel);

    // Wide invisible hit-line, drawn on top of `link` — edge labels show on
    // hover only, and a 1.5px line is too thin to reliably target.
    const linkHit = container.append('g')
        .selectAll('line')
        .data(viewData.links)
        .enter()
        .append('line')
        .attr('class', 'link-hit')
        .on('mouseenter', (event, d) => {
            linkLabel.filter(ld => ld === d).classed('link-label-visible', true);
        })
        .on('mouseleave', (event, d) => {
            linkLabel.filter(ld => ld === d).classed('link-label-visible', false);
        });

    const node = container.append('g')
        .selectAll('circle')
        .data(viewData.nodes)
        .enter()
        .append('circle')
        .attr('class', d => {
            const classes = ['node', `node-${d.level}`];
            if (d.archived) classes.push('node-archived');
            if (d.orphaned) classes.push('node-orphan');
            if (state.search.result) classes.push(searchHits.has(d.id) ? 'node-search-hit' : 'node-search-connector');
            if (d.id === state.selectedNode?.id) classes.push('selected');
            return classes.join(' ');
        })
        .attr('tabindex', 0)
        .attr('role', 'button')
        .attr('aria-label', d => `${d.id}${searchHits.has(d.id) ? `, search result ${searchHits.get(d.id)}` : ''}`)
        .attr('r', d => d._radius)
        .on('click', (event, d) => handleNodeClick(event, d))
        .on('keydown', (event, d) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                handleNodeClick(event, d);
            }
        })
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
        .data(viewData.nodes)
        .enter()
        .append('text')
        .attr('class', d => {
            let cls = 'node-label';
            if (d.archived) cls += ' node-label-archived';
            if (d.orphaned) cls += ' node-label-orphan';
            if (state.search.result) cls += ' node-label-search';
            return cls;
        })
        .attr('dy', d => -(d._radius + 6))
        .text(d => truncateText(d.id, state.search.result ? 28 : 20));

    const ranks = container.append('g').selectAll('text')
        .data(viewData.nodes.filter(n => searchHits.has(n.id) && searchHits.get(n.id) <= 5))
        .enter().append('text').attr('class', 'search-rank').attr('dy', '0.35em')
        .text(d => searchHits.get(d.id));

    // Controls and result rows change the available canvas size. Recenter the
    // forces from the current viewport rather than the initial layout.
    const viewport = document.getElementById('graph-container');
    state.simulation.force('center').x(viewport.clientWidth / 2).y(viewport.clientHeight / 2);
    state.simulation.force('x').x(viewport.clientWidth / 2);
    state.simulation.force('y').y(viewport.clientHeight / 2);
    let ticks = 0;

    state.simulation.force('charge', d3.forceManyBody().strength(d => {
        const contentRatio = d._contentWeight / maxContent;
        return CONFIG.simulation.chargeStrength * (1 + contentRatio);
    }));
    state.simulation.force('collision', d3.forceCollide().radius(d => d._radius + 4));

    state.simulation
        .nodes(viewData.nodes)
        .on('tick', () => {
            link
                .attr('x1', d => d.source.x)
                .attr('y1', d => d.source.y)
                .attr('x2', d => d.target.x)
                .attr('y2', d => d.target.y);

            linkHit
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
            ranks.attr('x', d => d.x).attr('y', d => d.y);
            if (state.fitNextRender && ++ticks >= 35) {
                fitGraphToNodes(viewData.nodes);
                state.fitNextRender = false;
            }
        });

    state.simulation.force('link').links(viewData.links);
    state.simulation.alpha(1).restart();
}

function fitGraphToNodes(nodes) {
    if (!nodes.length || !state.svgElements) return;
    const viewport = document.getElementById('graph-container');
    const xs = nodes.map(n => n.x).filter(Number.isFinite);
    const ys = nodes.map(n => n.y).filter(Number.isFinite);
    if (!xs.length || !ys.length) return;
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const scale = Math.min(1.4, viewport.clientWidth / (maxX - minX + 160), viewport.clientHeight / (maxY - minY + 120));
    const transform = d3.zoomIdentity.translate(viewport.clientWidth / 2, viewport.clientHeight / 2)
        .scale(scale).translate(-(minX + maxX) / 2, -(minY + maxY) / 2);
    state.svgElements.svg.transition().duration(250).call(state.zoom.transform, transform);
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
        // Peer id travels as a data attribute and is bound to a listener in
        // renderNodeDetails — never interpolated into an inline onclick.
        return `<li class="conn-row">
            <span class="conn-arrow">${arrow}</span>
            <span class="conn-rel">${escapeHtml(l.rel)}</span>
            <span class="conn-peer" data-peer="${escapeHtml(other)}" title="Click to select">${escapeHtml(other)}</span>
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
    state.revealedNodeIds.add(nodeId);
    state.fitNextRender = true;
    renderGraph(state.graphData);
    d3.selectAll('.node').classed('selected', false);
    d3.selectAll('.node').filter(d => d.id === nodeId).classed('selected', true);
    renderNodeDetails(node);
    updateSearchSelection();
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
    updateSearchSelection();
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

function scoreCacheKey(node) {
    return `${graphScopeKey()}:${node.id}`;
}

function scoreDate(timestamp) {
    return timestamp ? new Date(timestamp * 1000).toLocaleString() : 'Never';
}

function buildNodeScoreContent(node) {
    const cached = state.nodeScoreCache.get(scoreCacheKey(node));
    if (!cached || cached.loading) return '<p class="score-note">Calculating score…</p>';
    if (cached.error) return `<p class="score-note">${escapeHtml(cached.error)}</p>`;
    const data = cached.data;
    const total = data.score ?? data.preview_score;
    const preview = !data.eligible;
    const connected = data.connectedness;
    const incoming = connected.incoming, outgoing = connected.outgoing;
    const raw = key => data.components.find(c => c.key === key).raw.toFixed(3);
    const edgeCount = Object.values(incoming).reduce((a, b) => a + b, 0)
        + Object.values(outgoing).reduce((a, b) => a + b, 0);
    const rows = data.components.map(component => `<tr>
        <th scope="row" class="score-factor-${component.key}">${escapeHtml(component.label)}</th>
        <td>${(component.percentile * 100).toFixed(1)}%</td>
        <td>${(component.weight * 100).toFixed(0)}%</td>
        <td>+${component.contribution.toFixed(3)}</td>
    </tr>`).join('');
    return `
        <div class="score-total"><strong>${total.toFixed(3)}</strong><span>/ 1.000${preview ? ' · preview' : ''}</span></div>
        <p class="score-note">${escapeHtml(data.reason)}</p>
        <div class="score-bar" aria-hidden="true">${data.components.map(c =>
            `<span class="score-bar-${c.key}" style="width:${(c.contribution * 100).toFixed(2)}%"></span>`).join('')}</div>
        <table class="score-table">
            <thead><tr><th scope="col">Factor</th><th scope="col">Rank</th><th scope="col">Weight</th><th scope="col">Adds</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        <p class="score-note">Ranks among ${data.pool.size} eligible ${data.pool.include_archived ? 'active and archived' : 'active'} nodes in this graph. Equal values share a rank.</p>
        <details class="score-calculation"><summary>Raw values and calculation</summary><dl class="score-factors">
            <dt>Recency</dt>
            <dd>Latest write or read: ${escapeHtml(scoreDate(data.components[0].raw))}<br>Write: ${escapeHtml(scoreDate(data.recency.write_ts))}<br>Read: ${escapeHtml(scoreDate(data.recency.read_ts))}</dd>
            <dt>Connectedness · ${raw('connectedness')}</dt>
            <dd>Incoming: ${incoming.active} active, ${incoming.archived} archived, ${incoming.unweighted} unweighted.<br>Outgoing: ${outgoing.active} active, ${outgoing.archived} archived, ${outgoing.unweighted} unweighted.<br>Active neighbors count ×1; archived ×${connected.archived_neighbor_weight}; others ×0.<br>0.66 × ${connected.weighted_in.toFixed(2)} + 0.33 × ${connected.weighted_out.toFixed(2)} = ${connected.weighted_degree.toFixed(3)}.<br>Hub floor: ${connected.hub_floor_weight} × ln(1 + ${edgeCount}) = ${connected.hub_floor.toFixed(3)}. The larger value is used.</dd>
            <dt>Usefulness · ${raw('usefulness')}</dt>
            <dd>${data.usefulness.endorsements} explicit endorsements, decayed with a ${data.usefulness.half_life_days}-day half-life. Each contributes 0.5<sup>age / ${data.usefulness.half_life_days}</sup>.</dd>
        </dl></details>
        ${data.grace.protected ? `<p class="score-note">Creation grace: ${data.grace.days} days, until ${escapeHtml(scoreDate(data.grace.ends_ts))}.</p>` : ''}
        <p class="score-note">Higher scores stay longer. Graph size and available context also determine archival and refill.</p>`;
}

async function fetchNodeScore(node) {
    const key = scoreCacheKey(node);
    if (state.nodeScoreCache.has(key)) return;
    const generation = state.graphRequestId;
    const scope = graphScopeKey();
    state.nodeScoreCache.set(key, { loading: true });
    try {
        const response = await fetch(`${CONFIG.apiBaseUrl}/api/nodes/${node.level}/${encodeURIComponent(node.id)}/score${nodeApiQuery()}`);
        if (!response.ok) {
            const message = response.status === 404
                ? 'Score unavailable on the running server. The updated memory server must be running.'
                : `Score unavailable: ${await errDetail(response)}`;
            throw new Error(message);
        }
        const data = await response.json();
        if (generation !== state.graphRequestId || scope !== graphScopeKey()) return;
        state.nodeScoreCache.set(key, { data });
    } catch (error) {
        if (generation !== state.graphRequestId || scope !== graphScopeKey()) return;
        state.nodeScoreCache.set(key, { error: error.message });
    }
    if (state.selectedNode?.id === node.id && state.selectedNode.level === node.level) {
        const content = document.getElementById('node-score-content');
        if (content) content.innerHTML = buildNodeScoreContent(node);
    }
}

function renderNodeDetails(node) {
    const container = document.getElementById('detail-content');
    const ef = state.editingField;

    // ---- Gist field ----
    const gistHtml = ef === 'gist'
        ? `<div class="inline-edit-wrap">
               <textarea id="inline-gist" rows="3" aria-describedby="gist-counter inline-gist-help">${escapeHtml(node.gist)}</textarea>
               <div class="inline-edit-meta">
                   <span class="char-counter" id="gist-counter" aria-live="polite">${gistCounterText(node.gist)}</span>
                   <div class="inline-edit-actions">
                       <button class="btn btn-xs" onclick="cancelInlineEdit()">Cancel</button>
                       <button class="btn btn-xs btn-primary" onclick="saveInlineEdit('gist')">${icon('check','sm')} Save</button>
                   </div>
               </div>
               <p class="gist-guidance" id="inline-gist-help">Longer gists can still be saved. Move detail to Notes for faster scanning.</p>
           </div>`
        : `<div class="detail-value">${escapeHtml(node.gist)}</div>
           ${CONFIG.gistTargetLen !== null && Array.from(node.gist).length > CONFIG.gistTargetLen
               ? `<span class="char-counter over-limit">${gistCounterText(node.gist)}</span>` : ''}`;

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
                <h3>Archival score</h3>
                <div id="node-score-content">${buildNodeScoreContent(node)}</div>
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

    // Wire up connection-peer clicks (ids bound via data attribute, not inline JS)
    container.querySelectorAll('.conn-peer').forEach(el => {
        el.addEventListener('click', () => selectNodeById(el.dataset.peer));
    });

    // Wire up live char counter for gist
    if (ef === 'gist') {
        const ta = document.getElementById('inline-gist');
        bindGistCounter('inline-gist', 'gist-counter');
        if (ta) {
            ta.focus();
            ta.setSelectionRange(ta.value.length, ta.value.length);
        }
    } else if (ef === 'notes') {
        document.getElementById('inline-notes')?.focus();
    } else if (ef === 'touches') {
        document.getElementById('inline-touches')?.focus();
    }
    fetchNodeScore(node);
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

    const requestId = ++state.graphRequestId;
    const scope = graphScopeKey();
    // Cancel an in-flight search before refreshing its underlying snapshot.
    // Re-run the current query only after this graph response is accepted.
    cancelSearchRequest();
    try {
        hideElement('graph-error');
        hideElement('graph-welcome');
        showElement('graph-loading');

        const rawData = await fetchGraphData();
        if (requestId !== state.graphRequestId || scope !== graphScopeKey()) return;
        state.nodeScoreCache.clear();
        state.graphData = transformGraphData(rawData);
        state.rawEdgeTotals = {
            user: Object.keys(rawData.user?.edges || {}).length,
            project: Object.keys(rawData.project?.edges || {}).length,
        };

        renderGraph(state.graphData);

        hideElement('graph-loading');
        updateCurrentGraphLabel();

        // If a node was selected before reload, try to keep it selected
        if (state.selectedNode) {
            const refreshed = state.graphData.nodes.find(n => n.id === state.selectedNode.id && n.level === state.selectedNode.level);
            if (refreshed) {
                state.selectedNode = refreshed;
                if (!state.editingField) renderNodeDetails(refreshed);
            } else { state.selectedNode = null; showDetailEmpty(); }
        }
        if (state.search.query) await runSearch();
    } catch (error) {
        if (requestId !== state.graphRequestId || scope !== graphScopeKey()) return;
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
    updateStats();
    updateCurrentGraphLabel();
}

// The choices stay in place; their pressed state names the current view.
// Choosing either view clears search and returns to graph browsing.
function updateViewModeButton(viewData = null) {
    const all = document.getElementById('view-mode-btn');
    const visible = document.getElementById('visible-view-btn');
    const full = state.viewMode === 'full';
    const searching = Boolean(state.search.query);
    const ready = Boolean(state.graphData);
    all.setAttribute('aria-pressed', String(full && !searching));
    visible.setAttribute('aria-pressed', String(!full && !searching));
    all.disabled = visible.disabled = !ready;
    const orphaned = document.getElementById('show-orphaned-toggle');
    orphaned.disabled = !ready || full || searching;
    orphaned.checked = full || state.showOrphaned;
    document.getElementById('graph-search-input').disabled = !ready;
    document.getElementById('search-btn').disabled = !ready;
    const summary = document.getElementById('graph-view-summary');
    if (!ready) { summary.textContent = 'Select a graph to explore its nodes'; return; }
    if (searching) {
        if (state.search.loading) summary.textContent = 'Searching every node in this graph…';
        else if (state.search.error) summary.textContent = 'Search unavailable · graph browsing is still available';
        else {
            const result = state.search.result;
            summary.textContent = state.search.showAllMatches
                ? `Search · all ${result?.total || 0} matches and connecting nodes`
                : `Search · top ${result?.top.length || 0} matches and their connecting paths`;
        }
    } else if (full) summary.textContent = 'All nodes · includes archived and orphaned';
    else {
        const levelData = applyLevelFilter(state.graphData, state.graphLevel);
        const shown = viewData || applyDefaultViewFilter(levelData, state.showOrphaned);
        const hidden = levelData.nodes.length - shown.nodes.length;
        summary.textContent = `Active nodes + neighbors${state.showOrphaned ? ' + orphaned' : ''}${hidden ? ` · ${hidden} hidden` : ''}`;
    }
}

async function initialize() {
    console.log('Initializing Knowledge Graph Visual Editor...');

    // Recovery must be wired before health can fail. Startup has not yet
    // registered the ordinary graph-reload handler in that case.
    const retry = document.getElementById('retry-btn');
    retry.onclick = initialize;
    retry.disabled = true;
    const healthy = await checkHealth();
    retry.disabled = false;
    if (!healthy) {
        showError('Cannot connect to MCP server. Please ensure the server is running.');
        return;
    }
    retry.onclick = null;

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

    updateViewModeButton();
    const chooseView = mode => {
        state.viewMode = mode;
        state.fitNextRender = true;
        clearSearch();
    };
    document.getElementById('visible-view-btn').addEventListener('click', () => chooseView('default'));
    document.getElementById('view-mode-btn').addEventListener('click', () => chooseView('full'));
    document.getElementById('show-orphaned-toggle').addEventListener('change', (e) => {
        state.showOrphaned = e.target.checked;
        if (state.graphData) renderGraph(state.graphData);
    });

    document.getElementById('graph-search-form').addEventListener('submit', event => {
        event.preventDefault();
        runSearch();
    });
    document.getElementById('graph-search-input').addEventListener('input', scheduleSearch);
    document.getElementById('graph-search-input').addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); clearSearch(); }
    });
    document.getElementById('clear-search-btn').addEventListener('click', () => clearSearch());
    document.getElementById('search-all-matches-btn').addEventListener('click', () => {
        state.search.showAllMatches = !state.search.showAllMatches;
        state.revealedNodeIds.clear();
        state.fitNextRender = true;
        renderSearchResults();
        if (state.graphData) renderGraph(state.graphData);
    });
    document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && state.graphData) {
            event.preventDefault();
            document.getElementById('graph-search-input').focus();
        }
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
    // Escapes quotes as well as &<> — values are interpolated into attribute
    // contexts (title="...", data-*="..."), where a bare quote breaks out.
    return String(text)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
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
