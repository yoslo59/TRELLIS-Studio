import {button, dialog, el, field, input, select, action} from './workbench-ui.js?v=3.3.5';

const PRIORITIES = [['2', 'Highest'], ['1', 'High'], ['0', 'Normal'], ['-1', 'Low'], ['-2', 'Lowest']];
const STATE_ORDER = {running: 0, queued: 1, error: 2, cancelled: 2, done: 3};
const json = (method, data) => ({
  method,
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(data),
});
const resolutionChoices = values => values.map(value => [String(value), value + '³']);
const textureChoices = values => values.map(value => [String(value), value / 1024 + 'K']);
const heightInput = value => input('number', value, {min: '.01', max: 100, step: 'any', required: ''});
const triangleInput = (value, schema) => input('number', value, {
  min: schema.triangle_min, max: schema.triangle_max, step: 1, required: '',
});
const prioritySelect = value => select(PRIORITIES, String(value));
const checkbox = checked => {
  const control = input('checkbox', '');
  control.checked = checked;
  return control;
};

export function createQueueStudio({api, refresh}) {
  const ui = dialog('queueDialog', 'Generation queue', 'LOCAL COMPUTE',
    'Prepare a batch, then let the studio process one model at a time.', 'queue-dialog');
  const tabs = el('div', 'wb-tabs');
  const listTab = button('Queue', 'list', 'active');
  const composeTab = button('Prepare batch', 'plus');
  tabs.append(listTab, composeTab);
  const queue = el('section', 'queue-list');
  const compose = el('section', 'queue-compose');
  ui.body.append(tabs, queue, compose);

  const picker = input('file', '', {accept: 'image/png,image/jpeg,image/webp', multiple: ''});
  picker.hidden = true;
  const add = button('Choose reference images', 'image', 'primary', () => picker.click());
  const globalProfile = select([], 'game_d');
  const globalHeight = heightInput(1);
  const defaults = el('div', 'queue-defaults');
  defaults.append(field('Default profile', globalProfile), field('Default height · m', globalHeight), add, picker);
  const rows = el('div', 'queue-drafts');
  compose.append(defaults, el('p', 'hint',
    'Each image keeps its own name, profile, size, seed and priority. Open its settings to fine-tune the recipe.'), rows);

  const submit = button('Add batch to queue', 'plus', 'primary');
  const pause = button('Pause queue', 'pause');
  const info = el('span', 'wb-footnote');
  ui.foot.append(info, pause, submit);
  const recipe = dialog('queueRecipeDialog', 'Image settings', 'QUEUE RECIPE',
    'These settings apply only to this queued image.', 'recipe-dialog');
  const retry = dialog('queueRetryDialog', 'Edit and retry', 'NEW ATTEMPT',
    'Adjust the saved recipe and add a new attempt to the queue.', 'recipe-dialog');
  const renderedJobs = new Map();
  let drafts = [], profiles = [], snapshot = null, timer = null, pollNumber = 0;

  function updateFooter() {
    if (compose.hidden) {
      const waiting = snapshot?.items.filter(item => item.state === 'queued').length ?? 0;
      info.textContent = (snapshot?.paused ? 'Queue paused · ' : '') + waiting + ' waiting';
    } else {
      info.textContent = drafts.length + (drafts.length === 1 ? ' image prepared' : ' images prepared')
        + (snapshot?.paused ? ' · queue paused' : '');
    }
  }

  function tab(edit) {
    queue.hidden = edit;
    compose.hidden = !edit;
    listTab.classList.toggle('active', !edit);
    composeTab.classList.toggle('active', edit);
    submit.hidden = !edit;
    pause.hidden = edit;
    updateFooter();
  }

  listTab.onclick = () => tab(false);
  composeTab.onclick = () => tab(true);

  async function capabilities() {
    const [runtime, schema] = await Promise.all([api('/api/status'), api('/api/generation-options')]);
    return {runtime, schema};
  }

  async function editDraft(row) {
    const {runtime, schema} = await capabilities();
    const removeBg = checkbox(row.remove_bg);
    const triangles = triangleInput(row.triangles, schema);
    const texture = select(textureChoices(runtime.texture_sizes), String(row.texture_size));
    const resolution = select(resolutionChoices(runtime.supported_resolutions), String(row.resolution));
    const apply = button('Apply settings', 'check', 'primary', () => {
      if (!triangles.reportValidity()) return;
      if (!texture.value || !resolution.value) {
        recipe.notice.textContent = 'Choose a supported texture size and geometry resolution.';
        return;
      }
      Object.assign(row, {triangles: triangles.valueAsNumber, texture_size: Number(texture.value),
        resolution: Number(resolution.value), remove_bg: removeBg.checked});
      recipe.close();
    });
    const dense = profiles.find(profile => profile.id === row.profile_id)?.settings.preset === 'dense_master';
    recipe.body.replaceChildren(field('Geometry resolution', resolution),
      field(dense ? 'Maximum triangles' : 'Triangle target', triangles),
      field('Texture size', texture), field('Remove image background', removeBg), el('p', 'hint',
        'Sampling and finishing controls come from this image’s profile. Save a custom profile in Create for a different recipe.'));
    recipe.foot.replaceChildren(apply);
    recipe.show();
  }

  function applyProfile(row, id) {
    const profile = profiles.find(item => item.id === id);
    if (!profile) return;
    const {remove_bg, triangles, texture_size, resolution, advanced} = profile.settings;
    Object.assign(row, {profile_id: id, remove_bg, triangles, texture_size, resolution,
      advanced_settings: structuredClone(advanced)});
  }

  function renderDrafts() {
    rows.replaceChildren();
    for (const row of drafts) {
      const card = el('article', 'queue-draft');
      const img = el('img');
      img.src = row.url;
      img.alt = 'Reference for ' + row.name;
      const details = el('div', 'queue-draft-main');
      const title = input('text', row.name, {maxlength: 80, 'aria-label': 'Queued model name'});
      const profile = select(profiles.map(item => [item.id, item.name]), row.profile_id);
      const height = heightInput(row.height_m);
      const seed = input('number', row.seed, {min: 0, max: 2147483647, step: 1, required: ''});
      const priority = prioritySelect(row.priority);
      title.oninput = () => row.name = title.value;
      profile.onchange = () => applyProfile(row, profile.value);
      height.oninput = () => row.height_m = height.valueAsNumber;
      seed.oninput = () => row.seed = seed.valueAsNumber;
      priority.onchange = () => row.priority = Number(priority.value);
      const knobs = el('div', 'queue-knobs');
      knobs.append(field('Profile', profile), field('Height · m', height), field('Seed', seed), field('Priority', priority));
      details.append(title, knobs);

      const settings = button('Settings', 'sliders', 'quiet');
      settings.onclick = () => action(ui, settings, () => editDraft(row));
      const remove = button('Remove', 'trash', 'quiet', () => {
        URL.revokeObjectURL(row.url);
        drafts = drafts.filter(draft => draft !== row);
        renderDrafts();
      });
      const actions = el('div', 'queue-row-actions');
      actions.append(settings, remove);
      card.append(img, details, actions);
      rows.append(card);
    }
    submit.disabled = !drafts.length;
    updateFooter();
  }

  picker.onchange = () => {
    ui.notice.textContent = '';
    for (const file of picker.files) {
      if (drafts.length >= 30) {
        ui.notice.textContent = 'Prepare up to 30 images per batch.';
        break;
      }
      if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 25 * 1024 ** 2) {
        ui.notice.textContent = 'Use PNG, JPEG or WebP images up to 25 MB.';
        continue;
      }
      const row = {file, url: URL.createObjectURL(file), name: file.name.replace(/\.[^.]+$/, ''),
        height_m: globalHeight.valueAsNumber, seed: 42 + drafts.length, priority: 0};
      applyProfile(row, globalProfile.value);
      drafts.push(row);
    }
    picker.value = '';
    renderDrafts();
  };

  submit.onclick = () => action(ui, submit, async () => {
    const invalid = [...rows.querySelectorAll('input')].find(control => !control.validity.valid);
    if (invalid) {
      invalid.reportValidity();
      return;
    }
    const form = new FormData();
    form.set('specifications', JSON.stringify(drafts.map(({file, url, ...spec}) => spec)));
    for (const row of drafts) form.append('images', row.file);
    await api('/api/queue/batch', {method: 'POST', body: form});
    drafts.forEach(row => URL.revokeObjectURL(row.url));
    drafts = [];
    renderDrafts();
    tab(false);
    await poll();
    await refresh();
  });

  pause.onclick = () => action(ui, pause, async () => {
    await api('/api/queue', json('PATCH', {paused: !snapshot?.paused}));
    await poll();
  });

  async function editRetry(item) {
    const saved = item.settings;
    const pixal = saved.engine === 'pixal3d';
    const refinish = saved.source_kind === 'refinish';
    const adaptiveRefinish = refinish && saved.adaptive_finishing;
    const [{runtime, schema}, components] = await Promise.all([
      capabilities(),
      refinish ? api('/api/components') : null,
    ]);
    const canBakeNormals = !!components?.blender;
    const fixedResolution = refinish || pixal;
    const resolutions = refinish ? [saved.resolution ?? 512] : pixal ? [1024] : runtime.supported_resolutions;
    const textures = pixal && !refinish ? runtime.pixal3d.texture_sizes : runtime.texture_sizes;
    const resolution = select(resolutionChoices(resolutions), String(saved.resolution ?? 512));
    resolution.disabled = fixedResolution;
    const texture = select(textureChoices(textures), String(saved.texture_size));
    const triangles = triangleInput(saved.triangles, schema);
    const height = heightInput(saved.height_m);
    const priority = prioritySelect(item.priority);
    const canRepairSurface = runtime.features?.includes('repair_surface') &&
      (refinish || saved.preset === 'dense_master');
    const repairSurface = checkbox(!!saved.repair_surface);
    const finishing = saved.repair_surface ? saved.generation_options : item.retry_finishing;
    const method = select([['remesh', 'Rebuild surface'], ['direct', 'Simplify existing surface']], finishing?.mesh_mode);
    const preSimplify = checkbox(finishing?.pre_simplify);
    const repair = checkbox(finishing?.repair_holes);
    const bakeNormals = checkbox(!!saved.bake_normals);

    const title = el('strong', '', item.name);
    const failure = el('p', 'queue-failure', item.error || item.stage);
    const context = 'Seed ' + saved.seed + (pixal ? ' · ' + (saved.views?.length ?? 0) + ' saved views' : ' · Saved reference');
    retry.body.replaceChildren(title, failure, el('p', 'hint', context),
      field('Geometry resolution', resolution, refinish ? 'Uses the saved geometry cache.' : pixal ? 'Pixal3D uses 1024³.' : ''),
      field(saved.adaptive_finishing ? 'Maximum triangles' : 'Triangle target', triangles), field('Texture size', texture), field('Height · m', height),
      field('Priority', priority));
    if (canRepairSurface) {
      retry.body.append(field('Repair holes', repairSurface, 'Rebuild the surface before simplifying.'));
    }
    if (finishing?.mesh_mode && !adaptiveRefinish) {
      const legacyFields = [field('Finishing method', method), field('Pre-simplify dense meshes to 2M', preSimplify),
        field('Fill small holes', repair)];
      const syncFinishing = () => {
        for (const control of legacyFields) control.hidden = repairSurface.checked;
      };
      repairSurface.addEventListener('change', syncFinishing);
      syncFinishing();
      retry.body.append(...legacyFields);
    }
    if (refinish && (canBakeNormals || saved.bake_normals)) {
      retry.body.append(field('Transfer master detail to a normal map', bakeNormals,
        canBakeNormals ? '' : 'Blender is unavailable. Clear this option to retry without a normal map.'));
    }

    const log = el('pre', 'queue-log');
    log.hidden = true;
    const showLog = button('Show log', 'list', 'quiet');
    showLog.onclick = () => action(retry, showLog, async () => {
      const result = await api('/api/jobs/' + item.id + '/log');
      log.textContent = result.text || 'No calculation log is available.';
      log.hidden = false;
      showLog.hidden = true;
    });
    retry.body.append(showLog, log);

    const send = button('Add retry to queue', 'rotate', 'primary');
    send.onclick = () => action(retry, send, async () => {
      if (!triangles.reportValidity() || !height.reportValidity()) return;
      if (!texture.value || (!fixedResolution && !resolution.value)) {
        retry.notice.textContent = 'Choose a supported texture size and geometry resolution.';
        return;
      }
      if (refinish && bakeNormals.checked && !canBakeNormals) {
        retry.notice.textContent = 'Clear normal map transfer before retrying without Blender.';
        return;
      }
      const changes = {texture_size: Number(texture.value), triangles: triangles.valueAsNumber,
        height_m: height.valueAsNumber, priority: Number(priority.value)};
      if (!fixedResolution) changes.resolution = Number(resolution.value);
      if (refinish && bakeNormals.checked !== !!saved.bake_normals) changes.bake_normals = bakeNormals.checked;
      if (canRepairSurface && repairSurface.checked !== !!saved.repair_surface) changes.repair_surface = repairSurface.checked;
      if (finishing?.mesh_mode && !adaptiveRefinish && !repairSurface.checked) {
        if (method.value !== finishing.mesh_mode) changes.mesh_mode = method.value;
        if (preSimplify.checked !== finishing.pre_simplify) changes.pre_simplify = preSimplify.checked;
        if (repair.checked !== finishing.repair_holes) changes.repair_holes = repair.checked;
      }
      const result = await api('/api/queue/' + item.id + '/retry', json('POST', changes));
      retry.close();
      ui.notice.textContent = result.paused ? 'New attempt queued. Resume the queue to start it.' : 'New attempt queued.';
      await poll();
      await refresh();
    });
    retry.foot.replaceChildren(button('Back', 'close', 'secondary', () => retry.close()), send);
    retry.show();
  }

  function jobRow(item) {
    const row = el('article', 'queue-job');
    const img = el('img');
    img.src = '/api/jobs/' + item.id + '/files/input.png';
    img.alt = item.name;
    const copy = el('div', 'queue-job-copy');
    const progress = item.progress || 0;
    const recipeSummary = [
      item.settings.resolution ? item.settings.resolution + '³' : null,
      item.settings.triangles ? Number(item.settings.triangles).toLocaleString('en-US') + ' triangles' : null,
      item.settings.texture_size ? item.settings.texture_size / 1024 + 'K' : null,
      progress + '%',
    ].filter(Boolean).join(' · ');
    copy.append(el('strong', '', item.name), el('small', '', item.stage), el('span', '', recipeSummary));
    if (item.error) copy.append(el('p', 'queue-failure', item.error));
    if (item.settings.retry_of) copy.append(el('small', '', 'Retry of ' + item.settings.retry_of.slice(0, 8)));
    const bar = el('progress');
    bar.max = 100;
    bar.value = progress;
    bar.setAttribute('aria-label', 'Progress for ' + item.name);
    copy.append(bar);

    const priority = prioritySelect(item.priority);
    priority.setAttribute('aria-label', 'Priority for ' + item.name);
    priority.disabled = item.state !== 'queued';
    priority.onchange = () => action(ui, priority, async () => {
      try {
        await api('/api/queue/' + item.id, json('PATCH', {priority: Number(priority.value)}));
      } catch (error) {
        priority.value = String(item.priority);
        throw error;
      }
      await poll();
    });
    const controls = el('div', 'queue-job-actions');
    controls.append(priority);
    if (['queued', 'running'].includes(item.state)) {
      const cancel = button('Cancel', 'close', 'quiet');
      cancel.onclick = () => action(ui, cancel, async () => {
        await api('/api/jobs/' + item.id + '/cancel', {method: 'POST'});
        await poll();
        await refresh();
      });
      controls.append(cancel);
    }
    if (['error', 'cancelled'].includes(item.state)) {
      const edit = button('Edit and retry', 'rotate', 'secondary');
      edit.onclick = () => action(ui, edit, () => editRetry(item));
      controls.append(edit);
    }
    row.append(img, copy, controls);
    return row;
  }

  function renderQueue(items) {
    const ordered = [...items].sort((a, b) => (STATE_ORDER[a.state] ?? 4) - (STATE_ORDER[b.state] ?? 4)
      || (a.state === 'queued' ? b.priority - a.priority || a.created - b.created : b.created - a.created));
    const visible = new Set(items.map(item => item.id));
    for (const [id, row] of renderedJobs) {
      if (!visible.has(id)) {
        row.node.remove();
        renderedJobs.delete(id);
      }
    }
    if (!ordered.length) {
      if (!queue.firstChild) queue.append(el('div', 'wb-empty', 'Your queue is clear. Prepare a batch or queue an image from Create.'));
      return;
    }
    queue.querySelector('.wb-empty')?.remove();
    ordered.forEach((item, index) => {
      const key = JSON.stringify(item);
      let row = renderedJobs.get(item.id);
      if (!row || row.key !== key) {
        const node = jobRow(item);
        row?.node.remove();
        row = {key, node};
        renderedJobs.set(item.id, row);
      }
      if (queue.children[index] !== row.node) queue.insertBefore(row.node, queue.children[index] || null);
    });
  }

  async function poll() {
    const request = ++pollNumber;
    try {
      const latest = await api('/api/queue');
      if (request !== pollNumber) return;
      if (snapshot?.paused !== latest.paused) {
        pause.replaceChildren(...button(latest.paused ? 'Resume queue' : 'Pause queue',
          latest.paused ? 'play' : 'pause').childNodes);
      }
      snapshot = latest;
      const waiting = snapshot.items.filter(item => item.state === 'queued').length;
      document.getElementById('queueCount').textContent = waiting || '';
      if (!ui.element.open) return;
      updateFooter();
      renderQueue(snapshot.items);
    } catch (error) {
      if (request === pollNumber && ui.element.open) ui.notice.textContent = error.message;
    }
  }

  ui.element.addEventListener('close', () => clearInterval(timer));
  return {
    async open(prepare = false) {
      profiles = (await api('/api/profiles')).profiles;
      const preferred = globalProfile.value || 'game_d';
      globalProfile.replaceChildren(...profiles.map(profile => {
        const option = el('option', '', profile.name);
        option.value = profile.id;
        return option;
      }));
      globalProfile.value = profiles.some(profile => profile.id === preferred) ? preferred : profiles[0]?.id || '';
      renderDrafts();
      tab(prepare);
      ui.show();
      clearInterval(timer);
      timer = setInterval(poll, 1500);
      await poll();
    },
    update() {
      if (!ui.element.open) poll();
    },
  };
}
