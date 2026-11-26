import {button} from './workbench-ui.js?v=3.4.0';

const channels = ['roughness', 'metalness'];
const storageKey = id => 'trellis.materialPreview.' + id;

function storedAdjustment(id) {
  let saved;
  try { saved = JSON.parse(localStorage.getItem(storageKey(id))); } catch {}
  const value = channel => Number.isFinite(saved?.[channel]) ? Math.max(-1, Math.min(1, saved[channel])) : 0;
  return {roughness: value('roughness'), metalness: value('metalness')};
}

// Adjust the sampled GLB channels, including their factors. Blending toward
// either endpoint also works when all texels or the authored factor are zero.
function adjustMaterial(material, uniforms) {
  const compile = material.onBeforeCompile;
  const originalKey = material.customProgramCacheKey();
  material.customProgramCacheKey = () => originalKey + '/preview-surface-v1';
  material.onBeforeCompile = function (shader, renderer) {
    compile.call(this, shader, renderer);
    shader.uniforms.previewRoughness = uniforms.roughness;
    shader.uniforms.previewMetalness = uniforms.metalness;
    shader.fragmentShader = `uniform float previewRoughness;
uniform float previewMetalness;
float adjustSurface(float source, float adjustment) {
  return mix(source, step(0.0, adjustment), abs(adjustment));
}
` + shader.fragmentShader
      .replace('#include <roughnessmap_fragment>',
        '#include <roughnessmap_fragment>\nroughnessFactor = adjustSurface(roughnessFactor, previewRoughness);')
      .replace('#include <metalnessmap_fragment>',
        '#include <metalnessmap_fragment>\nmetalnessFactor = adjustSurface(metalnessFactor, previewMetalness);');
  };
}

export function createMaterialPreview(anchor, showPbr) {
  const panel = document.getElementById('materialPreviewPanel');
  const toggle = button('Material', 'palette');
  toggle.id = 'materialPreviewToggle';
  toggle.disabled = true;
  toggle.title = 'Adjust preview roughness and metallicity';
  toggle.setAttribute('aria-haspopup', 'dialog');
  toggle.setAttribute('aria-controls', panel.id);
  toggle.popoverTargetElement = panel;
  anchor.after(toggle);

  const uniforms = {roughness: {value: 0}, metalness: {value: 0}};
  const reset = panel.querySelector('[data-reset]');
  const sliders = channels.map(channel => ({
    channel,
    input: panel.querySelector(`[data-channel="${channel}"]`),
    output: panel.querySelector(`output[for="preview-${channel}"]`),
  }));
  let modelId = null;
  let selectedId = null;

  function sync() {
    for (const {channel, input, output} of sliders) {
      const value = Math.round(uniforms[channel].value * 100);
      const text = value === 0 ? 'Original' : `${value > 0 ? '+' : '−'}${Math.abs(value)}%`;
      input.value = value;
      input.setAttribute('aria-valuetext', text);
      output.value = text;
    }
    reset.disabled = channels.every(channel => uniforms[channel].value === 0);
    toggle.classList.toggle('material-adjusted', !reset.disabled && modelId === selectedId);
    toggle.disabled = !modelId || modelId !== selectedId;
    if (toggle.disabled && panel.matches(':popover-open')) panel.hidePopover();
  }
  function save() {
    if (toggle.disabled) return;
    try {
      if (reset.disabled) localStorage.removeItem(storageKey(modelId));
      else localStorage.setItem(storageKey(modelId), JSON.stringify({
        roughness: uniforms.roughness.value, metalness: uniforms.metalness.value,
      }));
    } catch { /* Preview controls also work without browser storage. */ }
  }
  for (const {channel, input} of sliders) {
    input.addEventListener('input', () => {
      uniforms[channel].value = Number(input.value) / 100;
      sync();
    });
    input.addEventListener('change', save);
  }
  reset.onclick = () => {
    for (const channel of channels) uniforms[channel].value = 0;
    sync();
    save();
  };
  function position() {
    if (!panel.matches(':popover-open')) return;
    const anchor = toggle.getBoundingClientRect();
    const view = document.getElementById('viewport').getBoundingClientRect();
    const width = panel.offsetWidth, height = panel.offsetHeight, gap = 8;
    const leftRoom = view.left >= width + 2 * gap;
    const rightRoom = innerWidth - view.right >= width + 2 * gap;
    const nearerLeft = anchor.left - view.left <= view.right - anchor.right;
    let left = anchor.left, top = anchor.top;
    if (leftRoom && (!rightRoom || nearerLeft)) left = view.left - width - gap;
    else if (rightRoom) left = view.right + gap;
    else {
      // A stacked workspace has no sidebar. Use the free space above or below
      // the canvas, including when the user has scrolled into the preview.
      top = view.top - gap >= height || view.top > innerHeight - view.bottom
        ? view.top - height - gap : view.bottom + gap;
    }
    panel.style.left = Math.max(gap, Math.min(innerWidth - width - gap, left)) + 'px';
    panel.style.top = Math.max(gap, Math.min(innerHeight - height - gap, top)) + 'px';
  }
  panel.addEventListener('beforetoggle', event => {
    if (event.newState === 'open') {
      showPbr();
      requestAnimationFrame(position);
    }
  });
  panel.addEventListener('toggle', position);
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, {capture: true, passive: true});

  return {
    getAdjustment(id) {
      if (id !== modelId) return storedAdjustment(id);
      return {roughness: uniforms.roughness.value, metalness: uniforms.metalness.value};
    },
    select(id) {
      if (selectedId === id) return;
      selectedId = id;
      sync();
    },
    attach(id, materials) {
      const pbr = [...materials].filter(material => material?.isMeshStandardMaterial);
      modelId = pbr.length ? id : null;
      const saved = modelId ? storedAdjustment(id) : {roughness: 0, metalness: 0};
      for (const channel of channels) uniforms[channel].value = saved[channel];
      for (const material of pbr) adjustMaterial(material, uniforms);
      sync();
    },
  };
}
