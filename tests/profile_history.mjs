import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createProfileHistory} from '../web/profile-history.js';
import {faceSource, refinedFaceLabel} from '../web/face-lineage.js';

const settings = {preset:'high_detail_native',profile:{id:'high_detail_native'},
  engine:'studio_workflow',workflow_id:'high_detail_native',
  workflow_options:{17:{seed:1,shape_steps:25},25:{texture_method:'ultratex',ultratex_size:4096}}};
const completed = (id, seconds, recipe=settings) => ({id,state:'done',settings:recipe,result:{seconds}});

test('only completed comparable generations enter the range, independent of seeds and JSON ordering', () => {
  const hint = {}, history=createProfileHistory(hint);
  const alternateSeed = {...settings, workflow_options:{25:settings.workflow_options[25],17:{shape_steps:25,seed:2}}};
  history.select(settings);
  history.update([completed('s1',600),completed('s2',1200,alternateSeed)]);
  assert.equal(hint.hidden,true);
  const rows=[completed('s1',600),completed('s2',1200,alternateSeed),completed('s3',1850),
    completed('derived',5,{...settings,source_kind:'face_refinement'}),
    completed('variant',1,{...settings,parent_id:'s1'}),
    completed('other profile',1,{...settings,profile:{id:'user_copy'}}),
    completed('other method',1,{...settings,workflow_options:{...settings.workflow_options,25:{texture_method:'trellis'}}}),
    completed('invalid',0),{...completed('stopped',50),state:'cancelled'}];
  history.update(rows);
  assert.equal(hint.textContent,'10–31 min observed · 3 matching runs');
  history.select({...settings, workflow_options:{...settings.workflow_options,17:{shape_steps:35}}});
  assert.equal(hint.hidden,true);
  history.select(alternateSeed);
  assert.equal(hint.hidden,false);
  history.update(rows.slice(0,1));
  assert.equal(hint.hidden,true);
});

test('standard geometry, texture size, finishing and kept source must agree', () => {
  const recipe={profile:{id:'high_detail'},preset:'high_detail',resolution:1024,texture_size:2048,
    triangles:300000,keep_finishing_source:true,generation_options:{shape_steps:25,texture_seed:-1}};
  const hint={}, history=createProfileHistory(hint);
  history.select(recipe);
  history.update([completed('a',60,recipe),completed('b',65,recipe),completed('c',70,recipe),
    ...Object.entries({triangles:200000,texture_size:4096,master_finishing:'none',keep_finishing_source:false})
      .map(([k,v])=>completed(k,9000,{...recipe,[k]:v}))]);
  assert.equal(hint.textContent,'About 1 min observed · 3 matching runs');
});

test('a source can be opened only when accessible and completed, including a zero-strength copy', () => {
  const child={source_kind:'face_refinement',parent_id:'a',face_refinement:{strength:0}};
  const source=completed('a',600);
  assert.equal(refinedFaceLabel(child),'Refined face · 0%');
  assert.equal(refinedFaceLabel({...child,face_refinement:{strength:.65}}),'Refined face · 65%');
  assert.equal(refinedFaceLabel({asset_type:'imported'}),'');
  assert.equal(faceSource(child,[source]),source);
  assert.equal(faceSource(child,[]),null);
  assert.equal(faceSource(child,[{...source,state:'running'}]),null);
});
