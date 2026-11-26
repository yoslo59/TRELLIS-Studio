import {icon} from './card-menu.js';
import {workflowTopology} from './workflow-topology.js?v=standard-profiles';

const sections = [
  {title: 'Reference', type: 'reference', game: ['312','193','15','55','56','242'], detail: ['12']},
  {title: 'Geometry', type: 'geometry', game: ['316','40','319','87','3','108','125','119','18','126','199','94','23','279','117'], detail: ['44','17']},
  {title: 'Mesh finishing', type: 'mesh', game: ['325','327','328','335','337','338','341','342'], detail: ['41','11','14','20','42','22','23']},
  {title: 'Texturing', type: 'texture', game: ['368','354','355','356','357','376','374','375','387','358','364','377','384'], detail: ['25']},
];

export function groupWorkflowStages(stages) {
  const type = stages.some(stage => stage.id === '316') ? 'game' : 'detail';
  const remaining = new Map(stages.map(stage => [stage.id, stage]));
  const groups = sections.map(section => {
    const items = [];
    for (const id of section[type]) {
      if (!remaining.has(id)) continue;
      items.push(remaining.get(id));
      remaining.delete(id);
    }
    return {title: section.title, type: section.type, stages: items};
  });
  if (remaining.size) groups.push({title: 'Additional settings', type: 'geometry', stages: [...remaining.values()]});
  return groups.filter(group => group.stages.length);
}

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function tool(label, glyph, action) {
  const button = el('button', 'quiet');
  button.type = 'button';
  button.title = label;
  button.setAttribute('aria-label', label);
  button.append(icon(glyph));
  button.addEventListener('click', action);
  return button;
}

const glyphs = {reference: 'image', geometry: 'cube', mesh: 'wire', texture: 'layers', output: 'download'};
const svg = name => document.createElementNS('http://www.w3.org/2000/svg', name);
const openGraphs = new WeakMap();
const phaseNames = {reference: 'Reference', geometry: 'Geometry', mesh: 'Mesh finishing', texture: 'Texturing', output: 'Output'};

function graphNode(stage, type, renderFields) {
  const editable = stage.fields?.length > 0;
  const card = el(editable ? 'details' : 'section', 'workflow-graph-node');
  card.dataset.node = stage.id;
  card.dataset.type = type;
  card.setAttribute('aria-label', stage.title);
  const head = el(editable ? 'summary' : 'div', 'workflow-graph-node-title');
  const inlet = el('span', 'workflow-graph-inlet');
  const outlet = el('span', 'workflow-graph-outlet');
  inlet.setAttribute('aria-hidden', 'true');
  outlet.setAttribute('aria-hidden', 'true');
  head.append(icon(glyphs[type]), el('span', '', stage.title), inlet, outlet);
  card.append(head);
  if (editable) {
    card.append(renderFields(stage));
    card.open = stage.open ?? ['12','17','25','312','3','335','358'].includes(stage.id);
  }
  return {stage, card, inlet, outlet};
}

export function createWorkflowGraph(workspace, groups, profileName, renderFields, topology, {monitor = false} = {}) {
  const graph = workflowTopology(groups.flatMap(group => group.stages), topology);
  const groupTypes = new Map(groups.flatMap(group => group.stages.map(stage => [stage.id, group.type])));
  const opener = el('button', 'secondary workflow-graph-open');
  opener.type = 'button';
  opener.append(icon('grid'), 'Open workflow graph');
  opener.setAttribute('aria-expanded', 'false');

  const panel = el('section', 'workflow-graph-panel');
  panel.setAttribute('aria-label', 'Workflow graph');
  const header = el('header', 'workflow-graph-header');
  const title = el('div');
  title.append(el('h2', '', monitor ? 'Generation graph' : 'Workflow graph'), el('p', '', profileName));
  const viewport = el('div', 'workflow-graph-viewport');
  viewport.tabIndex = 0;
  viewport.setAttribute('aria-label', 'Workflow stages');
  viewport.setAttribute('aria-description', monitor ? 'Drag to pan, scroll to zoom, and expand a node to inspect its saved settings.' :
    'Drag to pan, scroll to zoom, and expand a node to edit its values.');
  const flow = el('div', 'workflow-graph-flow');
  const lines = svg('svg');
  lines.classList.add('workflow-graph-lines');
  lines.setAttribute('aria-hidden', 'true');
  flow.append(lines);
  viewport.append(flow);
  const zoomReadout = el('output', 'workflow-graph-zoom', '100%');
  const nodes = new Map();
  const underlying = new Map();
  let zoom = 1;
  let drag;
  let trigger = opener;
  let activeNode = null;
  let follow = true;

  for (const column of graph.columns) {
    const lane = el('div', 'workflow-graph-lane');
    for (const id of column) {
      const stage = graph.nodes.get(id);
      const type = stage.type || groupTypes.get(id) || 'geometry';
      const node = graphNode(stage, type, renderFields);
      nodes.set(id, node);
      lane.append(node.card);
    }
    flow.append(lane);
  }

  const cables = graph.links.map(([from, to, type, method]) => {
    const path = svg('path');
    path.dataset.type = type;
    nodes.get(from).outlet.classList.add('connected');
    nodes.get(to).inlet.classList.add('connected');
    lines.append(path);
    return {from: nodes.get(from), to: nodes.get(to), path, method};
  });

  function draw() {
    if (!panel.isConnected) return;
    const origin = flow.getBoundingClientRect();
    const points = new Map();
    for (const node of nodes.values()) {
      points.set(node, [node.inlet, node.outlet].map(socket => {
        const rect = socket.getBoundingClientRect();
        return [(rect.left + rect.width / 2 - origin.left) / zoom,
          (rect.top + rect.height / 2 - origin.top) / zoom];
      }));
    }
    for (const {from, to, path} of cables) {
      const [x1, y1] = points.get(from)[1];
      const [x2, y2] = points.get(to)[0];
      const bend = Math.max(24, Math.min(90, Math.abs(x2 - x1) * .28));
      path.setAttribute('d', `M${x1} ${y1} C${x1 + bend} ${y1} ${x2 - bend} ${y2} ${x2} ${y2}`);
    }
  }

  const layout = new ResizeObserver(draw);
  function sync(backend, textureMethod) {
    for (const {stage, card} of nodes.values()) {
      card.classList.toggle('is-inactive', Boolean((stage.geometry_backend && stage.geometry_backend !== backend) ||
        (stage.texture_method && stage.texture_method !== textureMethod)));
    }
    for (const {from, to, path, method} of cables) {
      path.classList.toggle('is-inactive', (method && method !== textureMethod) ||
        from.card.classList.contains('is-inactive') || to.card.classList.contains('is-inactive'));
    }
  }

  function setZoom(value, x = viewport.clientWidth / 2, y = viewport.clientHeight / 2) {
    const next = Math.min(2, Math.max(.22, value));
    const ratio = next / zoom;
    const left = (viewport.scrollLeft + x) * ratio - x;
    const top = (viewport.scrollTop + y) * ratio - y;
    zoom = next;
    flow.style.zoom = String(zoom);
    zoomReadout.value = `${Math.round(zoom * 100)}%`;
    viewport.scrollTo(left, top);
    draw();
  }

  function fitView() {
    const width = flow.getBoundingClientRect().width / zoom;
    setZoom(Math.min(1, (viewport.clientWidth - 6) / width), 0, 0);
    viewport.scrollTo(0, 0);
  }

  function center(card) {
    if (!card || !panel.isConnected) return;
    const box = card.getBoundingClientRect(), view = viewport.getBoundingClientRect();
    viewport.scrollBy(box.left - view.left - (view.width - box.width) / 2,
      box.top - view.top - Math.max(16, (view.height - Math.min(view.height, box.height)) / 2));
  }

  const phaseNav = el('nav', 'workflow-graph-phases');
  phaseNav.setAttribute('aria-label', 'Find workflow phase');
  for (const [type, name] of Object.entries(phaseNames)) {
    const cards = [...nodes.values()].filter(node => node.card.dataset.type === type);
    if (!cards.length) continue;
    const link = el('button', 'workflow-graph-stage', name);
    link.dataset.type = type;
    link.type = 'button';
    link.title = 'Show ' + name.toLowerCase();
    link.addEventListener('click', () => center(cards.find(node => !node.card.classList.contains('is-inactive'))?.card));
    phaseNav.append(link);
  }
  const run = el('div', 'workflow-run');
  const stateLabel = el('strong', 'workflow-run-state');
  const stageLabel = el('span', 'workflow-run-stage');
  stageLabel.setAttribute('role', 'status');
  const percentLabel = el('span', 'workflow-run-percent');
  const followButton = el('button', 'quiet workflow-follow', 'Follow stage');
  followButton.prepend(icon('compass'));
  followButton.type = 'button';
  followButton.setAttribute('aria-pressed', 'true');
  followButton.addEventListener('click', () => {
    follow = !follow;
    followButton.setAttribute('aria-pressed', String(follow));
    if (follow) center(activeNode?.card);
  });
  if (monitor) {
    panel.dataset.monitor = 'true';
    run.append(stateLabel, stageLabel, percentLabel, followButton);
  }

  function setRun(job, nodeId) {
    const state = {queued: 'Queued', running: 'Running', done: 'Complete', error: 'Failed', cancelled: 'Cancelled'}[job.state] || job.state;
    stateLabel.textContent = state;
    run.dataset.state = job.state;
    const label = job.state === 'error' ? job.error || job.stage : job.stage;
    if (stageLabel.textContent !== (label || state)) stageLabel.textContent = label || state;
    percentLabel.textContent = ['running', 'done'].includes(job.state) && Number.isFinite(job.progress) ? job.progress + '%' : '';
    const next = job.state === 'running' ? nodes.get(nodeId) : null;
    if (!['queued', 'running'].includes(job.state) && document.activeElement === followButton) {
      closer.focus({preventScroll: true});
    }
    followButton.hidden = !['queued', 'running'].includes(job.state);
    if (next === activeNode) return;
    activeNode?.card.removeAttribute('aria-current');
    activeNode = next;
    activeNode?.card.setAttribute('aria-current', 'step');
    for (const cable of cables) cable.path.classList.toggle('is-current',
      Boolean(next && cable.to === next && !cable.path.classList.contains('is-inactive')));
    if (follow) center(activeNode?.card);
  }

  viewport.addEventListener('wheel', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.deltaY === 0 ||
        event.target.closest('input, select, textarea')) return;
    event.preventDefault();
    const box = viewport.getBoundingClientRect();
    setZoom(zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1), event.clientX - box.left, event.clientY - box.top);
  }, {passive: false});

  viewport.addEventListener('pointerdown', event => {
    if (event.pointerType === 'touch' || (event.button !== 0 && event.button !== 1) ||
        event.button === 0 && event.target.closest('.workflow-graph-node')) return;
    event.preventDefault();
    const {pointerId, clientX, clientY} = event;
    drag = {id: pointerId, x: clientX, y: clientY, left: viewport.scrollLeft, top: viewport.scrollTop};
    viewport.setPointerCapture(pointerId);
    viewport.classList.add('is-panning');
  });
  viewport.addEventListener('pointermove', event => {
    if (drag?.id !== event.pointerId) return;
    viewport.scrollTo(drag.left + drag.x - event.clientX, drag.top + drag.y - event.clientY);
  });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    viewport.addEventListener(type, event => {
      if (drag?.id !== event.pointerId) return;
      drag = undefined;
      viewport.classList.remove('is-panning');
    });
  }

  function close(restoreFocus = true) {
    if (!panel.isConnected) return;
    layout.disconnect();
    panel.remove();
    for (const [node, inert] of underlying) node.inert = inert;
    underlying.clear();
    openGraphs.delete(workspace);
    trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus) {
      const focusTarget = trigger.isConnected && !trigger.closest('[hidden]') ? trigger : workspace.querySelector('#view3d');
      focusTarget?.focus({preventScroll: true});
    }
  }

  const tools = el('div', 'workflow-graph-tools');
  const closer = tool('Close workflow graph', 'close', () => close());
  tools.append(tool('Zoom out', 'minus', () => setZoom(zoom / 1.1)), zoomReadout,
    tool('Zoom in', 'plus', () => setZoom(zoom * 1.1)), tool('Fit graph', 'fit', fitView), closer);
  header.append(title, tools);
  const overview = el('div', 'workflow-graph-overview');
  overview.append(header, phaseNav);
  if (monitor) overview.append(run);
  panel.append(overview, viewport);
  panel.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || panel.querySelector(':popover-open')) return;
    event.preventDefault();
    close();
  });
  function open(button = opener) {
    if (panel.isConnected) { close(); return; }
    openGraphs.get(workspace)?.(false);
    trigger = button;
    openGraphs.set(workspace, close);
    for (const node of workspace.children) {
      underlying.set(node, node.inert);
      node.inert = true;
    }
    workspace.append(panel);
    layout.observe(viewport);
    for (const node of nodes.values()) layout.observe(node.card);
    draw();
    trigger.setAttribute('aria-expanded', 'true');
    if (monitor && follow) center(activeNode?.card);
    closer.focus({preventScroll: true});
  }
  opener.addEventListener('click', () => open());
  return {opener, sync, close, open, setRun, get visible() { return panel.isConnected; }};
}
