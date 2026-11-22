const $ = id => document.getElementById(id);
const roles = ['front', 'left', 'back', 'right'];
const limit = 25 * 1024 * 1024;
const methods = {
  trellis2: {label: 'TRELLIS.2 · Single image', detail: 'Standard generation'},
  pixal3d_single: {label: 'Pixal3D · Single image', detail: 'Automatic camera estimation with MoGe'},
  pixal3d: {label: 'Pixal3D · Multi-view', detail: 'Front plus one to three matching views'},
};

export function createMultiviewInputs({ changed, invalid }) {
  const chooser = $('generationMethod');
  const methodButtons = [...chooser.querySelectorAll('button')];
  const addView = $('addReferenceView');
  const swapSides = $('swapReferenceSides');
  const pickReference = $('pickReference');
  const reprojectFace = $('reprojectFace');
  const slots = new Map(roles.map(role => {
    const area = document.querySelector(`[data-reference-slot="${role}"]`);
    return [role, {
      input: area.querySelector('input'),
      drop: area.querySelector('label'),
      image: area.querySelector('img'),
      prompt: area.querySelector('[data-slot-prompt]'),
      remove: area.querySelector('.reference-slot-remove'),
      file: null,
      url: null,
    }];
  }));
  let method = 'trellis2';
  let status = {};
  let fixedWorkflow = false;

  const isMulti = () => !fixedWorkflow && method === 'pixal3d';
  const usesPixal = () => !fixedWorkflow && method !== 'trellis2';
  const activeRoles = () => isMulti() ? roles : ['front'];
  const countViews = () => activeRoles().filter(role => slots.get(role).file).length;

  function capability(value) {
    const explicit = status.generation_methods?.[value];
    if (explicit) return explicit;
    if (value === 'pixal3d') return status.pixal3d;
    if (value === 'pixal3d_single') return status.pixal3d?.single_image;
    return status.ready === undefined ? null : {ready: status.ready, message: status.message};
  }

  function syncPicker(slot) {
    const transfer = new DataTransfer();
    if (slot.file) transfer.items.add(slot.file);
    slot.input.files = transfer.files;
    slot.input.setCustomValidity('');
  }

  function setFile(role, file) {
    const slot = slots.get(role);
    if (slot.url) URL.revokeObjectURL(slot.url);
    slot.file = file || null;
    slot.url = file ? URL.createObjectURL(file) : null;
    syncPicker(slot);
    if (slot.url) slot.image.src = slot.url;
    else slot.image.removeAttribute('src');
    slot.image.hidden = !file;
    slot.prompt.hidden = !!file;
    slot.remove.hidden = !file;
  }

  function renderReferences() {
    const count = countViews();
    $('referenceMode').textContent = isMulti() ? `${count} / 4 views` : `${count} ${count === 1 ? 'image' : 'images'}`;
    const hasFront = !!slots.get('front').file;
    pickReference.querySelector('span').textContent = hasFront ? 'Replace image' : 'Add image';
    $('mainViewBadge').hidden = !isMulti() || !hasFront;
    const help = !slots.get('front').file ? 'Add a front view and at least one additional view.'
      : count < 2 ? 'Add a side or back view.' : '';
    $('viewRequirements').textContent = help;
    $('viewRequirements').hidden = !help;
    swapSides.disabled = !(slots.get('left').file || slots.get('right').file);
  }

  function accept(role, file) {
    const slot = slots.get(role);
    if (!slot || (role !== 'front' && !isMulti())) return;
    if (file && (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > limit || !file.size)) {
      syncPicker(slot);
      invalid(file.size > limit ? 'Each image is limited to 25 MB.' : 'Choose a non-empty PNG, JPEG or WebP image.');
      return;
    }
    setFile(role, file);
    renderReferences();
    changed({role, file});
  }

  for (const [role, slot] of slots) {
    slot.input.addEventListener('change', () => accept(role, slot.input.files[0]));
    for (const type of ['dragenter', 'dragover']) {
      slot.drop.addEventListener(type, event => {
        event.preventDefault();
        slot.drop.classList.add('drag');
      });
    }
    for (const type of ['dragleave', 'drop']) {
      slot.drop.addEventListener(type, event => {
        event.preventDefault();
        slot.drop.classList.remove('drag');
        if (type === 'drop' && event.dataTransfer?.files.length) accept(role, event.dataTransfer.files[0]);
      });
    }
    slot.remove.addEventListener('click', () => {
      accept(role, null);
      slot.input.focus();
    });
  }

  function renderMode() {
    for (const id of ['supplementalViews', 'pixalViewAngles']) {
      $(id).hidden = $(id).disabled = !isMulti();
    }
    const locked = fixedWorkflow || usesPixal();
    $('generationEngine').hidden = fixedWorkflow;
    $('generationSampling').hidden = $('generationSampling').disabled = locked;
    $('resolution').closest('label').hidden = $('resolution').disabled = locked;
    $('removeBgField').hidden = $('removeBg').disabled = locked;
    addView.hidden = fixedWorkflow || isMulti();
    addView.setAttribute('aria-expanded', String(isMulti()));
    $('frontDropTitle').textContent = isMulti() ? 'Front view' : 'Drop your reference image';
    slots.get('front').input.setAttribute('aria-label', isMulti() ? 'Front reference image' : 'Reference image');
    $('generationMethodLabel').textContent = methods[method].label;
    for (const button of methodButtons) button.setAttribute('aria-pressed', String(button.value === method));
    renderReferences();
  }

  function sync(textureSize = Number($('textureSize').value)) {
    const projection = status.pixal3d?.face_projection;
    const canShowFace = usesPixal() && $('assetType').value === 'humanoid';
    $('faceProjectionField').hidden = !canShowFace;
    reprojectFace.disabled = !canShowFace || !capability(method)?.ready || !projection?.ready;
    if (canShowFace) {
      $('reprojectFaceHelp').textContent = projection?.ready
        ? 'Blends the reference face into the finished texture.'
        : projection?.message || 'Face reprojection is unavailable.';
    }
    const notice = $('generationMethodNotice');
    const hiddenViews = !isMulti() && roles.slice(1).some(role => slots.get(role).file);
    const selected = capability(method);
    const messages = [];
    if (status.message) messages.push(status.message);
    else if (usesPixal() && !selected?.ready) messages.push(selected?.message || 'This Pixal3D mode needs its optional runtime.');
    else if (selected && !selected.ready) messages.push(selected.message || 'Install the TRELLIS.2 model weights to generate.');
    if (usesPixal() && textureSize > 2048) messages.push('Choose 1K or 2K textures in Advanced for Pixal3D.');
    if (hiddenViews) messages.push('Additional references are available in Multi-view.');
    notice.textContent = messages.join(' ');
    notice.hidden = fixedWorkflow || !messages.length;
  }

  function setWorkflow(workflow) {
    const locked = !!workflow;
    if (locked === fixedWorkflow) return;
    fixedWorkflow = locked;
    renderMode();
  }

  function applyMethod(value) {
    if (value !== method) {
      method = value;
      renderMode();
      changed({mode: true});
    }
  }

  function selectMethod(value) {
    applyMethod(value);
    chooser.open = false;
    chooser.querySelector('summary').focus();
  }

  for (const button of methodButtons) button.addEventListener('click', () => selectMethod(button.value));
  chooser.addEventListener('toggle', () => {
    if (!chooser.open) return;
    const panel = chooser.closest('.creation');
    if (panel.scrollHeight <= panel.clientHeight) return;
    const dock = $('generationSummary').closest('.generation-dock');
    const visibleBottom = Math.min(panel.getBoundingClientRect().bottom, dock.getBoundingClientRect().top);
    const overlap = chooser.getBoundingClientRect().bottom - visibleBottom;
    if (overlap > 0) panel.scrollBy(0, overlap + 8);
  });
  chooser.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !chooser.open) return;
    chooser.open = false;
    chooser.querySelector('summary').focus();
  });
  pickReference.addEventListener('click', () => slots.get('front').input.click());
  addView.addEventListener('click', () => {
    selectMethod('pixal3d');
    slots.get('left').input.focus();
  });
  swapSides.addEventListener('click', () => {
    const left = slots.get('left').file;
    setFile('left', slots.get('right').file);
    setFile('right', left);
    renderReferences();
    changed({swap: true});
  });

  function setCapabilities(value) {
    status = value || {};
    for (const button of methodButtons) {
      const info = capability(button.value);
      const available = !!info?.ready;
      button.disabled = button.value !== 'trellis2' && !available;
      const detail = button.querySelector('[data-method-status]');
      const label = available || (button.value === 'trellis2' && !info)
        ? methods[button.value].detail : info?.message || 'Optional runtime required';
      if (detail.textContent !== label) detail.textContent = label;
    }
    const multi = capability('pixal3d');
    addView.disabled = !multi?.ready;
    addView.title = addView.disabled ? multi?.message || 'Pixal3D Multi-view runtime required.' : '';
    sync();
  }

  function referenceRequest() {
    const form = new FormData();
    for (const role of activeRoles()) {
      const file = slots.get(role).file;
      if (file) form.set(role === 'front' ? 'image' : `${role}_image`, file);
    }
    return form;
  }

  function appendRequest(form) {
    for (const [name, file] of referenceRequest()) form.set(name, file);
    if (isMulti()) {
      form.set('side_angle', $('sideAngle').value);
      if (!form.get('profile_id')) form.delete('profile_id');
    } else {
      form.set('engine', method);
    }
    if (reprojectFace.checked && !reprojectFace.disabled) form.set('reproject_face', 'true');
    if (usesPixal()) form.set('resolution', '1024');
  }

  async function restore(job) {
    const settings = job.settings;
    const single = settings.view_mode === 'single' || settings.pipeline_type === 'pixal3d_1024_monoview'
      || settings.engine === 'pixal3d_single';
    const restoredMethod = settings.workflow_id ? 'trellis2' : single ? 'pixal3d_single'
      : settings.engine === 'pixal3d' ? 'pixal3d' : 'trellis2';
    const sourceFile = settings.workflow_id && settings.workflow_input ? settings.workflow_input : 'input.png';
    const references = restoredMethod === 'pixal3d' ? settings.views : [{role: 'front', file: sourceFile}];
    if (!Array.isArray(references) || !references.some(view => view.role === 'front')
        || (restoredMethod === 'pixal3d' && references.length < 2)) {
      throw Error('The saved reference list is incomplete.');
    }
    const restored = await Promise.all(references.map(async ({role, file}) => {
      if (!roles.includes(role) || typeof file !== 'string') throw Error('The saved reference list is invalid.');
      const response = await fetch(`/api/jobs/${job.id}/files/${encodeURIComponent(file)}`);
      if (!response.ok) throw Error(`Saved ${role} reference is unavailable.`);
      const blob = await response.blob();
      const extension = file.split('.').at(-1);
      const type = {jpg: 'image/jpeg', webp: 'image/webp'}[extension] || 'image/png';
      return [role, new File([blob], `${settings.name}-${role}.${extension}`, {type})];
    }));
    const files = new Map(restored);
    for (const role of roles) setFile(role, files.get(role));
    fixedWorkflow = !!settings.workflow_id;
    method = restoredMethod;
    reprojectFace.checked = usesPixal() && settings.reproject_face === true;
    $('sideAngle').value = String(settings.side_angle ?? 40);
    renderMode();
  }

  renderMode();
  setCapabilities();
  return {
    accept, restore, referenceRequest, appendRequest, setCapabilities, setWorkflow, sync, applyMethod,
    get active() { return isMulti(); },
    get usesPixal() { return usesPixal(); },
    get method() { return method; },
    get methodLabel() { return methods[method].label; },
    get viewCount() { return countViews(); },
    get hasFront() { return !!slots.get('front').file; },
    get ready() { return !!capability(method)?.ready && (!isMulti() || countViews() >= 2); },
    pasteRole(target) { return (isMulti() && target.closest('[data-reference-slot]')?.dataset.referenceSlot) || 'front'; },
  };
}
