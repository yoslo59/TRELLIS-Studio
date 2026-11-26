import {button, dialog, el, field, input, select, action} from './workbench-ui.js?v=3.4.0';
import {faceSource, refinedFaceLabel} from './face-lineage.js';
import {createWorkflowFields, optionHelp} from './advanced-controls.js?v=studio-timing';
import {textureMethodAvailability,textureMethodChoices,textureMethodHelp,textureMethodOf,textureMethodRestriction} from './texture-methods.js?v=figure-master-ultratex';

const PRIORITIES = [['2', 'Highest'], ['1', 'High'], ['0', 'Normal'], ['-1', 'Low'], ['-2', 'Lowest']];
const STATE_ORDER = {running: 0, queued: 1, error: 2, cancelled: 2, done: 3};
const fullMeshNote = 'Master finishing · None. Exports the decoded mesh at its original triangle count.';
const json = (method, data) => ({
  method,
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(data),
});
const resolutionChoices = values => values.map(value => [String(value), value + '³']);
const textureChoices = values => values.map(value => [String(value), value / 1024 + 'K']);
const workflowSummary = (workflow, textureMethod) => [workflow.label, `${workflow.resolution}³`,
  workflow.target_faces > 0 ? `${Number(workflow.target_faces).toLocaleString('en-US')} target triangles` : 'Dense mesh',
  `${textureMethod === 'ultratex' ? 'UltraTex' : 'TRELLIS.2 PBR'} ${workflow.texture_size / 1024}K`].join(' · ');
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

export function createQueueStudio({api, refresh, canViewGraph, viewJobGraph, getRecords, refineFace}) {
  const ui = dialog('queueDialog', 'Generation queue', 'LOCAL COMPUTE',
    'Prepare a batch, then let the studio process one calculation at a time.', 'queue-dialog');
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
  const globalProfile = select([]);
  const globalHeight = heightInput(1);
  const defaults = el('div', 'queue-defaults');
  defaults.append(field('Default profile', globalProfile), field('Default height · m', globalHeight), add, picker);
  const rows = el('div', 'queue-drafts');
  compose.append(defaults, el('p', 'hint',
    'Each image has its own name, profile, size and priority. Open Settings to review its recipe.'), rows);

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
  const profileOf = id => profiles.find(profile => profile.id === id);
  const workflowOf = id => profileOf(id)?.workflow;
  const unavailableWorkflow = id => {
    const workflow = workflowOf(id);
    return !!workflow && workflow.available !== true;
  };

  function profileOptions(control, currentId) {
    control.replaceChildren(...profiles.filter(profile => !profile.retired || profile.id === currentId).map(profile => {
      const unavailable = unavailableWorkflow(profile.id);
      const option = el('option', '', profile.name + (unavailable ? ' · Unavailable' : ''));
      option.value = profile.id;
      option.disabled = unavailable;
      if (unavailable) option.title = profile.workflow.reason;
      return option;
    }));
  }

  function syncBatchSubmit() {
    submit.disabled = !drafts.length || drafts.some(row => unavailableWorkflow(row.profile_id));
  }

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

  function appendTextureMethod(container, runtime, settings, size, id) {
    const capabilities=textureMethodAvailability(runtime);
    const method=textureMethodOf(settings);
    const restriction=textureMethodRestriction(method,capabilities,true);
    const control=select(textureMethodChoices,restriction?'trellis':method);
    control.id = id;
    for(const option of control.options){
      option.title=textureMethodRestriction(option.value,capabilities,true);
      option.disabled=!!option.title;
    }
    const label=field('Texture method',control);
    const status=el('p','hint');
    status.setAttribute('role','status');
    status.textContent=restriction?`${restriction} Using Native PBR for this attempt.`:'';
    status.hidden=!status.textContent;
    container.append(label,status);
    optionHelp(label,'Texture method',textureMethodHelp);
    let previousSize=null;
    const sync=()=>{
      if(control.value==='ultratex'){
        if(previousSize===null)previousSize=size.value;
        size.value='2048';
        size.disabled=true;
      }else if(previousSize!==null){
        size.value=previousSize;
        previousSize=null;
        size.disabled=false;
      }
    };
    control.addEventListener('change',()=>{status.hidden=true;sync();});
    sync();
    return control;
  }

  async function editDraft(row) {
    const profile = profileOf(row.profile_id);
    const workflow = profile?.workflow;
    if (workflow) {
      const container = el('div');
      const settings = profile.settings;
      const editor = createWorkflowFields(container, workflow.advanced, row.workflow_options ?? settings.workflow_options);
      const summary = {...workflow, label: profile.name, resolution: settings.resolution,
        target_faces: settings.triangles, texture_size: settings.texture_size};
      const title = workflowSummary(summary, settings.texture_method);
      recipe.body.replaceChildren(el('strong', '', title), container);
      recipe.foot.replaceChildren(button('Apply', 'check', 'primary', () => {
        if(!editor.validate())return;
        row.workflow_options=editor.read();
        recipe.close();
      }));
      recipe.show();
      return;
    }
    const {runtime, schema} = await capabilities();
    const profileSettings = profile?.settings;
    const preset = profileSettings?.preset;
    const removeBg = checkbox(row.remove_bg);
    const requiredSource = !!row.master_finishing || ['dense_master', 'humanoid_experimental'].includes(preset);
    const keepSource = checkbox(requiredSource || row.keep_finishing_source);
    keepSource.disabled = requiredSource;
    const fullMesh = row.master_finishing === 'none';
    const triangles = fullMesh ? null : triangleInput(row.triangles, schema);
    const texture = select(textureChoices(runtime.texture_sizes), String(row.texture_size));
    const resolution = select(resolutionChoices(runtime.supported_resolutions), String(row.resolution));
    const supportsRetexture = row.master_finishing === 'hi3d_1024';
    const retexture = checkbox(row.retexture_after_refinement);
    retexture.id = 'queueRetextureAfterRefinement';
    const retextureField = field('Retexture after Hi3D', retexture);
    const retextureStatus = el('p', 'hint');
    let textureMethod;
    const syncRetexture = () => {
      retexture.disabled = Number(resolution.value) !== 1024;
      retextureStatus.textContent = retexture.disabled
        ? 'Choose 1024³ for the geometry resolution to use this option.'
        : runtime.detail_texturing?.available !== true ? runtime.detail_texturing?.reason || 'The local texturing pipeline is unavailable.' : '';
      retextureStatus.hidden = !retextureStatus.textContent;
    };
    const apply = button('Apply settings', 'check', 'primary', () => {
      if (triangles && !triangles.reportValidity()) return;
      if (!texture.value || !resolution.value) {
        recipe.notice.textContent = 'Choose a supported texture size and geometry resolution.';
        return;
      }
      if (supportsRetexture && !retexture.disabled && retexture.checked && runtime.detail_texturing?.available !== true) {
        recipe.notice.textContent = retextureStatus.textContent;
        return;
      }
      Object.assign(row, {texture_size: Number(texture.value), resolution: Number(resolution.value),
        remove_bg: removeBg.checked, keep_finishing_source: keepSource.checked});
      if (triangles) row.triangles = triangles.valueAsNumber;
      if (supportsRetexture) row.retexture_after_refinement = !retexture.disabled && retexture.checked;
      if (textureMethod) {
        row.texture_method=textureMethod.value;
        delete row.multiview_texture_refinement;
      }
      recipe.close();
    });
    recipe.body.replaceChildren(field('Geometry resolution', resolution),
      fullMesh ? el('p', 'hint', fullMeshNote)
        : field(preset === 'dense_master' ? 'Maximum triangles' : 'Triangle target', triangles),
      field('Texture size', texture), field('Remove image background', removeBg),
      field('Keep source for finishing variants', keepSource));
    if (supportsRetexture) {
      recipe.body.append(retextureField, retextureStatus);
      optionHelp(retextureField, 'Retexture after Hi3D',
        'Generates new PBR materials from the reference on the refined geometry, using the profile’s texture settings.');
      syncRetexture();
      resolution.addEventListener('change', syncRetexture);
    }
    textureMethod=appendTextureMethod(recipe.body,runtime,{...profileSettings,...row},texture,'queueTextureMethod');
    recipe.body.append(el('p', 'hint',
      'Sampling and finishing controls come from this image’s profile. Save a custom profile in Create for a different recipe.'));
    recipe.foot.replaceChildren(apply);
    recipe.show();
  }

  function applyProfile(row, id) {
    const profile = profileOf(id);
    if (!profile) return;
    if (row.profile_id !== id) delete row.workflow_options;
    row.profile_id = id;
    if (profile.workflow) return;
    const {remove_bg, triangles, texture_size, resolution, advanced, master_finishing, retexture_after_refinement} = profile.settings;
    Object.assign(row, {remove_bg, triangles, texture_size, resolution,
      keep_finishing_source: profile.keep_finishing_source === true,
      advanced_settings: structuredClone(advanced)});
    if (master_finishing) row.master_finishing = master_finishing;
    else delete row.master_finishing;
    if (retexture_after_refinement !== undefined) row.retexture_after_refinement = retexture_after_refinement;
    else delete row.retexture_after_refinement;
    delete row.texture_method;
    delete row.multiview_texture_refinement; // A new profile restores its texture refinement preference.
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
      const profile = select([]);
      profileOptions(profile, row.profile_id);
      profile.value = row.profile_id;
      const height = heightInput(row.height_m);
      const seed = input('number', row.seed, {min: 0, max: 2147483647, step: 1, required: ''});
      const priority = prioritySelect(row.priority);
      title.oninput = () => row.name = title.value;
      height.oninput = () => row.height_m = height.valueAsNumber;
      seed.oninput = () => row.seed = seed.valueAsNumber;
      priority.onchange = () => row.priority = Number(priority.value);
      const knobs = el('div', 'queue-knobs');
      const seedField = field('Seed', seed);
      const status = el('small', 'hint');
      const syncProfile = () => {
        const workflow = workflowOf(row.profile_id);
        seedField.hidden = seed.disabled = !!workflow;
        status.textContent = unavailableWorkflow(row.profile_id) ? workflow.reason : '';
        status.hidden = !status.textContent;
        syncBatchSubmit();
      };
      profile.onchange = () => { applyProfile(row, profile.value); syncProfile(); };
      syncProfile();
      knobs.append(field('Profile', profile), field('Height · m', height), seedField, field('Priority', priority));
      details.append(title, knobs, status);

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
    syncBatchSubmit();
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
    form.set('specifications', JSON.stringify(drafts.map(({file, url, ...spec}) => {
      if (workflowOf(spec.profile_id)) {
        const {name, profile_id, height_m, priority, workflow_options} = spec;
        return {name, profile_id, height_m, priority, workflow_options};
      }
      if (spec.master_finishing === 'none') delete spec.triangles;
      return spec;
    })));
    for (const row of drafts) form.append('images', row.file);
    await api('/api/queue/batch', {method: 'POST', body: form});
    drafts.forEach(row => URL.revokeObjectURL(row.url));
    drafts = [];
    renderDrafts();
    tab(false);
    await poll();
    await refresh();
  }).finally(syncBatchSubmit);

  pause.onclick = () => action(ui, pause, async () => {
    await api('/api/queue', json('PATCH', {paused: !snapshot?.paused}));
    await poll();
  });

  function retryActions(item, changes, available = true) {
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
    send.disabled = !available;
    send.onclick = () => action(retry, send, async () => {
      const payload = changes();
      if (!payload) return;
      const result = await api('/api/queue/' + item.id + '/retry', json('POST', payload));
      retry.close();
      ui.notice.textContent = result.paused ? 'New attempt queued. Resume the queue to start it.' : 'New attempt queued.';
      await poll();
      await refresh();
    });
    retry.foot.replaceChildren(button('Back', 'close', 'secondary', () => retry.close()), send);
    retry.show();
  }

  async function editRetry(item) {
    const saved = item.settings;
    if (saved.workflow_id) {
      const profile = profileOf(saved.workflow_id);
      const workflow = profile?.workflow;
      const height = heightInput(saved.height_m);
      const priority = prioritySelect(item.priority);
      const unavailable = workflow?.available !== true;
      const details = unavailable ? workflow?.reason || 'Choose a current profile to run a new generation.' : 'Reuses the saved reference image.';
      const recorded = workflow && {...workflow, label: saved.profile?.name || workflow.label,
        resolution: saved.resolution ?? workflow.resolution,
        target_faces: saved.triangles ?? workflow.target_faces, texture_size: saved.texture_size ?? workflow.texture_size};
      retry.body.replaceChildren(el('strong', '', item.name), el('p', 'queue-failure', item.error || item.stage),
        el('p', 'hint', recorded ? workflowSummary(recorded, saved.texture_method ?? profile.settings.texture_method) : saved.profile?.name),
        el('p', 'hint', details), field('Height · m', height), field('Priority', priority));
      const container = el('div');
      const editor = workflow && createWorkflowFields(container, workflow.advanced, saved.workflow_options);
      if (editor) retry.body.append(container);
      retryActions(item, () => height.reportValidity() && editor?.validate()
        ? {height_m: height.valueAsNumber, priority: Number(priority.value), workflow_options: editor.read()} : null,
      !unavailable);
      return;
    }
    const pixal = saved.engine === 'pixal3d';
    const refinish = saved.source_kind === 'refinish';
    const master = saved.master_finishing;
    const fullMesh = master === 'none';
    const adaptiveRefinish = refinish && saved.adaptive_finishing;
    const [{runtime, schema}, components] = await Promise.all([
      capabilities(),
      refinish ? api('/api/components') : null,
    ]);
    const canBakeNormals = !!components?.blender;
    const fixedResolution = refinish || pixal || saved.retexture_after_refinement;
    const resolutions = refinish ? [saved.resolution ?? 512] : fixedResolution ? [1024] : runtime.supported_resolutions;
    const textures = pixal && !refinish ? runtime.pixal3d.texture_sizes : runtime.texture_sizes;
    const resolution = select(resolutionChoices(resolutions), String(saved.resolution ?? 512));
    resolution.disabled = fixedResolution;
    const texture = select(textureChoices(textures), String(saved.texture_size));
    const triangles = triangleInput(saved.triangles, schema);
    triangles.disabled = !!master;
    const height = heightInput(saved.height_m);
    const priority = prioritySelect(item.priority);
    const canRepairSurface = !master && runtime.features?.includes('repair_surface') &&
      (refinish || saved.preset === 'dense_master');
    const finishingModes = [['saved', 'Standard finishing']];
    if (canRepairSurface) finishingModes.push(['repair', 'Repair holes']);
    if (!master && runtime.features?.includes('hd_reconstruction') && (refinish || saved.preset === 'dense_master')) {
      finishingModes.push(['hd', 'HD Reconstruction (768)']);
    }
    const surfaceMode = select(finishingModes, saved.hd_reconstruction ? 'hd' : saved.repair_surface ? 'repair' : 'saved');
    const hdChoice = surfaceMode.querySelector('[value="hd"]');
    const finishing = saved.repair_surface || saved.hd_reconstruction ? saved.generation_options : item.retry_finishing;
    const method = select([['remesh', 'Rebuild surface'], ['direct', 'Simplify existing surface']], finishing?.mesh_mode);
    const preSimplify = checkbox(finishing?.pre_simplify);
    const repair = checkbox(finishing?.repair_holes);
    const bakeNormals = checkbox(!!saved.bake_normals);

    const title = el('strong', '', item.name);
    const failure = el('p', 'queue-failure', item.error || item.stage);
    const context = 'Seed ' + saved.seed + (pixal ? ' · ' + (saved.views?.length ?? 0) + ' saved views' : ' · Saved reference');
    retry.body.replaceChildren(title, failure, el('p', 'hint', context),
      field('Geometry resolution', resolution, refinish ? 'Uses the saved geometry cache.' : pixal ? 'Pixal3D uses 1024³.'
        : saved.retexture_after_refinement ? 'The saved texture pass requires a 1024³ source.' : ''),
      fullMesh ? el('p', 'hint', fullMeshNote)
        : field(saved.adaptive_finishing || master ? 'Maximum triangles' : 'Triangle target', triangles,
          master ? 'Fixed by HD Master Finishing.' : ''), field('Texture size', texture), field('Height · m', height),
      field('Priority', priority));
    if (finishingModes.length > 1) {
      retry.body.append(field('Surface treatment', surfaceMode, 'HD Reconstruction requires a 1024 or higher geometry source.'));
    }
    const syncSurfaceMode = () => {
      if (!hdChoice) return;
      hdChoice.disabled = Number(resolution.value) < 1024;
      if (hdChoice.disabled && surfaceMode.value === 'hd') surfaceMode.value = 'saved';
    };
    syncSurfaceMode();
    if (!master && finishing?.mesh_mode && !adaptiveRefinish) {
      const legacyFields = [field('Finishing method', method), field('Pre-simplify dense meshes to 2M', preSimplify),
        field('Fill small holes', repair)];
      const syncFinishing = () => {
        for (const control of legacyFields) control.hidden = surfaceMode.value !== 'saved';
      };
      surfaceMode.addEventListener('change', syncFinishing);
      resolution.addEventListener('change', () => { syncSurfaceMode(); syncFinishing(); });
      syncFinishing();
      retry.body.append(...legacyFields);
    } else {
      resolution.addEventListener('change', syncSurfaceMode);
    }
    if (!master && refinish && (canBakeNormals || saved.bake_normals)) {
      retry.body.append(field('Transfer master detail to a normal map', bakeNormals,
        canBakeNormals ? '' : 'Blender is unavailable. Clear this option to retry without a normal map.'));
    }
    const singleImage = pixal ? saved.view_mode === 'single' : (saved.view_mode ?? 'single') === 'single';
    const textureMethod = !refinish && singleImage
      ? appendTextureMethod(retry.body,runtime,saved,texture,'retryTextureMethod') : null;

    retryActions(item, () => {
      if (!triangles.reportValidity() || !height.reportValidity()) return;
      if (!texture.value || (!fixedResolution && !resolution.value)) {
        retry.notice.textContent = 'Choose a supported texture size and geometry resolution.';
        return;
      }
      if (refinish && bakeNormals.checked && !canBakeNormals) {
        retry.notice.textContent = 'Clear normal map transfer before retrying without Blender.';
        return;
      }
      const changes = {texture_size: Number(texture.value), height_m: height.valueAsNumber, priority: Number(priority.value)};
      if (textureMethod && textureMethod.value!==textureMethodOf(saved)) changes.texture_method=textureMethod.value;
      if (!master) changes.triangles = triangles.valueAsNumber;
      if (!fixedResolution) changes.resolution = Number(resolution.value);
      if (refinish && bakeNormals.checked !== !!saved.bake_normals) changes.bake_normals = bakeNormals.checked;
      if (canRepairSurface && (surfaceMode.value === 'repair') !== !!saved.repair_surface) changes.repair_surface = surfaceMode.value === 'repair';
      if (hdChoice && (surfaceMode.value === 'hd') !== !!saved.hd_reconstruction) changes.hd_reconstruction = surfaceMode.value === 'hd';
      if (!master && finishing?.mesh_mode && !adaptiveRefinish && surfaceMode.value === 'saved') {
        if (method.value !== finishing.mesh_mode) changes.mesh_mode = method.value;
        if (preSimplify.checked !== finishing.pre_simplify) changes.pre_simplify = preSimplify.checked;
        if (repair.checked !== finishing.repair_holes) changes.repair_holes = repair.checked;
      }
      return changes;
    });
  }

  function jobRow(item) {
    const generatedMotion = item.settings.source_kind === 'generated_animation';
    const motionTransfer = item.settings.generated_animation_action === 'apply';
    const animation = generatedMotion || motionTransfer || item.settings.source_kind === 'animation_update';
    const row = el('article', 'queue-job');
    const img = el('img');
    img.src = animation ? '/web/asset-placeholder.svg' : '/api/jobs/' + item.id + '/files/input.png';
    img.alt = item.name;
    const copy = el('div', 'queue-job-copy');
    const progress = item.progress || 0;
    const face = refinedFaceLabel(item.settings);
    const recipeSummary = face ? face + ' · ' + progress + '%' : [
      generatedMotion ? 'Motion generation' : motionTransfer ? 'Saved motion transfer' : animation ? 'Animation update' : null,
      item.settings.workflow_id ? item.settings.profile?.name || 'ComfyUI' : null,
      generatedMotion && Number.isFinite(item.duration) ? item.duration + ' s' : null,
      generatedMotion && Number.isFinite(item.fps) ? item.fps + ' fps' : null,
      !animation && item.settings.resolution ? item.settings.resolution + '³' : null,
      !animation && (item.settings.master_finishing === 'none' ? 'Full mesh'
        : item.settings.triangles ? Number(item.settings.triangles).toLocaleString('en-US') + ' triangles' : null),
      !animation && item.settings.texture_size ? item.settings.texture_size / 1024 + 'K' : null,
      progress + '%',
    ].filter(Boolean).join(' · ');
    copy.append(el('strong', '', item.name), el('small', '', item.stage), el('span', '', recipeSummary));
    if (item.error) copy.append(el('p', 'queue-failure', item.error));
    if ((generatedMotion || motionTransfer) && ['error', 'cancelled'].includes(item.state)) {
      copy.append(el('small', '', generatedMotion
        ? 'Use prompt in My animations to create another attempt.'
        : 'Return to My animations and add the saved motion to your original model again.'));
    }
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
    if (canViewGraph?.(item)) {
      const graph = button('View graph', 'grid', 'secondary', () => action(ui, graph, async () => {
        await viewJobGraph(item.id, () => ui.close());
      }));
      controls.append(graph);
    }
    if (['queued', 'running'].includes(item.state)) {
      const cancel = button('Cancel', 'close', 'quiet');
      cancel.onclick = () => action(ui, cancel, async () => {
        await api('/api/jobs/' + item.id + '/cancel', {method: 'POST'});
        await poll();
        await refresh();
      });
      controls.append(cancel);
    }
    if (face && ['error', 'cancelled'].includes(item.state)) {
      const source = faceSource(item.settings, getRecords());
      if (source) {
        controls.append(button('Refine face on source', 'fit', 'secondary', () => {
          ui.close();
          refineFace(source);
        }));
      } else copy.append(el('small', '', 'Source model unavailable in this collection.'));
    } else if (!animation && ['error', 'cancelled'].includes(item.state)) {
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
      const key = JSON.stringify([item, faceSource(item.settings, getRecords())?.id]);
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
      const catalogue = await api('/api/profiles');
      profiles = catalogue.profiles;
      const preferred = globalProfile.value || catalogue.startup_profile;
      profileOptions(globalProfile);
      const selectable = id => {
        const profile = profileOf(id);
        return profile && !profile.retired && !unavailableWorkflow(id);
      };
      globalProfile.value = [preferred, catalogue.startup_profile, ...profiles.map(profile => profile.id)]
        .find(selectable) || '';
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
