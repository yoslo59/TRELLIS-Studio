import {createWorkflowGraph, groupWorkflowStages} from './workflow-graph.js?v=studio-timing';
import {standardWorkflowDefinition} from './standard-workflow-graph.js?v=studio-timing';
import {timingSnapshot} from './workflow-timings.js';
import {icon} from './card-menu.js';

const nativeProfiles = ['game_asset_native', 'high_detail_native'];
const standardProfiles = ['figure_master', 'high_detail'];

export function hasGenerationGraph(job) {
  const settings = job?.settings;
  return Boolean(settings && !settings.source_kind && !job.operation &&
    (settings.workflow_id ? nativeProfiles.includes(settings.workflow_id) : standardProfiles.includes(settings.preset)));
}

// Match reports emitted by the workers. Several operators share a percentage;
// elapsed time or a percentage by itself cannot identify the running operator.
const gameStages = [
  [/^Removing the reference background/, '193'], [/^Estimating the reference camera/, '56'],
  [/^Extracting DINOv3/, '15'], [/^Sampling the sparse structure/, '3'],
  [/^Decoding the sparse structure/, '119'], [/^Sampling the first shape latent/, '18'],
  [/^Subdividing the shape latent/, '94'], [/^Sampling the high-resolution shape/, '23'],
  [/^Decoding the high-resolution surface/, 'decode'], [/QuadDC/, '325'], [/WTiVo/, '327'],
  [/^(Preparing (high- and low-poly|the Character 05 high and low) meshes|Reducing the dense donor)/, '328'],
  [/^Reducing the game mesh/, '335'], [/^Preparing the (Character 05 )?UV layout/, '337'],
  [/^Smoothing the unwrapped/, '338'], [/^Baking the (Character 05 )?(normal map|surface maps)/, '341'],
  [/^Merging (vertices|the textured Character 05 surface)/, '342'],
  [/^(Verifying six pinned UltraTex|Starting the UltraTex runtime|Loading the UltraTex (albedo|mr) diffusion model)/, '354'],
  [/^Loading native UltraTex reference/, '375'], [/^Loading the UltraTex VAE/, '355'],
  [/^(Encoding the UltraTex text prompts|Loading the UltraTex text encoder)/, '356'], [/^(Preparing albedo at|albedo )/, '358'],
  [/^(Preparing mr at|mr )/, '364'], [/^UltraTex memory estimate/, '387'],
  [/^Baking the \d+px UltraTex/, '377'], [/^Assembling the UltraTex material/, '384'],
];

const standardStages = [
  [/^Loading model weights$/, 'weights'],
  [/^(Preparing image|Analysing image|Preparing Pixal3D reference|Estimating a single camera|Encoding Pixal3D reference)/, 'reference'],
  [/^Building volume/, 'structure'], [/^Generating geometry/, 'shape'],
  [/^(Loading Pixal3D model|Building Pixal3D volume|Reconstructing Pixal3D geometry)/, 'pixal3d'],
  [/^Unwrapping/, 'uv'],
  [/^(Extracting mesh|Saving reusable finishing source|Finishing mesh|Reconstructing the |Reducing (and baking|the QuadDC)|Measuring detail|Refining the decoded|Trimming the dense|Finishing the WTiVo)/, 'finish'],
  [/^(Generating (materials|Pixal3D materials|TRELLIS\.2 materials|six )|Baking |Preparing the fixed surface|Transferring master detail|Aligning the reference for texture|Harmonizing colors|Reprojecting the original face)/, 'texture'],
];
const match = (rules, stage) => rules.find(([pattern]) => pattern.test(stage))?.[1];

function geometryBackend(settings) {
  if (settings.geometry_backend) return settings.geometry_backend;
  if (!settings.workflow_id) return settings.engine || 'trellis2';
  if (settings.workflow_id === 'high_detail_native') return settings.workflow_options?.['44']?.geometry_backend;
  const trellis = settings.workflow_options?.['316']?.value;
  if (typeof trellis === 'boolean') return trellis ? 'trellis2' : 'pixal3d';
}

const textureMethod = settings => settings.texture_method || settings.workflow_options?.['25']?.texture_method;

function reportNode(settings, {stage = '', progress}) {
  if (/^(Finalizing model|Writing the Studio GLB)/.test(stage)) return 'export';
  if (settings.workflow_id === 'game_asset_native') {
    if (/^Loading native (structure_model|img2shape)/.test(stage)) return {pixal3d: '319', trellis2: '40'}[geometryBackend(settings)];
    return match(gameStages, stage);
  }
  if (settings.workflow_id === 'high_detail_native') {
    if (stage === 'Preparing the reference image') return '12';
    if (stage === 'Loading TRELLIS.2 GGUF model weights') return '44';
    if (stage === 'Generating dense TRELLIS.2 geometry') return '17';
    if (stage === 'Reconstructing the surface with QuadDC · first pass') return '41';
    if (stage === 'Reconstructing the surface with QuadDC · second pass') return '42';
    if (stage === 'Reducing triangles and repairing holes · first pass') return '11';
    if (stage === 'Reducing triangles and repairing holes · second pass') return '22';
    // Workers started before this update report the same text for the two passes.
    if (stage === 'Reconstructing the surface with QuadDC') return ({43: '41', 72: '42'})[progress];
    if (stage === 'Reducing triangles and repairing holes') return ({48: '11', 77: '22'})[progress];
    if (stage === 'Orienting the mesh · first pass') return '14';
    if (stage === 'Orienting the mesh · second pass') return '23';
    if (stage === 'Refining the TRELLIS.2 mesh') return '20';
    if (['Generating TRELLIS.2 materials', 'Unwrapping and baking TRELLIS.2 materials',
      'Baking TRELLIS.2 vertex materials'].includes(stage)) return '25';
    if (stage === 'Packaging the mesh and its UV atlas') return textureMethod(settings) === 'ultratex' ? '25' : 'export';
    if (/UltraTex|^(Preparing (mr|albedo) at|mr |albedo )/.test(stage)) return 'hd-ultratex';
    if (geometryBackend(settings) === 'pixal3d' && (stage === 'Preparing the Pixal3D reference' ||
        /^Loading native (structure_model|img2shape)/.test(stage) || match(gameStages, stage))) return 'pixal';
    return null;
  }
  return match(standardStages, stage);
}

export function runningNode(job) {
  if (job?.state !== 'running' || !hasGenerationGraph(job)) return null;
  return reportNode(job.settings, job);
}

export function runTimings(job) {
  return timingSnapshot(job, entry => reportNode(job.settings, entry));
}

const settingKeys = {
  removeBg: 'remove_bg', denseMasterFinishing: 'master_finishing', textureMethod: 'texture_method',
  textureSize: 'texture_size', retextureAfterRefinement: 'retexture_after_refinement',
  reprojectFace: 'reproject_face', height: 'height_m', keepFinishingSource: 'keep_finishing_source',
};

function savedValues(rows) {
  const list = document.createElement('dl');
  list.className = 'workflow-saved-values';
  for (const [label, value] of rows) {
    if (value === undefined || value === null) continue;
    const name = document.createElement('dt'), item = document.createElement('dd');
    name.textContent = label;
    item.textContent = typeof value === 'boolean' ? (value ? 'On' : 'Off') : String(value);
    list.append(name, item);
  }
  return list;
}

export function createRunGraph(workspace, job, nativeStages, fields) {
  const settings = job.settings;
  const name = [job.metadata?.display_name || settings.name,
    settings.profile?.name || settings.profile_snapshot?.name ||
      ({high_detail: 'High Detail (Props)', figure_master: 'Figure Master', high_detail_native: 'High Detail', game_asset_native: 'Game Asset'})[settings.preset]
  ].filter(Boolean).join(' · ');
  let groups, topology, rows;
  if (settings.workflow_id) {
    if (!nativeStages?.length) return null;
    groups = groupWorkflowStages(nativeStages);
    rows = stage => stage.fields.map(field => {
      const value = settings.workflow_options?.[stage.id]?.[field.name];
      const choice = field.choices?.find(item => Object.is(item.value, value));
      return [field.label, choice?.label ?? value];
    });
  } else {
    const standard = standardWorkflowDefinition(settings.preset, fields);
    groups = [{stages: standard.stages}];
    topology = standard.topology;
    rows = stage => stage.fields.map(([key, label]) => [label, key.startsWith('option_')
      ? (settings.generation_options || job.generation_options)?.[key.slice(7)] : settings[settingKeys[key] || key]]);
  }
  groups = groups.map(group => ({...group, stages: group.stages.map(stage => {
    const entries = rows(stage).filter(([, value]) => value != null);
    return {...stage, fields: entries, open: false};
  })}));
  const graph = createWorkflowGraph(workspace, groups, name, stage => savedValues(stage.fields), topology, {monitor: true});
  graph.sync(geometryBackend(settings), textureMethod(settings));
  return graph;
}

export function createGenerationMonitor(workspace, {getStages, fields}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'generationGraph';
  button.className = 'secondary workflow-graph-open';
  button.textContent = 'View generation graph';
  button.prepend(icon('grid'));
  button.setAttribute('aria-expanded', 'false');
  workspace.querySelector('.generation-wait-card').append(button);
  const saved = workspace.querySelector('#savedGenerationGraph');
  let current, graph;

  function close() {
    graph?.close(false);
    graph = null;
  }
  function available(job) {
    return hasGenerationGraph(job) && (!job.settings.workflow_id || Boolean(getStages(job.settings.workflow_id)?.length));
  }
  function update(job) {
    if (current?.id !== job?.id || !available(job)) close();
    current = job;
    const enabled = available(job);
    const inProgress = job?.state === 'queued' || job?.state === 'running';
    button.hidden = !enabled || !inProgress;
    if (saved) saved.hidden = !enabled || inProgress;
    if (graph?.visible) graph.setRun(job, runningNode(job), runTimings(job));
  }
  function open(trigger = button) {
    if (!available(current)) return;
    graph = createRunGraph(workspace, current, getStages(current.settings.workflow_id), fields);
    graph.setRun(current, runningNode(current), runTimings(current));
    graph.open(trigger);
  }
  button.onclick = () => open();
  if (saved) saved.onclick = () => open(saved);
  button.hidden = true;
  return {available, close, open, update};
}
