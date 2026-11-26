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
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', panel.id);
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
    if (toggle.disabled) close();
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
    sliders[0].input.focus({preventScroll: true});
  };
  function close() {
    if (panel.hidden) return;
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  }
  toggle.onclick = () => {
    if (!panel.hidden) {
      close();
      return;
    }
    showPbr();
    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    sliders[0].input.focus({preventScroll: true});
  };
  panel.closest('#workspace').addEventListener('keydown', event => {
    if (event.key !== 'Escape' || panel.hidden || panel.inert) return;
    close();
    toggle.focus({preventScroll: true});
    event.preventDefault();
  });
  document.getElementById('viewTextures').addEventListener('click', close);
  for (const mode of document.querySelectorAll('[data-material]:not([data-material="pbr"])')) {
    mode.addEventListener('click', close);
  }
  // The graph temporarily makes the preview controls inert.
  new MutationObserver(() => { if (panel.inert) close(); })
    .observe(panel, {attributes: true, attributeFilter: ['inert']});

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
