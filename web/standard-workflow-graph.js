import {optionHelp} from './advanced-controls.js?v=studio-timing';
import {createWorkflowGraph} from './workflow-graph.js?v=studio-timing';

const byId = id => document.getElementById(id);

function addField(grid, control, name, hint) {
  const label = document.createElement('label');
  const caption = document.createElement('span');
  caption.textContent = name;
  const checkbox = control.type === 'checkbox';
  label.className = checkbox ? 'check option-boolean' : 'field';
  label.append(...(checkbox ? [control, caption] : [caption, control]));
  grid.append(label);
  return hint ? optionHelp(label, name, hint) : label;
}

function mirror(grid, source, name, hint) {
  const control = source.cloneNode(true);
  control.id = `graph-${source.id}`;
  control.removeAttribute('name');
  control.removeAttribute('aria-describedby');
  const entry = addField(grid, control, name, hint || source.title);
  const property = control.type === 'checkbox' ? 'checked' : 'value';
  for (const type of ['input', 'change']) control.addEventListener(type, () => {
    source[property] = control[property];
    source.dispatchEvent(new Event(type, {bubbles: true}));
  });
  return () => {
    entry.hidden = Boolean(source.closest('[hidden]'));
    control.disabled = source.matches(':disabled');
    if (control.value !== source.value || property === 'checked') control[property] = source[property];
    if (control.options) for (const [index, option] of Array.from(control.options).entries()) {
      option.disabled = source.options[index].disabled;
      option.hidden = source.options[index].hidden;
      option.title = source.options[index].title;
    }
  };
}

function enginePicker(grid, figureMaster) {
  const buttons = [...byId('generationMethod').querySelectorAll('button[value]')];
  const select = document.createElement('select');
  select.id = 'graph-generationEngine';
  select.replaceChildren(...buttons.map(button => new Option(button.querySelector('strong').textContent, button.value)));
  addField(grid, select, 'Generation engine');
  select.addEventListener('change', () => buttons.find(button => button.value === select.value)?.click());
  return () => {
    for (const [index, button] of buttons.entries()) {
      select.options[index].disabled = button.disabled || (figureMaster && button.value !== 'trellis2');
      if (button.getAttribute('aria-pressed') === 'true') select.value = button.value;
    }
  };
}

export function standardWorkflowDefinition(preset, fields) {
  const figureMaster = preset === 'figure_master';
  const advancedFields = predicate => fields.filter(predicate).map(field => [
    `option_${field.key}`, field.label.replace(/^(Structure|Shape|Texture) · /, ''), field.hint,
  ]);
  const stage = (id, title, type, controls, extra) => ({id, title, type, fields: controls, ...extra});
  const stages = [
    stage('reference', 'Reference image', 'reference', [['removeBg', 'Remove image background']], {open: true}),
    stage('structure', 'Sparse structure', 'geometry', [['seed', '3D seed'],
      ...advancedFields(field => field.key.startsWith('structure_'))], {geometry_backend: 'trellis2'}),
    stage('shape', 'Shape generation', 'geometry', [['resolution', '3D resolution'],
      ...advancedFields(field => field.key.startsWith('shape_'))], {geometry_backend: 'trellis2'}),
    stage('finish', 'Mesh finishing', 'mesh', [['denseMasterFinishing', 'Master finishing'],
      ['triangles', 'Triangle budget'], ...advancedFields(field => ['mesh', 'hi3d'].includes(field.group))]),
    stage('uv', 'UV unwrapping', 'mesh', advancedFields(field => field.group === 'uv')),
    stage('texture', 'Materials and baking', 'texture', [['textureMethod', 'Texture method'],
      ['textureSize', 'Texture size'], ['retextureAfterRefinement', 'Retexture after Hi3D'],
      ['reprojectFace', 'Reproject the reference face'], ...advancedFields(field => field.group === 'texture')], {open: true}),
    stage('export', 'Model GLB', 'output', [['height', 'Height (m)'], ['keepFinishingSource', 'Keep finishing source']]),
  ];
  const topology = {
    columns: [['reference', 'weights'], ['structure', 'pixal3d'], ['shape'], ['finish'], ['uv'], ['texture'], ['export']],
    stages: {
      weights: {title: 'TRELLIS.2 model loading', type: 'geometry', geometry_backend: 'trellis2'},
      ...(!figureMaster && {pixal3d: {title: 'PiXal3D generation', type: 'geometry', geometry_backend: 'pixal3d'}}),
    },
    links: [['reference', 'structure', 'image'], ['weights', 'structure', 'model'], ['structure', 'shape', 'latent'],
      ['shape', 'finish', 'mesh'], ['reference', 'pixal3d', 'image'], ['pixal3d', 'finish', 'mesh'],
      ['finish', 'uv', 'mesh'], ['uv', 'texture', 'mesh'], ['texture', 'export', 'texture']],
  };
  return {stages, topology};
}

export function createStandardWorkflowGraph(workspace, profileName, preset, fields) {
  const figureMaster = preset === 'figure_master';
  const {stages, topology} = standardWorkflowDefinition(preset, fields);
  const update = [];
  const graph = createWorkflowGraph(workspace, [{stages}], profileName, node => {
    const grid = document.createElement('div');
    grid.className = 'option-grid workflow-graph-fields';
    if (node.id === 'reference') update.push(enginePicker(grid, figureMaster));
    if (node.id === 'finish' && figureMaster) {
      const note = document.createElement('p');
      note.className = 'hint';
      note.textContent = 'Remeshing uses QuadDC + MeshLib when installed, with native dual contouring as the fallback.';
      grid.append(note);
      update.push(() => {
        note.hidden = byId('denseMasterFinishing').value !== '' || byId('option_mesh_mode').value !== 'remesh';
      });
    }
    for (const [id, name, hint] of node.fields) {
      const source = byId(id);
      if (source) update.push(mirror(grid, source, name, hint));
    }
    return grid;
  }, topology);
  return {
    opener: graph.opener,
    sync(backend, method) {
      for (const refresh of update) refresh();
      graph.sync(backend, method);
    },
    close() { graph.close(false); },
    remove() { graph.close(false); graph.opener.remove(); },
  };
}
