// Workers report time relative to the start of this run, independent of the browser clock.
const seconds = value => Number.isFinite(value) && value >= 0;

export function timingSnapshot(job, identifyNode) {
  const timing = job.stage_timings;
  if (timing?.version !== 1 || !seconds(timing.elapsed_s) || !Array.isArray(timing.entries)) return null;
  const entries = [];
  for (const entry of timing.entries) {
    if (!entry || typeof entry.stage !== 'string' || !seconds(entry.start_s)) continue;
    const active = job.state === 'running' && entry.duration_s === null;
    if (!active && !seconds(entry.duration_s)) continue;
    entries.push({label: readableStage(entry.stage, true), node: identifyNode(entry), start: entry.start_s,
      duration: entry.duration_s, state: active ? 'running' : entry.incomplete ? 'stopped' : 'done'});
  }
  return {elapsed: timing.elapsed_s, partial: timing.partial === true, entries};
}

export function readableStage(label = '', interval = false) {
  const view = /^(Preparing )?(albedo|mr)(?: at)? (-?[\d.]+) degrees(?:, step (\d+\/\d+))?$/i.exec(label);
  if (view) {
    const map = view[2].toLowerCase() === 'mr' ? 'Metallic & roughness' : 'Base color';
    return `UltraTex · ${map} · ${view[3]}°` + (view[4] ? ` · Step ${view[4]}` : view[1] && !interval ? ' · Preparation' : '');
  }
  if (label === 'Loading the UltraTex albedo diffusion model') return 'UltraTex · Loading base color weights';
  if (label === 'Loading the UltraTex mr diffusion model') return 'UltraTex · Loading metallic & roughness weights';
  if (label === 'Generating six UltraTex albedo views') return 'UltraTex · Base color · Six views';
  if (label === 'Generating six UltraTex material views') return 'UltraTex · Metallic & roughness · Six views';
  return label;
}

export function formatRunTime(value) {
  const total = Math.floor(Math.max(0, value));
  if (total === 0 && value > 0) return '<1s';
  const s = total % 60;
  if (total < 60) return s + 's';
  const m = Math.floor(total / 60) % 60;
  if (total < 3600) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${Math.floor(total / 3600)}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
}

function duration(entry, elapsed) {
  return entry.duration ?? Math.max(0, elapsed - entry.start);
}

export function timingTotals(entries, elapsed, keyFor) {
  const totals = new Map();
  for (const entry of entries) {
    const key = keyFor(entry);
    if (key == null) continue;
    const previous = totals.get(key)?.seconds || 0;
    totals.set(key, {seconds: previous + duration(entry, elapsed), state: entry.state});
  }
  return totals;
}

function display(element, text) {
  if (element.textContent !== text) element.textContent = text;
}

const states = {running: 'In progress', done: 'Completed', stopped: 'Stopped'};
function stamp(element, record, empty = '') {
  if (record) {
    element.dataset.state = record.state;
    display(element, formatRunTime(record.seconds));
    element.setAttribute('aria-label', states[record.state] + ' · ' + element.textContent);
    element.title = element.getAttribute('aria-label');
  } else {
    delete element.dataset.state;
    element.removeAttribute('aria-label');
    element.removeAttribute('title');
    display(element, empty);
  }
  element.hidden = !record && !empty;
}

export function createGraphTimers(el, nodes, phases) {
  const timing = el('details', 'workflow-timings');
  const heading = el('summary', '', 'Stage timings');
  const total = el('span', 'workflow-time-total');
  heading.append(total);
  const list = el('ol', 'workflow-time-list');
  const hint = el('p', 'workflow-time-note');
  timing.append(heading, hint, list);
  const liveTime = el('span', 'workflow-run-timer');
  const rows = [];
  let sample, state, receivedAt, interval;

  function tick() {
    if (!sample) return;
    const active = sample.entries.find(entry => entry.state === 'running');
    const extra = active ? (performance.now() - receivedAt) / 1000 : 0;
    const elapsed = sample.elapsed + extra;
    display(total, 'Total ' + formatRunTime(elapsed));
    const byNode = timingTotals(sample.entries, elapsed, entry => entry.node);
    const byPhase = timingTotals(sample.entries, elapsed, entry => nodes.get(entry.node)?.card.dataset.type);
    for (const [id, node] of nodes) stamp(node.stamp, byNode.get(id));
    for (const [type, phase] of phases) {
      stamp(phase.stamp, byPhase.get(type), state === 'running' || state === 'queued' ? 'Pending' : '—');
    }
    liveTime.hidden = !active;
    if (active) display(liveTime, 'This stage ' + formatRunTime(duration(active, elapsed)));
    if (timing.open) for (const [i, entry] of sample.entries.entries()) {
      stamp(rows[i].stamp, {seconds: duration(entry, elapsed), state: entry.state});
    }
  }

  function stop() { clearInterval(interval); interval = undefined; }
  function start() {
    stop();
    if (timing.isConnected && state === 'running' && sample) interval = setInterval(tick, 1000);
    tick();
  }
  function update(job, snapshot) {
    if (sample?.elapsed !== snapshot?.elapsed || state !== job.state || receivedAt === undefined) {
      receivedAt = performance.now();
    }
    state = job.state;
    sample = snapshot;
    timing.hidden = !sample && state === 'queued';
    list.hidden = !sample?.entries.length;
    hint.textContent = !sample ? 'Stage timings were not recorded for this run.' :
      sample.partial ? 'Times end at the last worker update.' : '';
    hint.hidden = !hint.textContent;
    while (rows.length > (sample?.entries.length || 0)) rows.pop().row.remove();
    if (sample) {
      sample.entries.forEach((entry, i) => {
        if (!rows[i]) {
          const row = el('li'), label = el('span'), stamp = el('span', 'workflow-time-stamp');
          row.append(label, stamp);
          list.append(row);
          rows.push({row, label, stamp});
        }
        display(rows[i].label, entry.label);
      });
    } else {
      total.textContent = ['done', 'error', 'cancelled'].includes(state) && seconds(job.elapsed) ?
        'Total ' + formatRunTime(job.elapsed) : '';
      liveTime.hidden = true;
      for (const node of nodes.values()) stamp(node.stamp, null);
      for (const phase of phases.values()) stamp(phase.stamp, null, state === 'queued' ? 'Pending' : '');
    }
    start();
  }
  timing.addEventListener('toggle', tick);
  return {timing, liveTime, start, stop, update};
}
