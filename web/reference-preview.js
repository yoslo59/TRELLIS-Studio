const roleNames = {front: 'Front', left: 'Left', back: 'Back', right: 'Right'};
const imageNames = {original: 'Original', cutout: 'Cutout', framed: 'Model input'};
const fullCrop = [0, 0, 1, 1];
const svgNamespace = 'http://www.w3.org/2000/svg';
const maxStrokes = 48;
const maxPoints = 128;
const maxEditLength = 128 * 1024;
const rounded = value => Math.round(value * 10000) / 10000;
const isFullCrop = crop => crop.every((value, index) => value === fullCrop[index]);
const icon = name => `<svg class="ui-icon" aria-hidden="true"><use href="/web/icons.svg#${name}"/></svg>`;

export function createReferencePreview({api, multiview, removeBackground}) {
  const button = document.getElementById('openReferencePreview');
  const summary = document.getElementById('referenceEditSummary');
  const dialog = document.createElement('dialog');
  dialog.className = 'studio-dialog reference-preview';
  dialog.setAttribute('aria-labelledby', 'referencePreviewTitle');
  dialog.innerHTML = `
    <header class="studio-dialog-head">
      <div>
        <span class="eyebrow">REFERENCE WORKSHOP</span>
        <h2 id="referencePreviewTitle">Preview reference</h2>
        <p>Review the image that goes into the model. The original file stays intact.</p>
      </div>
      <button type="button" class="quiet" data-close aria-label="Close reference preview">${icon('close')}</button>
    </header>
    <div class="reference-preview-body">
      <div class="reference-preview-views" role="group" aria-label="Reference view" hidden></div>
      <div class="reference-preview-content" hidden>
        <section aria-label="Image comparison">
          <div class="reference-preview-stage">
            <div class="reference-preview-image">
              <img draggable="false" alt="">
              <svg class="reference-preview-overlay" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><rect width="100%" height="100%" fill="transparent"/><g class="reference-preview-marks"></g></svg>
            </div>
          </div>
          <div class="reference-preview-choices" role="group" aria-label="Compare reference stages">
            ${Object.entries(imageNames).map(([key, name]) => `
              <button type="button" data-image="${key}" aria-pressed="false"><img alt=""><span>${name}</span></button>
            `).join('')}
          </div>
        </section>
        <aside class="reference-preview-controls" aria-label="Reference corrections">
          <fieldset>
            <legend>Correct the silhouette</legend>
            <p class="hint">Paint over a missing detail or an unwanted patch. The brush opens the original view.</p>
            <div class="reference-preview-brushes" role="group" aria-label="Brush mode">
              <button type="button" class="secondary" data-brush="keep" aria-pressed="false">${icon('check')} Keep</button>
              <button type="button" class="secondary" data-brush="erase" aria-pressed="false">${icon('close')} Erase</button>
            </div>
            <label class="reference-preview-range">Brush radius <output data-radius-value>2%</output>
              <input type="range" data-radius min="0.1" max="10" step="0.1" value="2">
            </label>
            <button class="quiet" type="button" data-undo>${icon('back')} Undo last stroke</button>
          </fieldset>
          <fieldset>
            <legend>Trim the reference</legend>
            <p class="hint">Adjust the edges if the frame includes an object or shadow you want to exclude.</p>
            <div class="reference-preview-crop">
              ${['Left', 'Top', 'Right', 'Bottom'].map((name, index) => `
                <label class="reference-preview-range">${name} <output>0%</output>
                  <input type="range" data-edge="${index}" min="0" max="95" value="0">
                </label>
              `).join('')}
            </div>
          </fieldset>
          <button class="quiet" type="button" data-reset>${icon('restore')} Reset this view</button>
        </aside>
      </div>
      <p class="reference-preview-status" role="status" aria-live="polite"></p>
    </div>
    <footer class="studio-dialog-actions">
      <button type="button" class="quiet" data-close>Cancel</button>
      <button type="button" class="secondary" data-update>${icon('rotate')} Update preview</button>
      <button type="button" class="primary" data-apply>${icon('check')} Use this reference</button>
    </footer>`;
  document.body.append(dialog);

  const find = selector => dialog.querySelector(selector);
  const viewButtons = find('.reference-preview-views');
  const content = find('.reference-preview-content');
  const status = find('.reference-preview-status');
  const image = find('.reference-preview-image img');
  const overlay = find('.reference-preview-overlay');
  const marks = find('.reference-preview-marks');
  const brushRadius = find('[data-radius]');
  const updateButton = find('[data-update]');
  const applyButton = find('[data-apply]');
  const resetButton = find('[data-reset]');
  const undoButton = find('[data-undo]');
  const imageButtons = [...dialog.querySelectorAll('[data-image]')];
  const brushButtons = [...dialog.querySelectorAll('[data-brush]')];
  const edgeInputs = [...dialog.querySelectorAll('[data-edge]')];
  let saved = {};
  let approvedContext = null;
  let draft = {};
  let reviewed = '';
  let views = [];
  let originals = [];
  let role = 'front';
  let stage = 'framed';
  let brush = '';
  let drawing = null;
  let request = null;
  let failure = '';

  const view = () => views.find(item => item.role === role);
  const edits = () => draft[role] || {};
  const crop = () => edits().crop || fullCrop;
  const strokes = () => edits().strokes || [];
  const context = (removeBg = removeBackground.checked) => multiview.active ? 'pixal3d' : `trellis2:${Boolean(removeBg)}`;

  function selectedEdits(value) {
    return multiview.active ? value : value.front ? {front: value.front} : {};
  }

  function sync(disabled) {
    button.disabled = disabled || !multiview.hasFront;
    const count = Object.keys(selectedEdits(saved)).length;
    const approved = approvedContext === context();
    summary.hidden = (!approved && !count) || !multiview.hasFront;
    const label = count ? `${count} corrected ${count === 1 ? 'view' : 'views'}${approved ? '' : ' · check new preview'}` : 'Preview approved';
    if (summary.textContent !== label) summary.textContent = label;
  }

  function replaceEdits(next) {
    const clean = {};
    if (next.crop && !isFullCrop(next.crop)) clean.crop = next.crop;
    if (next.strokes?.length) clean.strokes = next.strokes;
    if (Object.keys(clean).length) draft[role] = clean;
    else delete draft[role];
  }

  function describeStatus() {
    const dirty = reviewed !== JSON.stringify(draft);
    status.classList.toggle('error', Boolean(failure));
    status.textContent = request ? 'Preparing the reference images…' : failure || (dirty ?
      'Corrections pending. Update the preview to check the new model input.' :
      'Model input shows the prepared image sent to the generation engine.');
    updateButton.disabled = Boolean(request) || (!dirty && !failure);
    applyButton.disabled = !views.length || Boolean(request) || dirty || Boolean(failure);
    content.setAttribute('aria-busy', Boolean(request));
  }

  function drawOverlay() {
    marks.replaceChildren();
    const width = image.naturalWidth;
    const height = image.naturalHeight;
    if (stage !== 'original' || !width || !height) return;
    overlay.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const add = (tag, attributes) => {
      const element = document.createElementNS(svgNamespace, tag);
      for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
      marks.append(element);
    };
    const [left, top, right, bottom] = crop();
    if (!isFullCrop(crop())) {
      const box = `M${left * width},${top * height}H${right * width}V${bottom * height}H${left * width}Z`;
      add('path', {class: 'reference-crop-shade', d: `M0,0H${width}V${height}H0Z ${box}`});
      add('path', {class: 'reference-crop-edge', d: box});
    }
    for (const stroke of drawing ? [...strokes(), drawing.stroke] : strokes()) {
      const radius = stroke.radius * Math.min(width, height);
      const style = `reference-${stroke.mode}-stroke`;
      if (stroke.points.length === 1) {
        const [[x, y]] = stroke.points;
        add('circle', {class: style, cx: x * width, cy: y * height, r: radius});
      } else {
        const points = stroke.points.map(([x, y]) => `${x * width},${y * height}`).join(' ');
        add('polyline', {class: style, points, 'stroke-width': radius * 2});
      }
    }
  }

  function drawView() {
    const current = view();
    content.hidden = !current;
    if (!current) return;
    image.alt = `${roleNames[role]} reference · ${imageNames[stage]}`;
    if (image.getAttribute('src') !== current[stage]) image.src = current[stage];
    for (const choice of imageButtons) {
      const key = choice.dataset.image;
      choice.setAttribute('aria-pressed', key === stage);
      const thumbnail = choice.querySelector('img');
      choice.disabled = !current[key];
      if (!current[key]) thumbnail.removeAttribute('src');
      else if (thumbnail.getAttribute('src') !== current[key]) thumbnail.src = current[key];
    }
    for (const tab of viewButtons.children) tab.setAttribute('aria-pressed', tab.dataset.role === role);
    for (const choice of brushButtons) {
      choice.setAttribute('aria-pressed', choice.dataset.brush === brush);
      choice.disabled = Boolean(request);
    }
    const trims = [crop()[0], crop()[1], 1 - crop()[2], 1 - crop()[3]].map(value => Math.round(value * 100));
    for (const [index, edge] of edgeInputs.entries()) {
      const dimension = index % 2 === 0 ? current.width || image.naturalWidth : current.height || image.naturalHeight;
      const minimum = Math.min(100, Math.max(5, Math.ceil(1600 / (dimension || 768))));
      edge.max = Math.max(trims[index], 100 - minimum - trims[(index + 2) % 4]);
      edge.value = trims[index];
      edge.previousElementSibling.textContent = `${trims[index]}%`;
      edge.disabled = Boolean(request);
    }
    undoButton.disabled = Boolean(request) || !strokes().length;
    resetButton.disabled = Boolean(request) || !draft[role];
    overlay.classList.toggle('painting', stage === 'original' && Boolean(brush) && !request);
    drawOverlay();
  }

  function selectStage(value) {
    stage = value;
    if (stage !== 'original') brush = '';
    drawView();
  }

  function renderViews() {
    const focusedRole = document.activeElement?.dataset.role;
    viewButtons.replaceChildren();
    viewButtons.hidden = views.length < 2;
    for (const item of views) {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'secondary';
      tab.dataset.role = item.role;
      tab.textContent = roleNames[item.role];
      tab.onclick = () => { role = item.role; drawView(); };
      viewButtons.append(tab);
      if (item.role === focusedRole) tab.focus();
    }
  }

  async function refreshPreview(form = multiview.referenceRequest()) {
    if (request) return;
    const payload = JSON.stringify(draft);
    if (payload.length > maxEditLength) {
      failure = 'Too many brush points. Undo some strokes before updating the preview.';
      describeStatus();
      return;
    }
    form.set('engine', multiview.active ? 'pixal3d' : 'trellis2');
    form.set('remove_bg', String(removeBackground.checked));
    form.set('reference_edits', payload);
    const controller = new AbortController();
    request = controller;
    failure = '';
    describeStatus();
    drawView();
    try {
      const result = await api('/api/reference-preview', {method: 'POST', body: form, signal: controller.signal});
      if (request !== controller) return;
      if (!Array.isArray(result.views) || !result.views.some(item => item.role === 'front')) {
        throw Error('The preview returned no main reference.');
      }
      views = result.views;
      reviewed = payload;
      stage = 'framed';
      brush = '';
      renderViews();
    } catch (error) {
      if (request === controller) failure = error.message;
    } finally {
      if (request === controller) {
        request = null;
        describeStatus();
        drawView();
      }
    }
  }

  function previewChanges() {
    failure = '';
    describeStatus();
    drawView();
  }

  function pointerPoint(event) {
    const rect = overlay.getBoundingClientRect();
    const unit = (position, offset, size) => rounded(Math.max(0, Math.min(1, (position - offset) / size)));
    return [unit(event.clientX, rect.left, rect.width), unit(event.clientY, rect.top, rect.height)];
  }

  overlay.addEventListener('pointerdown', event => {
    if (event.button !== 0 || drawing || !brush || request) return;
    if (strokes().length >= maxStrokes) {
      status.textContent = `This view has ${maxStrokes} strokes. Undo a stroke before adding another.`;
      return;
    }
    event.preventDefault();
    drawing = {pointer: event.pointerId, stroke: {mode: brush, radius: Number(brushRadius.value) / 100, points: [pointerPoint(event)]}};
    overlay.setPointerCapture(event.pointerId);
    drawOverlay();
  });
  overlay.addEventListener('pointermove', event => {
    if (drawing?.pointer !== event.pointerId) return;
    const {points} = drawing.stroke;
    if (points.length >= maxPoints) return;
    const point = pointerPoint(event);
    const [x, y] = points.at(-1);
    if (Math.hypot(point[0] - x, point[1] - y) < .003) return;
    points.push(point);
    drawOverlay();
  });
  overlay.addEventListener('pointerup', event => {
    if (drawing?.pointer !== event.pointerId) return;
    replaceEdits({...edits(), strokes: [...strokes(), drawing.stroke]});
    drawing = null;
    previewChanges();
  });
  overlay.addEventListener('lostpointercapture', () => {
    drawing = null;
    drawOverlay();
  });
  image.onload = drawView;

  for (const choice of imageButtons) choice.onclick = () => selectStage(choice.dataset.image);
  for (const choice of brushButtons) choice.onclick = () => {
    brush = brush === choice.dataset.brush ? '' : choice.dataset.brush;
    selectStage('original');
  };
  brushRadius.oninput = () => { find('[data-radius-value]').textContent = `${brushRadius.value}%`; };
  for (const [index, edge] of edgeInputs.entries()) edge.oninput = () => {
    const next = [...crop()];
    next[index] = rounded(index < 2 ? edge.value / 100 : 1 - edge.value / 100);
    replaceEdits({...edits(), crop: next});
    brush = '';
    stage = 'original';
    previewChanges();
  };
  undoButton.onclick = () => {
    replaceEdits({...edits(), strokes: strokes().slice(0, -1)});
    stage = 'original';
    previewChanges();
  };
  resetButton.onclick = () => {
    delete draft[role];
    stage = 'original';
    previewChanges();
  };
  updateButton.onclick = () => { void refreshPreview(); };
  applyButton.onclick = () => {
    saved = multiview.active ? draft : {...saved, ...draft};
    if (!multiview.active && !draft.front) delete saved.front;
    approvedContext = context();
    sync(false);
    dialog.close();
  };
  for (const close of dialog.querySelectorAll('[data-close]')) close.onclick = () => dialog.close();
  dialog.addEventListener('close', () => {
    if (dialog.open) return;
    request?.abort();
    request = null;
    drawing = null;
    views = [];
    for (const item of originals) URL.revokeObjectURL(item.original);
    originals = [];
    image.removeAttribute('src');
    for (const choice of imageButtons) choice.querySelector('img').removeAttribute('src');
  });
  button.onclick = () => {
    draft = structuredClone(selectedEdits(saved));
    reviewed = '';
    role = 'front';
    stage = 'original';
    brush = '';
    const form = multiview.referenceRequest();
    originals = [];
    for (const key of Object.keys(roleNames)) {
      const file = form.get(key === 'front' ? 'image' : `${key}_image`);
      if (file) originals.push({role: key, original: URL.createObjectURL(file)});
    }
    views = originals;
    renderViews();
    dialog.showModal();
    void refreshPreview(form);
  };

  return {
    get open() { return dialog.open; },
    sync,
    append(form) {
      const edits = selectedEdits(saved);
      if (approvedContext === context() || Object.keys(edits).length) form.set('reference_edits', JSON.stringify(edits));
    },
    discard(role) {
      if (dialog.open) dialog.close();
      if (role) delete saved[role];
      approvedContext = null;
    },
    restore(value, removeBg) {
      const exists = value && typeof value === 'object';
      approvedContext = exists ? context(removeBg) : null;
      saved = exists ? structuredClone(value) : {};
    },
  };
}
