import {createWorkflowGraph, groupWorkflowStages} from './workflow-graph.js?v=standard-profiles';

const element=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;};
const commonHelp={
  "assetType": "Classifies the model by intended use. Generation creates a static mesh; rigging, moving parts and game behaviours are separate steps.",
  "image": "Reference image for 3D generation. A complete, readable object isolated from the background works best.",
  "name": "Model name in the library and exports. The name does not change generated appearance.",
  "preset": "Game - Default targets 28,000 triangles. Dense Master measures surface error to choose a lighter working mesh within a default ceiling of 1.2 million triangles. It saves the decoded source for later finishing.",
  "resolution": "3D generation grid: 512\u00b3, 1024\u00b3 or 1536\u00b3. Higher values require more time and memory. Texture size is independent.",
  "height": "Final model height in metres. Dimensions scale proportionally, with the origin placed at ground level.",
  "textureSize": "Square texture size. Pixels are shared across all surfaces. UltraTex uses a fixed 2K atlas.",
  "removeBg": "Removes an opaque background. Images that are already transparent keep their cutout.",
  "triangles": "Triangle budget for the final mesh. Higher values can retain details that are already present in the generated surface. Enter zero to use the profile budget.",
  "seed": "Random seed for geometry generation. Keep it fixed to compare settings; small GPU variations may remain.",
  "advancedMasterFinishing": "Overrides the selected master finishing for this generation. Direct Export simplifies the generated surface. Repair Holes rebuilds the surface to fill small openings.",
  "reprojectFace": "Blends the visible face from the front reference into the Pixal3D texture after generation.",
  "keepFinishingSource": "Keeps the decoded geometry and materials for later finishing variants. The saved source can use substantial disk space.",
  "profileBase": "Selects the preset used for generation defaults and triangle budgets. Values you have entered in this form remain available when you change it.",
  "referencePrompt": "Context stored with the model and included in the image brief. It is not sent as a text prompt to the image-to-3D engine.",
  "terrainName": "Terrain name in the library and exported files.",
  "terrainStyle": "Procedural terrain family: hills, rocks, basin or plateaus.",
  "terrainSize": "Width and length of the square terrain in metres.",
  "terrainHeight": "Maximum requested elevation range in metres.",
  "terrainPalette": "Colour and material family generated for the terrain.",
  "roughness": "Small-scale terrain detail. This is independent of PBR material roughness.",
  "flatZone": "Size of the flattened region in the terrain.",
  "terrainResolution": "Heightmap sample count. A larger heightmap produces more detail and more data.",
  "terrainSeed": "Random seed for terrain. The same seed and settings reproduce the same terrain.",
  "displayName": "Library display name. Renaming does not regenerate the model.",
  "category": "Category used to organise and search the library.",
  "reviewState": "Your review of the result: to review, needs changes, retained or rejected.",
  "tags": "Comma-separated keywords to help find your models.",
  "notes": "Notes and intended changes stored with this model. They do not modify geometry."
};
let activeHelp;
export function optionHelp(label, fieldName, hint) {
  const control = label.querySelector('input, select, textarea');
  const field = element('div', 'option-with-help');
  const heading = element('div', 'option-help-heading');
  const button = element('button', 'option-help-button', '?');
  const explanation = element('p', 'hint option-help-text', hint);
  let pinned = false;
  let dismissTimer;
  explanation.id = control.id + 'OptionHelp';
  explanation.setAttribute('popover', 'manual');
  explanation.setAttribute('role', 'tooltip');
  button.type = 'button';
  button.setAttribute('aria-label', 'Help for ' + fieldName);
  button.setAttribute('aria-describedby', explanation.id);
  button.setAttribute('aria-expanded', 'false');

  function place() {
    const anchor = button.getBoundingClientRect();
    if (!anchor.width || anchor.bottom < 0 || anchor.top > innerHeight) {
      dismiss();
      return;
    }
    const {width, height} = explanation.getBoundingClientRect();
    const above = anchor.bottom + height + 10 > innerHeight && anchor.top >= height + 10;
    explanation.style.left = Math.max(8, Math.min(innerWidth - width - 8, anchor.left)) + 'px';
    explanation.style.top = Math.max(8, Math.min(innerHeight - height - 8,
      above ? anchor.top - height - 4 : anchor.bottom + 4)) + 'px';
  }
  function dismiss() {
    if (activeHelp !== dismiss) return;
    activeHelp = null;
    clearTimeout(dismissTimer);
    pinned = false;
    explanation.hidePopover();
    button.setAttribute('aria-expanded', 'false');
    window.removeEventListener('scroll', place, true);
    window.removeEventListener('resize', place);
    document.removeEventListener('pointerdown', outside);
    document.removeEventListener('keydown', escape);
  }
  function outside(event) {
    if (!button.contains(event.target) && !explanation.contains(event.target)) dismiss();
  }
  function escape(event) {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    dismiss();
  }
  function show() {
    clearTimeout(dismissTimer);
    if (activeHelp === dismiss) return;
    activeHelp?.();
    activeHelp = dismiss;
    explanation.showPopover();
    button.setAttribute('aria-expanded', 'true');
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    place();
  }
  function scheduleDismiss() {
    clearTimeout(dismissTimer);
    if (!pinned && !button.matches(':focus-visible')) dismissTimer = setTimeout(dismiss, 150);
  }
  button.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse') show(); });
  button.addEventListener('pointerleave', scheduleDismiss);
  button.addEventListener('focus', () => { if (button.matches(':focus-visible')) show(); });
  button.addEventListener('blur', dismiss);
  button.addEventListener('click', () => {
    if (pinned) dismiss();
    else {
      show();
      pinned = true;
    }
  });
  explanation.addEventListener('pointerenter', () => clearTimeout(dismissTimer));
  explanation.addEventListener('pointerleave', scheduleDismiss);
  explanation.addEventListener('toggle', event => { if (event.newState === 'closed') dismiss(); });

  label.before(field);
  field.append(heading, explanation);
  if (control.type === 'checkbox') {
    heading.classList.add('option-help-checkbox-heading');
    label.prepend(control);
    heading.append(label, button);
  } else {
    const caption = element('label', 'option-help-caption');
    caption.htmlFor = control.id;
    caption.append(label.querySelector(':scope > span'));
    heading.append(caption, button);
    field.append(label);
  }
  return field;
}

function attachCommonHelp() {
  for (const [id, hint] of Object.entries(commonHelp)) {
    const input = document.getElementById(id);
    if (!input) continue;
    const label = input.closest('label');
    if (label?.closest('#advancedSettings')) {
      const fieldName = label.firstChild.textContent.trim() || label.querySelector('span')?.textContent;
      if (label.firstChild.nodeType === Node.TEXT_NODE) label.firstChild.replaceWith(element('span', '', fieldName));
      optionHelp(label, fieldName, hint);
    } else {
      input.title = hint;
      if (label) label.title = hint;
    }
  }
}

let workflowEditorId = 0;
function addWorkflowField(grid, field, name, control) {
  const label = element('label', field.kind === 'boolean' ? 'check option-boolean' : 'field');
  const text = element('span', '', name);
  if (field.kind === 'boolean') label.append(control, text);
  else label.append(text, control);
  grid.append(label);
  if (field.hint) optionHelp(label, name, field.hint);
}

function mirrorWorkflowField(control, peer) {
  const property = control.type === 'checkbox' ? 'checked' : 'value';
  peer[property] = control[property];
  for (const type of ['input', 'change']) {
    control.addEventListener(type, () => { peer[property] = control[property]; });
    peer.addEventListener(type, () => {
      control[property] = peer[property];
      control.dispatchEvent(new Event(type, {bubbles: true}));
    });
  }
}

export function createWorkflowFields(container, stages, values = {}, {workspace, profileName} = {}) {
  const id = ++workflowEditorId;
  const controls = new Map();
  const sections = new Map();
  const groups = groupWorkflowStages(stages);
  const groupsByStage = new Map();
  let geometry;
  let material;
  container.replaceChildren();
  for (const group of groups) {
    const section = element('details', 'workflow-option-section');
    section.open = group.title === 'Geometry';
    section.append(element('summary', '', group.title));
    group.stages.forEach(stage => groupsByStage.set(stage.id, section));
    container.append(section);
  }
  for (const [index, stage] of stages.entries()) {
    const details = element('details', 'option-group');
    details.open = index === 0 || stage.id === '316' || stage.id === '44';
    details.append(element('summary', '', stage.title));
    const grid = element('div', 'option-grid');
    const entries = new Map();
    for (const field of stage.fields) {
      const value = values[stage.id]?.[field.name] ?? field.default;
      const control = element(field.kind === 'choice' ? 'select' : field.name === 'text' ? 'textarea' : 'input');
      control.id = `workflow-${id}-${stage.id}-${field.name}`;
      control.disabled = field.locked === true || field.kind === 'choice' && field.choices.length === 1;
      const fixed = control.disabled;
      if (field.kind === 'choice') {
        control.replaceChildren(...field.choices.map((choice, number) => {
          const option = new Option(choice.label, String(number));
          option.disabled = choice.available === false;
          if (choice.reason) option.title = choice.reason;
          return option;
        }));
        control.selectedIndex = field.choices.findIndex(choice => Object.is(choice.value, value));
      } else if (field.kind === 'boolean') {
        control.type = 'checkbox';
        control.checked = value;
      } else if (field.kind === 'text' || field.kind === 'large_integer') {
        if (control.tagName === 'TEXTAREA') control.rows = 3;
        else control.type = 'text';
        control.value = value;
        control.maxLength = field.kind === 'text' ? 6000 : 20;
        if (field.kind === 'large_integer') {
          control.required = true;
          control.pattern = '[0-9]+';
          control.inputMode = 'numeric';
        }
      } else {
        control.type = 'number';
        control.step = field.kind === 'integer' ? String(field.enforce_step ? field.step : 1) : 'any';
        control.required = true;
        if (field.min !== undefined) control.min = field.min;
        if (field.max !== undefined) control.max = field.max;
        control.value = value;
      }
      const name = field.name === 'geometry_backend' || stage.id === '316' && field.name === 'value'
        ? 'Geometry engine' : field.label;
      addWorkflowField(grid, field, name, control);
      entries.set(field, {control, fixed, name});
      if (name === 'Geometry engine') geometry = {field, control};
      if (field.name === 'texture_method') material = {field, control};
    }
    controls.set(stage.id, entries);
    sections.set(stage.id, details);
    details.append(grid);
  }
  for (const group of groups) for (const stage of group.stages) groupsByStage.get(stage.id).append(sections.get(stage.id));
  const graph = workspace && createWorkflowGraph(workspace, groups, profileName, stage => {
    const grid = element('div', 'option-grid workflow-graph-fields');
    for (const [field, entry] of controls.get(stage.id)) {
      const peer = entry.control.cloneNode(true);
      peer.id += '-graph';
      mirrorWorkflowField(entry.control, peer);
      addWorkflowField(grid, field, entry.name, peer);
      entry.peer = peer;
    }
    return grid;
  });
  if (graph) container.prepend(graph.opener);
  const synchronize = () => {
    const choice = geometry?.field.choices[geometry.control.selectedIndex]?.value;
    const backend = typeof choice === 'boolean' ? (choice ? 'trellis2' : 'pixal3d') : choice;
    for (const stage of stages) {
      const hidden = stage.geometry_backend !== undefined && stage.geometry_backend !== backend;
      sections.get(stage.id).hidden = hidden;
      for (const {control, fixed, peer} of controls.get(stage.id).values()) {
        control.disabled = hidden || fixed;
        if (peer) peer.disabled = control.disabled;
      }
    }
    graph?.sync(backend, material?.field.choices[material.control.selectedIndex]?.value);
  };
  geometry?.control.addEventListener('change', synchronize);
  material?.control.addEventListener('change', synchronize);
  synchronize();
  return {
    closeGraph() { graph?.close(false); },
    read() {
      return Object.fromEntries([...controls].map(([id, entries]) => [id, Object.fromEntries([...entries].map(([field, {control}]) => {
        if (sections.get(id).hidden) return [field.name, field.default];
        let value = control.value;
        if (field.kind === 'choice') value = field.choices[control.selectedIndex]?.value;
        if (field.kind === 'boolean') value = control.checked;
        if (field.kind === 'number' || field.kind === 'integer') value = control.valueAsNumber;
        return [field.name, value];
      }))]));
    },
    validate() {
      for (const entries of controls.values()) {
        for (const {control} of entries.values()) {
          if (control.checkValidity()) continue;
          control.closest('details').open = true;
          const section = control.closest('.workflow-option-section');
          if (section) section.open = true;
          control.reportValidity();
          return false;
        }
      }
      return true;
    },
  };
}

export async function createAdvancedControls(container,{groupId,title,idPrefix='option_'}={}){
  if(!groupId)attachCommonHelp();
  let schema,ready=false;
  let fields=[];
  const inputs=new Map();
  try{
    const [response,helpResponse]=await Promise.all([fetch('/api/generation-options'),fetch('/web/generation-help.en.json?v=figure-master')]);
    if(!response.ok)throw Error('Restart the TRELLIS server and reload the page to enable advanced settings.');
    schema=await response.json();
    const help=helpResponse.ok?await helpResponse.json():{};
    const groups=groupId?schema.groups.filter(group=>group.id===groupId):schema.groups;
    fields=groupId?schema.fields.filter(field=>field.group===groupId):schema.fields;
    if(!groups.length||!fields.length)throw Error('Advanced settings are not available on this server yet.');
    for(const field of fields)field.hint=help[field.key]||field.hint;
    for(const group of groups){
      const details=element('details','option-group');details.dataset.group=group.id;
      details.open=group.id==='geometry';
      details.append(element('summary','',title||group.title),element('p','hint',group.hint));
      const grid=element('div','option-grid');details.append(grid);
      let phase;
      for(const field of fields.filter(f=>f.group===group.id)){
        let fieldName=field.label;
        if(group.id==='geometry'&&field.label.includes(' · ')){
          const [phaseName,parameter]=field.label.split(' · ');
          if(phaseName!==phase){
            phase=phaseName;
            grid.append(element('h3','option-phase',phase));
          }
          fieldName=parameter[0].toUpperCase()+parameter.slice(1);
        }
        const label=element('label',field.type==='boolean'?'check option-boolean':'field');
        const input=element(field.type==='choice'?'select':'input');input.id=idPrefix+field.key;
        if(field.type==='choice')for(const choice of field.choices){const option=element('option','',choice.label);option.value=choice.value;input.append(option);}
        else if(field.type==='boolean')input.type='checkbox';
        else{input.type='number';input.min=field.min;input.max=field.max;input.step=field.step;input.required=true;}
        if(field.type==='boolean'){input.checked=field.default;label.append(input,element('span','',fieldName));}
        else{label.append(element('span','',fieldName),input);input.value=field.default;}
        if(fieldName!==field.label)input.setAttribute('aria-label',field.label);
        inputs.set(field.key,input);grid.append(label);
        const entry=field.hint?optionHelp(label,field.label,field.hint):label;
        entry.dataset.option=field.key;
      }
      container.append(details);
    }
    ready=true;
  }catch(error){container.append(element('p','advanced-error',error.message));}
  function read(){
    if(!ready)throw Error('Advanced settings are not available on this server yet.');
    return Object.fromEntries(fields.map(field=>[field.key,field.type==='boolean'?inputs.get(field.key).checked:field.type==='choice'?inputs.get(field.key).value:Number(inputs.get(field.key).value)]));
  }
  function sync(){
    const remesh=inputs.get('mesh_mode')?.value==='remesh';
    for(const key of ['remesh_project','remesh_band','remesh_resolution'])if(inputs.has(key))inputs.get(key).closest('[data-option]').hidden=!remesh;
    for(const key of ['structure_steps','shape_steps','texture_steps'])if(inputs.has(key))inputs.get(key).placeholder='0 = profile';
  }
  function apply(values={}){
    if(!ready)return;
    for(const field of fields){const value=values[field.key]??field.default,input=inputs.get(field.key);if(field.type==='boolean')input.checked=value;else input.value=String(value);}
    sync();
  }
  inputs.get('mesh_mode')?.addEventListener('change',sync);sync();
  return {get ready(){return ready;},get defaults(){return schema?.default_generation;},get fields(){return fields;},read,apply,reset:()=>apply(),
    group:id=>container.querySelector(`[data-group="${id}"]`),
    openGroup:id=>{const group=container.querySelector(`[data-group="${id}"]`);if(group)group.open=true;}};
}
