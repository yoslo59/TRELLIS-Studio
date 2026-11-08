const $ = id => document.getElementById(id);
const roles = ['front', 'left', 'back', 'right'];
const limit = 25 * 1024 * 1024;

export function createMultiviewInputs({ changed, invalid }) {
  const toggle = $('pixalMode');
  const reprojectFace = $('reprojectFace');
  const slots = new Map(roles.map(role => {
    const area = document.querySelector(`[data-reference-slot="${role}"]`);
    return [role, {
      input: area.querySelector('input'),
      drop: area.querySelector('label'),
      image: area.querySelector('img'),
      prompt: area.querySelector('[data-slot-prompt]'),
      remove: area.querySelector('button'),
      file: null,
      url: null,
    }];
  }));
  let capabilities;

  function syncPicker(slot) {
    const transfer = new DataTransfer();
    if (slot.file) transfer.items.add(slot.file);
    slot.input.files = transfer.files;
    slot.input.setCustomValidity('');
  }

  function setFile(role, file) {
    const slot = slots.get(role);
    if (slot.url) URL.revokeObjectURL(slot.url);
    slot.file = file;
    slot.url = file ? URL.createObjectURL(file) : null;
    syncPicker(slot);
    if (slot.url) slot.image.src = slot.url;
    else slot.image.removeAttribute('src');
    slot.image.hidden = !file;
    slot.prompt.hidden = !!file;
    slot.remove.hidden = !file;
  }

  function countViews() {
    return roles.filter(role => slots.get(role).file).length;
  }

  function renderReferences() {
    const count = countViews();
    $('referenceMode').textContent = toggle.checked ? `${count} / 4 VIEWS` : 'SINGLE IMAGE';
    $('mainViewBadge').hidden = !toggle.checked || !slots.get('front').file;
    const help = !slots.get('front').file ? 'Add a front view and at least one complementary view.' :
      count < 2 ? 'Add at least one complementary view.' : '';
    $('viewRequirements').textContent = help;
    $('viewRequirements').hidden = !help;
  }

  function accept(role, file) {
    const slot = slots.get(role);
    if (!slot || (role !== 'front' && !toggle.checked)) return;
    if (file && (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > limit || !file.size)) {
      syncPicker(slot);
      invalid(file.size > limit ? 'Each image is limited to 25 MB.' : 'Choose a non-empty PNG, JPEG or WebP image.');
      return;
    }
    setFile(role, file || null);
    renderReferences();
    changed({ role, file });
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
    const active = toggle.checked;
    for (const id of ['monoProfile', 'monoAssetType', 'monoBrief', 'monoResolution', 'monoTexture', 'monoAdvanced', 'monoBackground']) {
      const element = $(id);
      element.hidden = active;
      if (element instanceof HTMLFieldSetElement) element.disabled = active;
    }
    for (const id of ['pixalProfile', 'supplementalViews', 'pixalViewAngles', 'pixalQuality', 'pixalTexture']) {
      const element = $(id);
      element.hidden = !active;
      if (element instanceof HTMLFieldSetElement) element.disabled = !active;
    }
    $('frontDropTitle').textContent = active ? 'Main view · front' : 'Drop your reference image';
    slots.get('front').input.setAttribute('aria-label', active ? 'Main reference, front view' : 'Reference image');
    renderReferences();
  }

  toggle.addEventListener('change', () => {
    renderMode();
    changed({ mode: true });
  });

  function setCapabilities(value) {
    capabilities = value;
    const state = $('pixalRuntimeState');
    state.textContent = value?.ready ? 'READY' : 'OPTIONAL';
    state.classList.toggle('dirty', !value?.ready);
    $('pixalRuntimeNotice').textContent = value?.ready ? 'Pixal3D ready on this computer.' :
      value?.message || 'Restart TRELLIS Studio to check the optional Pixal3D runtime.';
    const projection = value?.face_projection;
    reprojectFace.disabled = !value?.ready || !projection?.ready;
    $('reprojectFaceHelp').textContent = reprojectFace.disabled ?
      projection?.message || value?.message || 'Restart TRELLIS Studio to check face reprojection availability.' :
      'Blends the visible face from the front reference into the model texture after generation.';
    const profile = value?.profile;
    $('pixalProfileName').textContent = profile?.name || 'Character · Pixal3D';
    $('pixalProfileSummary').textContent = profile ?
      `${profile.resolution}³ · ${Number(profile.triangles).toLocaleString('en-US')} target triangles · texture ${profile.texture_size / 1024}K` :
      'Dedicated recipe for characters.';
    $('pixalTriangles').placeholder = profile ? String(profile.triangles) : 'Profile default';
    const textures = value?.texture_sizes || [1024, 2048];
    for (const option of $('pixalTextureSize').options) option.disabled = !!option.value && !textures.includes(Number(option.value));
  }

  function buildRequest() {
    const form = new FormData();
    form.set('image', slots.get('front').file);
    for (const role of roles.slice(1)) {
      const file = slots.get(role).file;
      if (file) form.set(`${role}_image`, file);
    }
    for (const [field, id] of Object.entries({
      name: 'name', height_m: 'height', parent_id: 'parentId', side_angle: 'sideAngle', seed: 'pixalSeed',
      triangles: 'pixalTriangles', texture_size: 'pixalTextureSize',
    })) {
      const value = $(id).value;
      if (value !== '') form.set(field, value);
    }
    if (reprojectFace.checked && !reprojectFace.disabled) form.set('reproject_face', 'true');
    if ($('keepFinishingSource').checked) form.set('keep_finishing_source', 'true');
    return form;
  }

  async function restore(job) {
    const settings = job.settings;
    const active = settings.engine === 'pixal3d';
    const references = active ? settings.views : [{ role: 'front', file: 'input.png' }];
    if (!Array.isArray(references) || !references.some(view => view.role === 'front') || (active && references.length < 2)) {
      throw Error('The saved reference list is incomplete.');
    }
    const restored = await Promise.all(references.map(async ({ role, file }) => {
      if (!roles.includes(role) || typeof file !== 'string') throw Error('The saved reference list is invalid.');
      const response = await fetch(`/api/jobs/${job.id}/files/${encodeURIComponent(file)}`);
      if (!response.ok) throw Error(`Saved ${role} reference is unavailable.`);
      const blob = await response.blob();
      return [role, new File([blob], `${settings.name}-${role}.png`, { type: 'image/png' })];
    }));
    const files = new Map(restored);
    for (const role of roles) setFile(role, files.get(role) || null);
    toggle.checked = active;
    reprojectFace.checked = active && settings.reproject_face === true;
    if (active) {
      $('sideAngle').value = String(settings.side_angle ?? 40);
      $('pixalSeed').value = String(settings.seed ?? 42);
      $('pixalTriangles').value = settings.triangles ?? '';
      $('pixalTextureSize').value = String(settings.texture_size ?? '');
    }
    renderMode();
  }

  renderMode();
  return {
    accept, restore, buildRequest, setCapabilities,
    get active() { return toggle.checked; },
    get hasFront() { return !!slots.get('front').file; },
    get ready() { return !!capabilities?.ready && countViews() >= 2 && !$('pixalTextureSize').selectedOptions[0]?.disabled; },
    get summary() { return `${capabilities?.profile?.name || 'Character · Pixal3D'} · ${countViews()} views · local compute`; },
    pasteRole(target) { return (toggle.checked && target.closest('[data-reference-slot]')?.dataset.referenceSlot) || 'front'; },
  };
}
