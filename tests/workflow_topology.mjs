import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {workflowTopology} from '../web/workflow-topology.js';
import {standardWorkflowDefinition} from '../web/standard-workflow-graph.js';
import {hasGenerationGraph, runningNode, runTimings} from '../web/workflow-run.js';
import {formatRunTime, readableStage, timingTotals} from '../web/workflow-timings.js';

for (const profile of ['game_asset_native', 'high_detail_native']) {
  test(`${profile} displays every configurable stage once`, async () => {
    const schema = JSON.parse(await readFile(new URL(`../native_workflows/${profile}.json`, import.meta.url), 'utf8'));
    const graph = workflowTopology(schema.stages);
    const placed = graph.columns.flat();
    assert.equal(new Set(placed).size, placed.length);
    for (const stage of schema.stages) assert.ok(placed.includes(stage.id), stage.title);
    for (const [from, to] of graph.links) {
      assert.ok(placed.includes(from), from);
      assert.ok(placed.includes(to), to);
      assert.notEqual(from, to);
    }
    assert.ok(graph.links.some(([from]) => from === 'reference'));
    assert.ok(graph.links.some(([, to]) => to === 'export'));
  });
}

test('native operations with identical global progress select different graph nodes', () => {
  const settings = {workflow_id: 'game_asset_native', geometry_backend: 'trellis2'};
  const node = (stage, progress = 84) => runningNode({state: 'running', settings, stage, progress});
  assert.equal(node('WTiVo · Tetrahedralization · 8m 42s elapsed · 40m limit', 61), '327');
  assert.equal(node('Filtering inner faces with QuadDC (5/21 batches complete)', 55), '325');
  assert.equal(node('Sampling the first shape latent (4/20)', 30), '18');
  assert.equal(node('Reducing the game mesh with LODTailor', 70), '335');
  assert.equal(node('Preparing the Character 05 UV layout', 74), '337');
  assert.equal(node('albedo 0 degrees, step 2/6'), '358');
  assert.equal(node('mr 0 degrees, step 2/6'), '364');
  assert.equal(node('Baking the 2048px UltraTex albedo and material maps'), '377');
  assert.equal(node('Finalizing model', 99), 'export');
  assert.ok(!node('Worker is starting'));
});

test('High Detail uses the recorded branch and distinguishes both repeated repair passes', () => {
  const settings = {workflow_id: 'high_detail_native', geometry_backend: 'pixal3d', texture_method: 'ultratex'};
  const node = (stage, progress) => runningNode({state: 'running', settings, stage, progress});
  assert.equal(node('Sampling the first shape latent (4/20)', 23), 'pixal');
  assert.equal(node('Reconstructing the surface with QuadDC · first pass', 43), '41');
  assert.equal(node('Reconstructing the surface with QuadDC · second pass', 72), '42');
  assert.equal(node('Reducing triangles and repairing holes', 48), '11');
  assert.equal(node('Reducing triangles and repairing holes', 77), '22');
  assert.equal(node('mr 90 degrees, step 2/4', 96), 'hd-ultratex');
});

test('standard recipes, terminal states and other operations stay separate', () => {
  const job = {state: 'running', settings: {preset: 'figure_master'}, stage: 'Building volume'};
  assert.equal(runningNode(job), 'structure');
  for (const state of ['queued', 'error', 'cancelled', 'done']) assert.ok(!runningNode({...job, state}));
  assert.equal(runningNode({...job, settings: {preset: 'high_detail', engine: 'pixal3d'},
    stage: 'Reconstructing Pixal3D geometry'}), 'pixal3d');
  assert.equal(hasGenerationGraph({...job, operation: 'animation'}), false);
  assert.equal(hasGenerationGraph({...job, settings: {preset: 'figure_master', source_kind: 'refinish'}}), false);
  for (const preset of ['figure_master', 'high_detail']) {
    const {stages, topology} = standardWorkflowDefinition(preset, []);
    const node = runningNode({...job, settings: {preset}, stage: 'Loading model weights'});
    const graph = workflowTopology(stages, topology);
    assert.equal(graph.nodes.get(node).title, 'TRELLIS.2 model loading');
    assert.ok(graph.links.some(([from, to]) => from === node && to === 'structure'));
  }
});

test('recorded intervals survive reopening and accumulate repeated visits to a node', () => {
  const settings = {workflow_id: 'game_asset_native', geometry_backend: 'trellis2'};
  const job = {state: 'running', settings, stage_timings: {version: 1, elapsed_s: 40, entries: [
    {stage: 'Starting generation', start_s: 0, duration_s: 1},
    {stage: 'Preparing albedo at 0 degrees', start_s: 1, duration_s: 4},
    {stage: 'Loading the UltraTex albedo diffusion model', start_s: 5, duration_s: 10},
    {stage: 'Preparing albedo at 0 degrees', start_s: 15, duration_s: 15},
    {stage: 'Preparing mr at 45 degrees', start_s: 30, duration_s: null},
  ]}};
  const snapshot = runTimings(job);
  const values = timingTotals(snapshot.entries, snapshot.elapsed + 2, entry => entry.node);
  assert.deepEqual(values.get('354'), {seconds: 10, state: 'done'});
  assert.deepEqual(values.get('358'), {seconds: 19, state: 'done'});
  assert.deepEqual(values.get('364'), {seconds: 12, state: 'running'});
  assert.equal(values.size, 3);
  assert.equal(snapshot.entries[4].label, 'UltraTex · Metallic & roughness · 45°');
  assert.equal(readableStage('mr 45 degrees, step 2/6'), 'UltraTex · Metallic & roughness · 45° · Step 2/6');
  assert.deepEqual(runTimings(structuredClone(job)), snapshot);
});

test('a failed or cancelled phase displays the recorded time and remains stopped', () => {
  const settings = {workflow_id: 'high_detail_native', texture_method: 'ultratex'};
  for (const state of ['error', 'cancelled']) {
    const job = {state, settings, stage_timings: {version: 1, partial: true, elapsed_s: 80, entries: [
      {stage: 'Unwrapping and baking TRELLIS.2 materials', start_s: 0, duration_s: 57},
      {stage: 'Loading the UltraTex mr diffusion model', start_s: 57, duration_s: 23, incomplete: true},
    ]}};
    const {entries, partial} = runTimings(job);
    assert.equal(partial, true);
    assert.deepEqual([...timingTotals(entries, 999, entry => entry.node)], [
      ['25', {seconds: 57, state: 'done'}], ['hd-ultratex', {seconds: 23, state: 'stopped'}],
    ]);
  }
  assert.equal(runTimings({settings, state: 'done'}), null);
  assert.equal(runTimings({settings, stage_timings: {version: 2}}), null);
  assert.equal(formatRunTime(0.4), '<1s');
  assert.equal(formatRunTime(71), '1m 11s');
  assert.equal(formatRunTime(3661), '1h 01m 01s');
});
