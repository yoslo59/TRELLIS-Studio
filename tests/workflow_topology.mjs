import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {workflowTopology} from '../web/workflow-topology.js';

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
