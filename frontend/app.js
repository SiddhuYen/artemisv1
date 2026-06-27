const $ = (id) => document.getElementById(id);

let currentNetwork = {nodes: [], edges: [], stats: {}};
let selectedNodeId = 'me';
let boards = [];
let currentBoard = null;
let selectedBoardNodeId = 'me';
let selectedBoardEdgeIndex = null;
let pollTimer = null;
let dragState = null;
const CLIENT_ID_KEY = 'artemisClientId';
const clientId = localStorage.getItem(CLIENT_ID_KEY) || `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
localStorage.setItem(CLIENT_ID_KEY, clientId);

function apiFetch(url, options = {}) {
  return fetch(url, {
    ...options,
    headers: {
      ...(options.headers || {}),
      'X-Artemis-Client-Id': clientId,
    },
  });
}

function setStatus(status, label) {
  $('statusDot').className = `dot ${status || ''}`;
  $('statusText').textContent = label;
  $('runButton').disabled = status === 'running';
  const progress = $('runProgress');
  if (progress) progress.hidden = status !== 'running';
  if ($('progressTitle')) $('progressTitle').textContent = status === 'running' ? label : status === 'error' ? 'Error' : status === 'done' ? 'Complete' : 'Idle';
}

function setProgressDetail(detail) {
  if ($('progressDetail')) $('progressDetail').textContent = detail || 'Working...';
}

function escapeHtml(v) {
  return String(v || '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

function safeUrl(url) {
  const value = String(url || '').trim();
  if (!value) return '';
  try {
    const parsed = new URL(value, window.location.origin);
    return ['http:', 'https:', '/api/boards/', '/download'].some(prefix => parsed.protocol === prefix || value.startsWith(prefix)) ? value : '';
  } catch {
    return value.startsWith('/download') || value.startsWith('/api/boards/') ? value : '';
  }
}

function personLabel(p) {
  return [p?.name, p?.company, p?.position].filter(Boolean).join(' · ') || 'Unknown';
}

function initials(p) {
  const parts = String(p?.name || '?').trim().split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2)).toUpperCase();
}

function nodeClass(node) {
  return ['board-node', node.role || 'lead', node.highlighted ? 'active' : '', node.id === selectedBoardNodeId ? 'selected' : ''].filter(Boolean).join(' ');
}

function boardNodeId(person, index) {
  if (index === 0 || person?.id === 'me') return 'me';
  const raw = String(person?.profile_url || person?.name || `node-${index}`).toLowerCase();
  return raw.replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || `node-${index}`;
}

function normalizeName(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function nodeCanonicalKey(node) {
  const url = String(node?.profile_url || '').trim().split('?', 1)[0].replace(/\/$/, '').toLowerCase();
  if (url) return `url:${url}`;
  const name = normalizeName(node?.name);
  const company = normalizeName(node?.company);
  if (name && company) return `name_company:${name}:${company}`;
  return name ? `name:${name}` : '';
}

function collapseDuplicateBoardNodes(board) {
  const canonical = new Map();
  const idMap = new Map();
  const nodes = [];
  for (const node of board.nodes || []) {
    const nameKey = normalizeName(node.name) ? `name:${normalizeName(node.name)}` : '';
    const key = nodeCanonicalKey(node);
    const existing = canonical.get(key) || canonical.get(nameKey);
    if (existing && node.id !== 'me') {
      idMap.set(node.id, existing.id);
      existing.route_count = Number(existing.route_count || 0) + Number(node.route_count || 0);
      existing.highlighted = Boolean(existing.highlighted || node.highlighted);
      if (!existing.dossier_path && node.dossier_path) existing.dossier_path = node.dossier_path;
      if (!existing.matches_path && node.matches_path) existing.matches_path = node.matches_path;
      continue;
    }
    nodes.push(node);
    if (key) canonical.set(key, node);
    if (nameKey) canonical.set(nameKey, node);
  }
  board.nodes = nodes;
  board.edges = (board.edges || []).map(edge => ({
    ...edge,
    source: idMap.get(edge.source) || edge.source,
    target: idMap.get(edge.target) || edge.target,
  })).filter(edge => edge.source !== edge.target);
}

function boardPositions(nodes, width, height) {
  const byDepth = {};
  nodes.forEach(node => {
    if (Number.isFinite(Number(node.x)) && Number.isFinite(Number(node.y))) return;
    let depth = Number(node.depth || 1);
    if (node.role === 'me' || node.id === 'me') depth = 0;
    if (node.role === 'target') depth = 5;
    if (node.role === 'sub_target') depth = Math.max(3, Math.min(depth, 4));
    if (node.role === 'cold_approach') depth = Math.max(2, Math.min(depth, 4));
    if (node.role === 'gateway' || node.role === 'ecosystem') depth = Math.max(2, Math.min(depth, 4));
    node._layoutDepth = depth;
    (byDepth[depth] ||= []).push(node);
  });
  const depths = Object.keys(byDepth).map(Number).sort((a, b) => a - b);
  const pos = new Map();
  depths.forEach((depth, depthIndex) => {
    const group = byDepth[depth];
    const x = 70 + (depth / 5) * (width - 190);
    group.forEach((node, index) => {
      const y = 70 + ((index + 1) / (group.length + 1)) * (height - 160);
      pos.set(node.id, {x, y});
    });
  });
  return pos;
}

function ensureBoardPositions(board) {
  const nodes = board.nodes || [];
  const width = Math.max(1160, window.innerWidth - 420);
  const height = Math.max(720, window.innerHeight - 82);
  const generated = boardPositions(nodes, width, height);
  nodes.forEach(node => {
    if (!Number.isFinite(Number(node.x)) || !Number.isFinite(Number(node.y))) {
      const p = generated.get(node.id) || {x: 80, y: 120};
      node.x = Math.round(p.x);
      node.y = Math.round(p.y);
    }
  });
}

function normalizeBoard(board) {
  if (!board) return null;
  board.nodes ||= [];
  board.edges ||= [];
  if (!board.nodes.some(node => node.id === 'me')) {
    board.nodes.unshift({id: 'me', name: 'You', depth: 0, role: 'me', highlighted: true});
  }
  if (board.target && !board.nodes.some(node => node.role === 'target')) {
    board.nodes.push({
      id: boardNodeId({name: board.target}, 999),
      name: board.target,
      company: board.context || '',
      position: 'Target',
      depth: 5,
      role: 'target',
      source: 'target',
      highlighted: true,
      route_count: 0,
    });
  }
  collapseDuplicateBoardNodes(board);
  ensureBoardPositions(board);
  return board;
}

async function loadBoards(preferredId = '') {
  const res = await apiFetch('/api/boards');
  const data = await res.json();
  boards = data.boards || [];
  currentBoard = normalizeBoard(preferredId ? await fetchBoard(preferredId) : data.current);
  renderBoardPicker();
  renderBoard();
}

async function fetchBoard(id) {
  const res = await apiFetch(`/api/boards/${encodeURIComponent(id)}`);
  return res.ok ? res.json() : null;
}

function renderBoardPicker() {
  $('boardSelect').innerHTML = boards.map(board => `<option value="${escapeHtml(board.id)}">${escapeHtml(board.name || board.target || 'Untitled Board')}</option>`).join('');
  if (currentBoard) {
    $('boardSelect').value = currentBoard.id;
    $('boardName').value = currentBoard.name || currentBoard.target || 'Untitled Board';
    $('exportBoard').href = `/api/boards/${encodeURIComponent(currentBoard.id)}/csv`;
  }
  renderManualEdgeOptions();
}

function renderBoard() {
  currentBoard = normalizeBoard(currentBoard);
  if (!currentBoard) return;
  const nodes = currentBoard.nodes || [];
  const edges = currentBoard.edges || [];
  const width = Math.max(1160, window.innerWidth - 420);
  const height = Math.max(720, window.innerHeight - 82);
  ensureBoardPositions(currentBoard);
  const pos = new Map(nodes.map(node => [node.id, {x: Number(node.x || 0), y: Number(node.y || 0)}]));
  const edgeSvg = edges.map((edge, edgeIndex) => {
    const a = pos.get(edge.source), b = pos.get(edge.target);
    if (!a || !b) return '';
    const mid = (a.x + b.x) / 2;
    const cls = ['board-edge', edge.type || 'candidate', edge.highlighted ? 'active' : ''].filter(Boolean).join(' ');
    const d = `M ${a.x + 31} ${a.y + 31} C ${mid} ${a.y + 31}, ${mid} ${b.y + 31}, ${b.x + 31} ${b.y + 31}`;
    return `<path class="${cls}" d="${d}"></path><path class="board-edge-hit" data-board-edge="${edgeIndex}" d="${d}"></path>`;
  }).join('');
  const nodeHtml = nodes.map(node => {
    const p = pos.get(node.id);
    return `<button class="${nodeClass(node)}" data-board-node="${escapeHtml(node.id)}" style="left:${p.x}px;top:${p.y}px" title="${escapeHtml(personLabel(node))}">${escapeHtml(initials(node))}</button><div class="board-label" data-board-label="${escapeHtml(node.id)}" style="left:${p.x}px;top:${p.y + 72}px"><strong>${escapeHtml(node.name || 'Unknown')}</strong>${node.company ? escapeHtml(node.company) : ''}</div>`;
  }).join('');
  $('boardCanvas').innerHTML = `<div class="board-map" style="width:${width}px;height:${height}px"><svg class="edge-layer" viewBox="0 0 ${width} ${height}">${edgeSvg}</svg>${nodeHtml}</div>`;
  document.querySelectorAll('[data-board-node]').forEach(el => el.onclick = () => selectBoardNode(el.dataset.boardNode));
  document.querySelectorAll('[data-board-node]').forEach(el => el.onpointerdown = startNodeDrag);
  document.querySelectorAll('[data-board-edge]').forEach(el => el.onclick = () => selectBoardEdge(Number(el.dataset.boardEdge)));
  selectBoardNode(selectedBoardNodeId, false);
  renderManualEdgeOptions();
}

function selectBoardNode(id, rerender = true) {
  selectedBoardNodeId = id || 'me';
  selectedBoardEdgeIndex = null;
  if (rerender) renderBoard();
  const node = (currentBoard?.nodes || []).find(item => item.id === selectedBoardNodeId);
  $('nodeInspector').innerHTML = renderNodeInspector(node);
}

function selectBoardEdge(index) {
  selectedBoardEdgeIndex = index;
  selectedBoardNodeId = '';
  const edge = (currentBoard?.edges || [])[index];
  $('nodeInspector').innerHTML = renderEdgeInspector(edge);
}

function downloadLink(path, label) {
  if (!path) return '';
  const url = `/download?path=${encodeURIComponent(path)}`;
  return `<a class="secondary button-link" href="${escapeHtml(url)}">${escapeHtml(label)}</a>`;
}

function renderNodeInspector(node) {
  if (!node) return '<div class="inspector-empty">Select a node.</div>';
  const canRun = node.id !== 'me' && node.name;
  const profile = safeUrl(node.profile_url);
  return `
    <div class="inspector-head">
      <h2>${escapeHtml(node.name || 'Unknown')}</h2>
      <span>${escapeHtml(node.role || 'lead')}</span>
    </div>
    <div class="inspector-body">
      <p>${escapeHtml([node.company, node.position].filter(Boolean).join(' · '))}</p>
      ${profile ? `<p><a href="${escapeHtml(profile)}" target="_blank" rel="noreferrer">${escapeHtml(profile)}</a></p>` : ''}
      <p><strong>Source:</strong> ${escapeHtml(node.source || 'board')}</p>
      <p><strong>Routes:</strong> ${escapeHtml(node.route_count || 0)}</p>
      <div class="inspector-actions">
        ${canRun ? `<button class="primary" type="button" id="runNode">Run Toward Node</button>` : ''}
        ${downloadLink(node.dossier_path, 'Dossier')}
        ${downloadLink(node.matches_path, 'Report')}
      </div>
    </div>`;
}

function renderEdgeInspector(edge) {
  if (!edge) return '<div class="inspector-empty">Select a link.</div>';
  const source = (currentBoard?.nodes || []).find(node => node.id === edge.source) || {};
  const target = (currentBoard?.nodes || []).find(node => node.id === edge.target) || {};
  const showReasons = currentBoard?.show_link_reasons;
  const sourceUrl = safeUrl(edge.source_url);
  const targetUrl = safeUrl(edge.target_source_url);
  return `
    <div class="inspector-head">
      <h2>${escapeHtml(source.name || 'Source')} -> ${escapeHtml(target.name || 'Target')}</h2>
      <span>${escapeHtml(edge.confidence || edge.type || 'link')}</span>
    </div>
    <div class="inspector-body">
      ${showReasons ? `
        <p><strong>Why this link exists:</strong> ${escapeHtml(edge.reason || 'This link was added by the verified-hop workflow.')}</p>
        ${edge.evidence ? `<p><strong>Evidence:</strong> ${escapeHtml(edge.evidence)}</p>` : ''}
        ${edge.relationship_type ? `<p><strong>Relationship:</strong> ${escapeHtml(edge.relationship_type)}</p>` : ''}
        <div class="inspector-actions">
          ${sourceUrl ? `<a class="secondary button-link" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">Source</a>` : ''}
          ${targetUrl ? `<a class="secondary button-link" href="${escapeHtml(targetUrl)}" target="_blank" rel="noreferrer">Target source</a>` : ''}
        </div>
      ` : '<p>Turn on Verify hops before running to attach source-backed reasoning to links.</p>'}
    </div>`;
}

function renderManualEdgeOptions() {
  if (!$('manualEdgeSource') || !$('manualEdgeTarget')) return;
  const options = (currentBoard?.nodes || []).map(node => `<option value="${escapeHtml(node.id)}">${escapeHtml(node.name || node.id)}</option>`).join('');
  $('manualEdgeSource').innerHTML = options;
  $('manualEdgeTarget').innerHTML = options;
}

function markBoardDirty() {
  if (!currentBoard) return;
  currentBoard.saved = false;
  setStatus('', 'Unsaved');
}

function uniqueManualNodeId(name) {
  const base = `manual-${boardNodeId({name}, Date.now())}`;
  const existing = new Set((currentBoard?.nodes || []).map(node => node.id));
  let id = base;
  let index = 2;
  while (existing.has(id)) {
    id = `${base}-${index}`;
    index += 1;
  }
  return id;
}

function addManualBoardNode(payload) {
  currentBoard = normalizeBoard(currentBoard);
  const node = {
    id: uniqueManualNodeId(payload.name),
    name: payload.name,
    company: payload.company || '',
    position: payload.position || '',
    profile_url: payload.profile_url || '',
    depth: 2,
    role: 'manual',
    source: 'manual',
    highlighted: false,
    route_count: 0,
    x: 180,
    y: 160 + ((currentBoard.nodes || []).length % 6) * 92,
  };
  currentBoard.nodes.push(node);
  selectedBoardNodeId = node.id;
  markBoardDirty();
  renderBoard();
}

function addManualBoardEdge(payload) {
  if (!payload.source || !payload.target || payload.source === payload.target) return;
  currentBoard.edges ||= [];
  const key = `${payload.source}->${payload.target}:manual-${Date.now()}`;
  currentBoard.edges.push({
    key,
    source: payload.source,
    target: payload.target,
    route: 'manual',
    type: 'manual',
    highlighted: false,
    reason: payload.reason || 'Manually added board connection.',
    evidence: payload.reason || '',
    source_url: payload.source_url || '',
    confidence: 'manual',
  });
  markBoardDirty();
  renderBoard();
}

function startNodeDrag(event) {
  if (event.button !== 0) return;
  const id = event.currentTarget.dataset.boardNode;
  const node = (currentBoard?.nodes || []).find(item => item.id === id);
  if (!node) return;
  dragState = {
    id,
    node,
    startX: event.clientX,
    startY: event.clientY,
    nodeX: Number(node.x || 0),
    nodeY: Number(node.y || 0),
    moved: false,
  };
  event.currentTarget.setPointerCapture(event.pointerId);
  event.currentTarget.onpointermove = dragNode;
  event.currentTarget.onpointerup = endNodeDrag;
  event.currentTarget.onpointercancel = endNodeDrag;
}

function dragNode(event) {
  if (!dragState) return;
  const dx = event.clientX - dragState.startX;
  const dy = event.clientY - dragState.startY;
  if (Math.abs(dx) + Math.abs(dy) > 3) dragState.moved = true;
  dragState.node.x = Math.max(10, Math.round(dragState.nodeX + dx));
  dragState.node.y = Math.max(10, Math.round(dragState.nodeY + dy));
  const nodeEl = document.querySelector(`[data-board-node="${CSS.escape(dragState.id)}"]`);
  const labelEl = document.querySelector(`[data-board-label="${CSS.escape(dragState.id)}"]`);
  if (nodeEl) {
    nodeEl.style.left = `${dragState.node.x}px`;
    nodeEl.style.top = `${dragState.node.y}px`;
  }
  if (labelEl) {
    labelEl.style.left = `${dragState.node.x}px`;
    labelEl.style.top = `${dragState.node.y + 72}px`;
  }
}

function endNodeDrag(event) {
  if (!dragState) return;
  const moved = dragState.moved;
  event.currentTarget.onpointermove = null;
  event.currentTarget.onpointerup = null;
  event.currentTarget.onpointercancel = null;
  if (moved) {
    markBoardDirty();
    renderBoard();
  }
  dragState = null;
}

function payloadForRun(person, context = '') {
  return {
    board_id: currentBoard?.id,
    person,
    context,
    max_results: Number($('maxResults').value || 8),
    max_pages: Number($('maxPages').value || 8),
    max_adjacent_queries: Number($('adjacent').value || 20),
    match_limit: Number($('matchLimit').value || 50),
    search_provider: $('searchProvider').value,
    use_apify_instagram: $('useApifyInstagram').checked,
    allow_insecure_ssl: $('allowInsecureSsl').checked,
    no_adjacent_pass: $('skipAdjacent').checked,
    no_institution_pass: $('skipInstitution').checked,
    no_verify_hops: !$('verifyHops').checked,
    no_seed_map: !$('seedMap').checked,
    cache_days: Number($('cacheDays').value || 30),
    force_refresh: $('forceRefresh').checked,
  };
}

async function runResearch(person, context = '') {
  if (!currentBoard) await loadBoards();
  setStatus('running', 'Starting');
  setProgressDetail(`Preparing research for ${person}.`);
  const res = await apiFetch('/api/research', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payloadForRun(person, context))});
  const data = await res.json();
  if (!res.ok) {
    setStatus('error', 'Error');
    $('nodeInspector').innerHTML = `<div class="inspector-empty">${escapeHtml(data.error || 'Research failed.')}</div>`;
    return;
  }
  pollJob(data.job_id);
}

async function pollJob(id) {
  if (pollTimer) clearTimeout(pollTimer);
  const res = await apiFetch(`/api/jobs/${id}`);
  const job = await res.json();
  setStatus(job.status === 'done' ? 'done' : job.status === 'error' ? 'error' : 'running', job.status === 'done' ? 'Complete' : job.status === 'error' ? 'Error' : 'Running');
  setProgressDetail(job.message || (job.log || []).slice(-1)[0] || 'Working...');
  if (job.board) {
    currentBoard = normalizeBoard(job.board);
    await refreshBoardListOnly();
    renderBoardPicker();
    renderBoard();
  }
  if (job.status === 'running' || job.status === 'queued') pollTimer = setTimeout(() => pollJob(id), 1800);
  if (job.status === 'error') $('nodeInspector').innerHTML = `<div class="inspector-empty">${escapeHtml(job.message || 'Research failed.')}</div>`;
}

async function refreshBoardListOnly() {
  const res = await apiFetch('/api/boards');
  const data = await res.json();
  boards = data.boards || [];
}

document.querySelectorAll('.nav-btn').forEach(btn => btn.onclick = () => {
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b === btn));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === btn.dataset.view));
  if (btn.dataset.view === 'graphView') renderFullGraph();
});

$('boardSelect').onchange = async () => {
  currentBoard = normalizeBoard(await fetchBoard($('boardSelect').value));
  selectedBoardNodeId = 'me';
  renderBoardPicker();
  renderBoard();
};

$('newBoard').onclick = async () => {
  const name = prompt('Board name', 'Untitled Board') || 'Untitled Board';
  const res = await apiFetch('/api/boards', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name})});
  currentBoard = normalizeBoard(await res.json());
  await loadBoards(currentBoard.id);
};

$('saveBoard').onclick = async () => {
  if (!currentBoard) return;
  currentBoard.name = $('boardName').value.trim() || 'Untitled Board';
  const res = await apiFetch(`/api/boards/${encodeURIComponent(currentBoard.id)}/save`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: currentBoard.name, board: currentBoard})});
  currentBoard = normalizeBoard(await res.json());
  await loadBoards(currentBoard.id);
  setStatus('done', 'Saved');
};

$('manualBoardNodeForm').onsubmit = (e) => {
  e.preventDefault();
  addManualBoardNode({
    name: $('manualBoardName').value.trim(),
    company: $('manualBoardCompany').value.trim(),
    position: $('manualBoardPosition').value.trim(),
    profile_url: $('manualBoardUrl').value.trim(),
  });
  $('manualBoardNodeForm').reset();
};

$('manualBoardEdgeForm').onsubmit = (e) => {
  e.preventDefault();
  addManualBoardEdge({
    source: $('manualEdgeSource').value,
    target: $('manualEdgeTarget').value,
    reason: $('manualEdgeReason').value.trim(),
    source_url: $('manualEdgeUrl').value.trim(),
  });
  $('manualEdgeReason').value = '';
  $('manualEdgeUrl').value = '';
};

$('researchForm').onsubmit = async (e) => {
  e.preventDefault();
  await runResearch($('person').value.trim(), $('context').value.trim());
};

$('nodeInspector').onclick = async (e) => {
  if (e.target?.id !== 'runNode') return;
  const node = (currentBoard?.nodes || []).find(item => item.id === selectedBoardNodeId);
  if (!node) return;
  $('person').value = node.name || '';
  $('context').value = node.company || node.position || '';
  await runResearch(node.name, node.company || node.position || '');
};

async function loadNetwork() {
  const res = await fetch('/api/network');
  currentNetwork = await res.json();
  renderNetworkPanel();
  renderFullGraph();
}

function renderNetworkPanel() {
  const s = currentNetwork.stats || {};
  $('networkStats').innerHTML = [['Nodes', s.nodes || 0], ['Edges', s.edges || 0], ['First-degree', s.first_degree || 0], ['2nd+ degree', s.second_degree_plus || 0]].map(([k, v]) => `<div class="stat"><strong>${v}</strong><span>${k}</span></div>`).join('');
  const nodes = [...(currentNetwork.nodes || [])].sort((a, b) => (a.depth || 0) - (b.depth || 0) || String(a.name).localeCompare(String(b.name)));
  $('connectionList').innerHTML = nodes.map(n => `<div class="person-card ${n.id === selectedNodeId ? 'active' : ''}" data-id="${escapeHtml(n.id)}"><strong>${escapeHtml(n.name)}</strong><span>${escapeHtml([n.company, n.position].filter(Boolean).join(' · '))}</span><span>${escapeHtml(n.profile_url || '')}</span></div>`).join('');
  document.querySelectorAll('.person-card').forEach(card => card.onclick = () => { selectedNodeId = card.dataset.id; renderNetworkPanel(); });
  const selected = nodes.find(n => n.id === selectedNodeId) || nodes[0];
  if (selected) {
    selectedNodeId = selected.id;
    $('selectedCard').innerHTML = `<h2>${escapeHtml(selected.name)}</h2><p>${escapeHtml([selected.company, selected.position].filter(Boolean).join(' · '))}</p><p><code>${escapeHtml(selected.profile_url || selected.id)}</code></p><p>Depth: ${escapeHtml(selected.depth)}</p>`;
  }
}

function graphPositions(nodes, width, height) {
  const byDepth = {};
  nodes.forEach(n => { const d = Number(n.depth || 0); (byDepth[d] ||= []).push(n); });
  const depths = Object.keys(byDepth).map(Number).sort((a, b) => a - b);
  const pos = new Map();
  depths.forEach((d, di) => {
    const group = byDepth[d];
    group.forEach((n, i) => {
      const x = 60 + (di / (Math.max(depths.length - 1, 1))) * (width - 140);
      const y = 50 + ((i + 1) / (group.length + 1)) * (height - 120);
      pos.set(n.id, {x, y});
    });
  });
  return pos;
}

function renderGraph(containerId, nodes, edges, options = {}) {
  edges = Array.isArray(edges) ? edges : [];
  const width = 1100, height = Math.max(560, nodes.length * 18);
  const pos = graphPositions(nodes, width, height);
  const edgeSvg = edges.map(e => {
    const a = pos.get(e.source), b = pos.get(e.target);
    if (!a || !b) return '';
    const mid = (a.x + b.x) / 2;
    return `<path d="M ${a.x + 29} ${a.y + 29} C ${mid} ${a.y + 29}, ${mid} ${b.y + 29}, ${b.x} ${b.y + 29}" fill="none" stroke="#cbd5e1" stroke-width="2"></path>`;
  }).join('');
  const nodeHtml = nodes.map(n => {
    const p = pos.get(n.id);
    const cls = n.id === 'me' ? 'me' : options.targetId === n.id ? 'target' : '';
    return `<div class="node ${cls}" style="left:${p.x}px;top:${p.y}px" title="${escapeHtml(personLabel(n))}">${escapeHtml(initials(n))}</div><div class="node-label" style="left:${p.x}px;top:${p.y + 64}px">${escapeHtml(n.name)}${n.company ? '<br>' + escapeHtml(n.company) : ''}</div>`;
  }).join('');
  $(containerId).innerHTML = `<div class="network-map"><div class="network-canvas" style="height:${height}px"><svg class="edge-layer" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${edgeSvg}</svg>${nodeHtml}</div></div>`;
}

function renderFullGraph() {
  renderGraph('fullGraph', currentNetwork.nodes || [], currentNetwork.edges || []);
}

$('uploadForm').onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData();
  fd.append('file', $('csvFile').files[0]);
  fd.append('parent_id', $('uploadMode').value === 'selected' ? selectedNodeId : 'me');
  fd.append('replace_root', $('uploadMode').value === 'root' ? '1' : '0');
  setStatus('running', 'Uploading');
  const res = await fetch('/api/network/upload', {method: 'POST', body: fd});
  const data = await res.json();
  setStatus(res.ok ? 'done' : 'error', res.ok ? 'Uploaded' : 'Error');
  await loadNetwork();
  alert(res.ok ? `Added ${data.added} connections.` : data.error);
};

$('manualForm').onsubmit = async (e) => {
  e.preventDefault();
  const payload = {parent_id: selectedNodeId, name: $('manualName').value, company: $('manualCompany').value, position: $('manualPosition').value, profile_url: $('manualUrl').value};
  const res = await fetch('/api/network/manual', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  const data = await res.json();
  if (!res.ok) alert(data.error);
  $('manualForm').reset();
  await loadNetwork();
};

$('resetGraph').onclick = async () => {
  if (!confirm('Reset the whole graph?')) return;
  await fetch('/api/network/reset', {method: 'POST'});
  selectedNodeId = 'me';
  await loadNetwork();
};

window.addEventListener('resize', () => renderBoard());

loadBoards();
loadNetwork();
