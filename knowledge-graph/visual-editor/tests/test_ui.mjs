// UI state and async regression tests without a browser or dependencies.
// This covers behavior; a browser remains necessary for visual layout QA.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
    constructor() {
        this.classes = new Set();
        this.classList = {
            add: name => this.classes.add(name),
            remove: name => this.classes.delete(name),
            contains: name => this.classes.has(name),
            toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name),
        };
        this.attributes = {};
        this.dataset = {};
        this.listeners = {};
        this.listenerCounts = {};
        this.children = [];
        this.value = '';
        this.style = {};
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback) {
        this.listeners[name] = callback;
        this.listenerCounts[name] = (this.listenerCounts[name] || 0) + 1;
    }
    appendChild(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    querySelectorAll() { return []; }
    querySelector() { return null; }
    focus() { this.focused = true; context.document.activeElement = this; }
    setSelectionRange(start, end) { this.selection = [start, end]; }
}

const elements = new Map();
const element = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
};
const timers = new Map();
let timerId = 0;
const d3Selection = { classed() { return this; }, filter() { return this; } };
const context = vm.createContext({
    console, URLSearchParams, AbortController,
    setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: id => timers.delete(id),
    window: { location: { origin: 'http://localhost:8766' }, addEventListener() {} },
    document: {
        getElementById: element,
        createElement: () => new Element(),
        addEventListener() {},
        querySelectorAll: selector => selector === '.search-result' ? element('search-results').children : [],
    },
    d3: { select: () => d3Selection, selectAll: () => d3Selection },
    fetch: async () => { throw new Error('Unexpected API request'); },
});
const source = fs.readFileSync(new URL('../frontend/static/js/app.js', import.meta.url), 'utf8');
vm.runInContext(source + `
globalThis.app = {
    state, applyLevelFilter, applyDefaultViewFilter, applySearchViewFilter,
    highlightSearchText, runSearch, scheduleSearch, clearSearch,
    resetGraphSelection, selectNodeById, updateViewModeButton,
    renderSearchResults, buildNodeScoreContent, fetchNodeScore,
    CONFIG, checkHealth, gistCounterText, bindGistCounter,
    openEditNodeModal, renderNodeDetails, submitNodeForm, saveInlineEdit,
    initialize, openModal, closeModal, handleKeyboardShortcut,
};
// D3 drawing and layout are not emulated. Keep the real view/state logic.
renderGraph = data => {
    const level = applyLevelFilter(data, state.graphLevel);
    globalThis.renderedView = state.search.result
        ? applySearchViewFilter(level, state.search.result, state.search.showAllMatches, state.revealedNodeIds)
        : state.viewMode === 'full' ? level : applyDefaultViewFilter(level, state.showOrphaned);
    updateViewModeButton(globalThis.renderedView);
};
renderNodeDetails = () => {};
loadGraph = async () => {};
globalThis.toasts = [];
showToast = (message, type) => toasts.push({ message, type });
`, context);
const { app } = context;
const { state } = app;
let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log(`ok  ${name}`); }
const json = value => JSON.parse(JSON.stringify(value));
const ids = data => Array.from(data.nodes, n => n.id).sort();
const nodes = [
    { id: 'active', level: 'user', gist: 'Signal owner' },
    { id: 'archived', level: 'user', gist: 'Signal pattern', archived: true },
    { id: 'bridge', level: 'user', gist: 'Connector', archived: true },
    { id: 'orphan', level: 'user', gist: 'Signal residue', archived: true, orphaned: true },
    { id: 'far-archived', level: 'user', gist: 'A sleeping node', archived: true },
];
const links = [
    { source: 'active', target: 'archived', rel: 'owns', level: 'user' },
    { source: 'archived', target: 'bridge', rel: 'via', level: 'user' },
    { source: 'bridge', target: 'orphan', rel: 'connects', level: 'user' },
];
const graph = { nodes, links };
const hit = id => ({ id, gist: 'A signal match', matched_fields: ['Description'], excerpt: '' });
const result = {
    top: [hit('active'), hit('archived')], more: [hit('orphan')], total: 3,
    terms: ['signal'], connectors: [{ id: 'bridge' }],
    path_edges: [{ from: 'archived', to: 'bridge', rel: 'via' }, { from: 'bridge', to: 'orphan', rel: 'connects' }],
};
function ready() {
    state.graphLevel = 'user';
    state.selectedProject = null;
    state.graphData = graph;
    state.viewMode = 'default';
    state.showOrphaned = false;
    app.clearSearch(false);
}

await check('Visible view keeps only active nodes and immediate neighbors; All nodes has every tier', () => {
    assert.deepEqual(ids(app.applyDefaultViewFilter(graph, false)), ['active', 'archived']);
    assert.deepEqual(ids(app.applyDefaultViewFilter(graph, true)), ['active', 'archived', 'orphan']);
    ready();
    app.updateViewModeButton();
    assert.equal(element('visible-view-btn').attributes['aria-pressed'], 'true');
    assert.equal(element('view-mode-btn').attributes['aria-pressed'], 'false');
    assert.match(element('graph-view-summary').textContent, /3 hidden/);
    state.viewMode = 'full';
    app.updateViewModeButton();
    assert.equal(element('view-mode-btn').attributes['aria-pressed'], 'true');
    assert.equal(element('show-orphaned-toggle').checked, true);
    assert.equal(element('show-orphaned-toggle').disabled, true);
});

await check('Level filtering does not borrow edges from a different graph with colliding IDs', () => {
    const mixed = { nodes: [...nodes, { id: 'active', level: 'project' }, { id: 'archived', level: 'project' }],
        links: [...links, { source: 'active', target: 'archived', level: 'project', rel: 'other' }] };
    assert.equal(app.applyLevelFilter(mixed, 'user').links.length, 3);
    assert.equal(app.applyLevelFilter(mixed, 'project').links.length, 1);
});

await check('Search keeps complete paths, including orphaned and lower-ranked endpoints', () => {
    const view = app.applySearchViewFilter(graph, result, false, new Set());
    assert.deepEqual(ids(view), ['active', 'archived', 'bridge', 'orphan']);
    assert.equal(view.links.length, 3);
    const noPaths = { ...result, path_edges: [] };
    assert.deepEqual(ids(app.applySearchViewFilter(graph, noPaths, true, new Set())), ['active', 'archived', 'orphan']);
    assert.equal(app.applySearchViewFilter(graph, { top: [], more: [], path_edges: [] }, false, new Set()).nodes.length, 0);
});

await check('Highlighting escapes untrusted text and merges overlapping search stems', () => {
    const html = app.highlightSearchText('<img src=x onerror="signal">', ['sign', 'signal']);
    assert.ok(html.includes('&lt;img'));
    assert.ok(html.includes('&quot;'));
    assert.ok(!html.includes('<img'));
    assert.equal((html.match(/<mark>/g) || []).length, 1);
});

await check('Result list is ranked and selecting a hidden result reveals it without recall', () => {
    ready();
    state.search.query = 'signal';
    state.search.result = result;
    app.renderSearchResults();
    assert.deepEqual(element('search-results').children.map(row => row.dataset.nodeId), ['active', 'archived', 'orphan']);
    assert.match(element('search-result-summary').textContent, /3 matches/);
    const before = json(nodes);
    element('search-results').children[2].listeners.click();
    assert.equal(state.selectedNode.id, 'orphan');
    assert.ok(state.revealedNodeIds.has('orphan'));
    assert.deepEqual(json(nodes), before);
});

await check('A stale response cannot replace a newer search, even when fetch ignores abort', async () => {
    ready();
    const pending = [];
    context.fetch = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }));
    element('graph-search-input').value = 'first';
    const first = app.runSearch();
    element('graph-search-input').value = 'second';
    const second = app.runSearch();
    assert.equal(pending[0].options.signal.aborted, true);
    const newest = { ...result, terms: ['second'], total: 22 };
    pending[1].resolve({ ok: true, json: async () => newest });
    await second;
    pending[0].resolve({ ok: true, json: async () => result });
    await first;
    assert.equal(state.search.result.total, 22);
    assert.equal(state.search.query, 'second');
});

await check('Clearing search cancels pending work and restores the previous All nodes view', async () => {
    ready();
    state.viewMode = 'full';
    let finish;
    context.fetch = () => new Promise(resolve => { finish = resolve; });
    element('graph-search-input').value = 'signal';
    const pending = app.runSearch();
    app.clearSearch();
    finish({ ok: true, json: async () => result });
    await pending;
    assert.equal(state.search.result, null);
    assert.equal(element('view-mode-btn').attributes['aria-pressed'], 'true');
    assert.ok(element('search-results-panel').classes.has('hidden'));
});

await check('Switching graphs clears the query and rejects results from the previous graph', async () => {
    ready();
    let finish;
    context.fetch = () => new Promise(resolve => { finish = resolve; });
    element('graph-search-input').value = 'signal';
    const pending = app.runSearch();
    state.graphLevel = 'project';
    state.selectedProject = '/home/example/project';
    app.resetGraphSelection();
    finish({ ok: true, json: async () => result });
    await pending;
    assert.equal(state.search.result, null);
    assert.equal(element('graph-search-input').value, '');
    assert.equal(element('search-btn').disabled, true);
});

await check('Typing debounces search and clearing cancels the timer', () => {
    ready();
    element('graph-search-input').value = 'a';
    app.scheduleSearch();
    element('graph-search-input').value = 'ab';
    app.scheduleSearch();
    assert.equal(timers.size, 1);
    app.clearSearch();
    assert.equal(timers.size, 0);
});

await check('Search errors remain retryable and a no-match response renders an empty search view', async () => {
    ready();
    element('graph-search-input').value = 'signal';
    context.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: 'Offline' }) });
    await app.runSearch();
    assert.match(element('search-result-summary').textContent, /Search failed.*Offline.*retry/);
    context.fetch = async () => ({ ok: true, json: async () => ({ top: [], more: [], connectors: [], path_edges: [], terms: ['signal'], total: 0 }) });
    await app.runSearch();
    assert.match(element('search-result-summary').textContent, /No matches/);
    assert.equal(context.renderedView.nodes.length, 0);
});

await check('Score card labels excluded-node previews and keeps a zero score visible', () => {
    ready();
    const data = {
        score: 0, preview_score: null, eligible: true, reason: 'Used for archival',
        pool: { size: 3, include_archived: false },
        components: ['recency', 'connectedness', 'usefulness'].map((key, i) => ({ key, label: key, raw: 0, percentile: 0, weight: [0.25, 0.4, 0.35][i], contribution: 0 })),
        connectedness: { incoming: { active: 0, archived: 0, unweighted: 0 }, outgoing: { active: 0, archived: 0, unweighted: 0 }, weighted_in: 0, weighted_out: 0, weighted_degree: 0, hub_floor: 0, hub_floor_weight: 0.5, archived_neighbor_weight: 0.2 },
        recency: { write_ts: 0, read_ts: 0 }, usefulness: { endorsements: 0, half_life_days: 90 }, fresh: { protected: false, tier_size: 0, budget_chars: 5250 },
    };
    state.nodeScoreCache.set('user::active', { data });
    assert.match(app.buildNodeScoreContent(nodes[0]), /<strong>0.000<\/strong>/);
    data.eligible = false; data.score = null; data.preview_score = 0.5;
    assert.match(app.buildNodeScoreContent(nodes[0]), /0.500.*preview/);
});

await check('Score requests are read-only, cached, and invalidate across graph refreshes', async () => {
    ready();
    state.nodeScoreCache.clear();
    const urls = [];
    context.fetch = async url => { urls.push(url); return { ok: true, json: async () => ({ score: 0.5 }) }; };
    state.selectedNode = null;
    await app.fetchNodeScore(nodes[0]);
    await app.fetchNodeScore(nodes[0]);
    assert.equal(urls.length, 1);
    assert.ok(urls[0].includes('/api/nodes/user/active/score'));
    let finish;
    state.nodeScoreCache.clear();
    context.fetch = () => new Promise(resolve => { finish = resolve; });
    const request = app.fetchNodeScore(nodes[0]);
    state.graphRequestId++;
    state.nodeScoreCache.clear();
    finish({ ok: true, json: async () => ({ score: 1 }) });
    await request;
    assert.equal(state.nodeScoreCache.size, 0);
});

await check('Gist counters take their soft target from server health and count Unicode like Python', async () => {
    context.fetch = async () => ({ json: async () => ({ status: 'ok', mcp_server: { status: 'ok' }, limits: { gist_target_chars: 240 } }) });
    assert.equal(await app.checkHealth(), true);
    assert.equal(app.CONFIG.gistTargetLen, 240);
    assert.equal(app.gistCounterText('🧠'.repeat(240)), '240/240');
    assert.match(app.gistCounterText('🧠'.repeat(241)), /241\/240.*over target/);
    // No frontend default invents a policy if health cannot supply one.
    context.fetch = async () => ({ json: async () => ({ status: 'ok', mcp_server: { status: 'ok' } }) });
    await app.checkHealth();
    assert.equal(app.gistCounterText('🧠'), '1 character');
    context.fetch = async () => ({ json: async () => ({ status: 'ok', mcp_server: { status: 'ok' }, limits: { gist_target_chars: 300 } }) });
    await app.checkHealth();
});

await check('Over-target inline and modal gists have no maxlength, and warnings update while typing', () => {
    ready();
    const node = { ...nodes[0], gist: '🧠'.repeat(301) };
    state.selectedNode = node;
    state.editingField = 'gist';
    state.nodeScoreCache.set('user::active', { error: 'Score not requested by this test' });
    element('inline-gist').value = node.gist;
    app.renderNodeDetails(node);
    assert.ok(!element('detail-content').innerHTML.includes('maxlength'));
    assert.match(element('detail-content').innerHTML, /Longer gists can still be saved/);
    assert.match(element('gist-counter').textContent, /301\/300.*over target/);
    assert.ok(element('gist-counter').classes.has('over-limit'));
    element('inline-gist').value += 'a';
    element('inline-gist').listeners.input();
    assert.match(element('gist-counter').textContent, /302\/300/);
    element('inline-gist').value = 'Edited headline';
    element('inline-gist').listeners.input();
    assert.ok(!element('gist-counter').classes.has('over-limit'));
    element('node-gist').value = node.gist;
    app.openEditNodeModal(node);
    assert.ok(!element('modal-container').innerHTML.includes('maxlength'));
    assert.match(element('node-gist-counter').textContent, /301\/300.*over target/);
    element('node-gist').value += 'b';
    element('node-gist').listeners.input();
    assert.match(element('node-gist-counter').textContent, /302\/300/);
    state.editingField = null;
    app.renderNodeDetails(node);
    assert.match(element('detail-content').innerHTML, /char-counter over-limit.*301\/300.*over target/);
});

await check('Inline and modal saves accept long gists, while empty gists remain invalid', async () => {
    ready();
    const node = { ...nodes[0], gist: 'Existing gist' };
    state.selectedNode = node;
    const requests = [];
    context.fetch = async (url, options) => {
        requests.push({ url, ...options, body: JSON.parse(options.body) });
        return { ok: true };
    };
    const gist = '🧠'.repeat(301) + ' another letter';
    element('inline-gist').value = gist;
    await app.saveInlineEdit('gist');
    assert.equal(requests[0].body.gist, gist);
    assert.equal(node.gist, gist);
    element('node-id').value = 'long-gist';
    element('node-gist').value = gist;
    element('node-notes').value = '';
    element('node-touches').value = '';
    await app.submitNodeForm(false);
    await app.submitNodeForm(true);
    assert.equal(requests.length, 3);
    assert.ok(requests.every(r => r.method === 'POST' && r.body.gist === gist));
    element('inline-gist').value = ' ';
    element('node-gist').value = ' ';
    await app.saveInlineEdit('gist');
    await app.submitNodeForm(true);
    assert.equal(requests.length, 3);
    assert.equal(context.toasts.at(-1).type, 'error');
});

await check('Retry stays usable if the memory server is down when the editor first opens', async () => {
    context.fetch = async () => ({ json: async () => ({ status: 'ok', mcp_server: { status: 'down' } }) });
    await app.initialize();
    assert.ok(!element('graph-error').classes.has('hidden'));
    const retry = element('retry-btn').onclick || element('retry-btn').listeners.click;
    assert.equal(typeof retry, 'function', 'The visible Retry button must have a handler after failed startup');
    assert.equal(element('retry-btn').disabled, false);
});

await check('Retry bootstraps a recovered server, disables duplicate clicks, then becomes graph refresh', async () => {
    vm.runInContext(`
        globalThis.socketStarts = 0;
        globalThis.projectLoads = 0;
        globalThis.graphLoads = 0;
        connectWebSocket = () => socketStarts++;
        loadProjects = async () => projectLoads++;
        loadGraph = async () => graphLoads++;
        initializeGraph = () => ({ svg: null, container: null });
    `, context);
    state.graphData = null;
    state.graphLevel = null;
    state.selectedNode = null;
    let finish;
    context.fetch = () => new Promise(resolve => { finish = resolve; });
    const retrying = element('retry-btn').onclick();
    assert.equal(element('retry-btn').disabled, true);
    finish({ json: async () => ({ status: 'ok', mcp_server: { status: 'ok' }, limits: { gist_target_chars: 300 } }) });
    await retrying;
    assert.equal(element('retry-btn').disabled, false);
    assert.equal(element('retry-btn').onclick, null);
    assert.equal(element('retry-btn').listenerCounts.click, 1);
    assert.equal(context.socketStarts, 1);
    assert.equal(context.projectLoads, 1);
    assert.equal(element('connection-text').textContent, 'Connected');
    assert.ok(element('graph-error').classes.has('hidden'));
    await element('retry-btn').listeners.click();
    assert.equal(context.graphLoads, 1);
});

await check('Node dialogs contain keyboard focus, dismiss with Escape, and restore the opener', () => {
    ready();
    const opener = element('new-node-btn');
    const overlay = element('modal-overlay');
    const container = element('modal-container');
    const first = element('modal-close');
    const input = element('node-id');
    const last = element('modal-submit');
    overlay.classList.add('hidden');
    opener.focus();
    container.querySelector = () => input;
    container.querySelectorAll = () => [first, input, last];
    app.openEditNodeModal();
    assert.ok(!overlay.classes.has('hidden'));
    assert.equal(context.document.activeElement, input);
    let prevented = 0;
    const key = (key, extra = {}) => app.handleKeyboardShortcut({
        key, preventDefault: () => prevented++, ...extra,
    });
    key('k', { ctrlKey: true });
    assert.equal(context.document.activeElement, input, 'Search shortcut must not move focus outside the dialog');
    last.focus();
    key('Tab');
    assert.equal(context.document.activeElement, first);
    key('Tab', { shiftKey: true });
    assert.equal(context.document.activeElement, last);
    key('Escape');
    assert.ok(overlay.classes.has('hidden'));
    assert.equal(context.document.activeElement, opener);
    assert.equal(prevented, 3);
    key('k', { ctrlKey: true });
    assert.equal(context.document.activeElement, element('graph-search-input'));
    container.querySelector = () => null;
    container.querySelectorAll = () => [];
});

console.log(`${passed} UI behavior checks passed (visual layout not tested).`);
