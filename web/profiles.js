// Recipes are persisted by the local server, independently from object history.
import {detailRefinements} from './hd-master-finishing.js?v=detail-refinement-native';
import {createProfileHistory} from './profile-history.js';
import {createWorkflowFields, optionHelp} from './advanced-controls.js?v=studio-timing';
import {createStandardWorkflowGraph} from './standard-workflow-graph.js?v=studio-timing';
import {textureMethodChoices,textureMethodHelp,textureMethodOf,textureMethodRestriction} from './texture-methods.js?v=figure-master-ultratex';

const $=id=>document.getElementById(id);
const steps={game_d:20,high_detail:20,humanoid_experimental:20,dense_master:20,fast:8,balanced:12,detail:20};
const figureSteps={structure_steps:30,shape_steps:35,texture_steps:12};
const startupKey='trellis.studio.startup-profile.v2';
const profileNames={game_d:'Game Asset (Standard)',dense_master:'HD Master'};
const profilePurposes={
  high_detail_native:'For close-ups and high-density geometry.',
  game_asset_native:'For real-time characters and lightweight assets.',
  high_detail:'For props with fine surface detail.',
  figure_master:'For figures and sculpted surface detail.',
};
const restoredWorkflows={game_asset_comfy:'game_asset_native',high_detail_comfy:'high_detail_native'};
const finishingNames={
  none:'None',
  studio_768:'Balanced Reconstruction',
  wtivo_1024:'Surface Closure',
  direct:'Direct Export',
  repair:'Repair Holes',
};
const finishingDetails={
  '':'Uses the profile’s standard mesh processing.',
  none:'Exports the decoded mesh at its original triangle count and keeps the source for finishing later.',
  studio_768:'Studio 768 · Rebuilds the surface and retains fine details.',
  wtivo_1024:'WTiVo 1024 · Prioritizes closing surface gaps.',
  direct:'Adaptive reduction within the triangle ceiling.',
  repair:'Fills small gaps while retaining the source for variants.',
};
const finishingName=value=>detailRefinements[value]?.name||finishingNames[value];
const defaultFinishing=preset=>preset==='dense_master'?'studio_768':'';
const isReconstruction=value=>Boolean(detailRefinements[value])||value==='studio_768'||value==='wtivo_1024';
const number=new Intl.NumberFormat('en-US');
const inlineFinishingLimit=3000000;
const detailRefinementLimit=1000000;
const detailSourceResolutions=[512,1024,1536];
const retextureHelp='Generates new PBR materials from the reference on the refined geometry. Uses the texture settings below and adds processing time.';
export async function createProfileControls(advanced,{changed=()=>{},usePixal=()=>false,isMultiview=()=>false,describeMethod=()=>'',useNativeMethod=()=>{},setWorkflow=()=>{}}={}){
  const history=createProfileHistory($('profileHistory'));
  let profiles=[],selected=null,saving=false,edit=false,sourceRequired=false,previousKeep=false;
  let deletingProfile=null,deleting=false,serverStartup='';
  let repairSupported=false, hdSupported=false, masterSupport={};
  let detailTexturing={};
  let textureSupport={},preferredTextureMethod='trellis',ultraAtlas=null;
  let startupProfile='', refinementInputs=null, standardFinishing='', pixalFallback=false, pixalAtlas=null;
  let workflowFields, savedWorkflowValues, standardGraph, standardGraphKey;
  const workflowBox=$('workflowAdvancedControls');
  function removeStandardGraph() {
    standardGraph?.remove();
    standardGraph = standardGraphKey = undefined;
  }
  function showWorkflowFields(profile, values={}) {
    removeStandardGraph();
    workflowFields?.closeGraph();
    workflowFields=createWorkflowFields(workflowBox,profile.workflow.advanced,values,
      {workspace:$('workspace'),profileName:profile.name});
  }
  const retexture=$('retextureAfterRefinement');
  const retextureField=$('detailTexturingField');
  const retextureStatus=$('detailTexturingStatus');
  optionHelp(retexture.closest('label'), 'Retexture after Hi3D', retextureHelp);
  const textureMethod=$('textureMethod');
  const textureMethodStatus=$('textureMethodStatus');
  textureMethod.replaceChildren(...textureMethodChoices.map(([id,name])=>new Option(name,id)));
  optionHelp(textureMethod.closest('label'),'Texture method',textureMethodHelp);
  const textures=advanced.group('texture');
  if(textures)textures.querySelector('.option-grid').prepend(retextureField);
  const readFinishing=()=>$('preset').value==='dense_master'&&$('advancedMasterFinishing').value
    || $('denseMasterFinishing').value;
  const retextureEligible=()=>!usePixal()&&readFinishing()==='hi3d_1024'&&Number($('resolution').value)===1024;
  const wantsRetexture=()=>retextureEligible()&&retexture.checked;
  function read(){
    const {texture_seed,...options}=advanced.read();
    const preset=$('preset').value;
    const defaults=selected?.settings.preset===preset
      ? selected.settings : profiles.find(profile=>profile.id===preset)?.settings;
    const requestedTriangles=Number($('triangles').value);
    const retainBudget=readFinishing()!=='none'||(Number.isInteger(requestedTriangles)&&requestedTriangles>=1000&&requestedTriangles<=8000000);
    return {
      preset,resolution:Number($('resolution').value),
      triangles:(retainBudget&&requestedTriangles)||defaults?.triangles||
        ({game_d:28000,high_detail:250000,figure_master:800000,humanoid_experimental:250000,dense_master:1200000,
          character_pixal:28000,fast:25000,balanced:50000,detail:100000}[preset]),
      texture_size:Number($('textureSize').value)||defaults?.texture_size||(preset==='fast'?1024:2048),
      remove_bg:$('removeBg').checked,advanced:options,
    };
  }
  function budget(recipe, workflowValues){
    const workflow=selected?.workflow;
    if(!recipe&&workflow){
      const values=workflowValues||workflowFields?.read();
      if(selected.settings.preset==='high_detail_native'&&values)return {
        resolution:parseInt(values['17'].pipeline_type,10)||workflow.resolution,
        texture_size:values['25'].texture_method==='ultratex' ? values['25'].ultratex_size :
          values['25'].bake_on_vertices ? 0 : (values['25'].texture_size||workflow.texture_size),
        triangles:values['22'].target_face_num||workflow.target_faces,
      };
      if(selected.settings.preset==='game_asset_native'&&values)return {
        resolution:Number(values['94'].target_resolution)||workflow.resolution,
        texture_size:values['377'].texture_size||workflow.texture_size,
        triangles:values['335'].target_tris||workflow.target_faces,
      };
      return {resolution:workflow.resolution,texture_size:workflow.texture_size,triangles:workflow.target_faces};
    }
    recipe??=read();
    const finishing=readFinishing();
    const result={resolution:usePixal()?1024:recipe.resolution,texture_size:recipe.texture_size};
    if(finishing!=='none'){
      result.triangles=detailRefinements[finishing]?Math.min(recipe.triangles,detailRefinementLimit):recipe.triangles;
    }
    return result;
  }
  function syncRefinementBudget(active){
    const triangles=$('triangles');
    if(active){
      if(!refinementInputs){
        const exceedsLimit=Number(triangles.value)>detailRefinementLimit;
        refinementInputs={triangles:exceedsLimit?triangles.value:null};
        if(exceedsLimit)triangles.value=String(detailRefinementLimit);
      }
    }else if(refinementInputs){
      if(refinementInputs.triangles!==null)triangles.value=refinementInputs.triangles;
      refinementInputs=null;
    }
    triangles.placeholder=active?number.format(detailRefinementLimit):'';
  }
  function profileRecipe(recipe=read()){
    const finishing=readFinishing();
    if(isReconstruction(finishing)&&(finishing!=='studio_768'||recipe.resolution>=1024)){
      recipe={...recipe,triangles:budget(recipe).triangles,master_finishing:finishing};
    }
    if(finishing==='none')recipe={...recipe,master_finishing:finishing};
    if(finishing==='hi3d_1024')recipe={...recipe,retexture_after_refinement:wantsRetexture()};
    const method=preferredTextureMethod==='ultratex'&&recipe.texture_size!==2048?'trellis':preferredTextureMethod;
    return {...recipe,texture_method:method};
  }
  function resolved(s){
    const copy=structuredClone(s);
    for(const key of ['structure_steps','shape_steps','texture_steps']){
      if(copy.advanced[key]===0)copy.advanced[key]=copy.preset==='figure_master'?figureSteps[key]:steps[copy.preset];
    }
    copy.master_finishing=copy.master_finishing||defaultFinishing(copy.preset);
    if(detailRefinements[copy.master_finishing])copy.triangles=Math.min(copy.triangles,detailRefinementLimit);
    copy.retexture_after_refinement=copy.retexture_after_refinement===true;
    copy.texture_method=textureMethodOf(copy);
    delete copy.multiview_texture_refinement;
    return copy;
  }
  const equal=(a,b)=>{const x=resolved(a),y=resolved(b);return Object.keys(x).every(k=>k==='advanced'?Object.keys(x[k]).every(f=>x[k][f]===y[k][f]):x[k]===y[k]);};
  const canSaveProfile=()=>selected&&(selected.workflow ? selected.workflow.custom_profiles===true : advanced.ready);
  function syncProfileActions(dirty){
    const workflow=selected.workflow;
    const unsupportedWorkflow=!!workflow&&!canSaveProfile();
    $('saveProfileAs').hidden=unsupportedWorkflow;
    $('saveProfileAs').disabled=!canSaveProfile();
    $('updateProfile').hidden=selected.builtin||unsupportedWorkflow;
    $('deleteProfile').hidden=selected.builtin||!selected.id;
    const startup=$('useProfileAtStartup');
    const isStartup=selected.id===startupProfile;
    startup.disabled=!selected.id||dirty||isStartup||Boolean(workflow&&workflow.available!==true);
    startup.setAttribute('aria-pressed',String(isStartup));
    startup.textContent=isStartup?'Startup profile':'Use at startup';
  }
  function syncFixedWorkflow(workflow){
    setWorkflow(workflow);
    for(const id of ['textureMethodChoice','generationSizeFields','generationMeshFields','keepFinishingSourceField']){
      $(id).hidden=!!workflow;
    }
    workflowBox.hidden=!workflow;
    $('resetAdvanced').textContent=workflow?'Reset workflow settings':'Reset profile settings';
    $('seed').disabled=!!workflow;
    if(!workflow)return;
    const values=workflowFields?.read();
    const method=values?.['25']?.texture_method || selected.settings.texture_method;
    const texture=method==='ultratex'?'UltraTex':'TRELLIS.2 PBR';
    const status=$('generationProfileStatus');
    const message=workflow.available===true ? selected.description : workflow.reason;
    status.classList.toggle('generation-method-notice',workflow.available!==true);
    if(status.textContent!==message)status.textContent=message;
    status.hidden=false;
    $('denseMasterRepairField').hidden=true;
    $('advancedMasterField').hidden=true;
    $('finishingSourceHelp').hidden=true;
    $('keepFinishingSource').disabled=true;
    $('textureSize').disabled=true;
    $('triangles').disabled=true;
    $('profileId').value=selected.id;
    const dirty=Object.entries(values||{}).some(([stage,fields])=>
      Object.entries(fields).some(([name,value])=>name!=='seed'&&value!==savedWorkflowValues?.[stage]?.[name]));
    $('profileState').textContent=dirty?'MODIFIED':'';
    $('profileState').hidden=!dirty;
    $('profileState').classList.toggle('dirty',dirty);
    const {resolution,texture_size,triangles}=budget(undefined,values);
    history.select({profile:selected, preset:selected.settings.preset, workflow_id:selected.settings.preset,
      engine:'studio_workflow',workflow_options:values});
    const meshSummary=triangles>0?`${number.format(triangles)} target triangles`:'Dense mesh';
    $('profileSummary').textContent=`${resolution}³ · ${meshSummary} · ${texture_size ? `${texture} ${texture_size/1024}K` : 'Vertex colours'}`;
    $('generationSummary').textContent=`${selected.name} · ${$('profileSummary').textContent}`;
    syncProfileActions(dirty);
  }
  function sync(){
    if(!selected)return;
    syncFixedWorkflow(selected.workflow);
    if(selected.workflow){changed();return;}
    const activePreset=$('preset').value;
    const experimental=activePreset==='humanoid_experimental', dense=activePreset==='dense_master';
    const figureUnsupported=activePreset==='figure_master'&&usePixal();
    $('generationProfileStatus').classList.add('generation-method-notice');
    $('generationProfileStatus').textContent=figureUnsupported?'Figure Master uses TRELLIS.2 · Single image. Choose that engine or another profile.':'';
    $('generationProfileStatus').hidden=!figureUnsupported;
    const finishing=$('denseMasterFinishing');
    const override=$('advancedMasterFinishing');
    if(!dense)override.value='';
    if(dense&&!finishing.value)finishing.value=defaultFinishing(activePreset);
    if(usePixal()&&detailRefinements[finishing.value]){
      finishing.value=standardFinishing||defaultFinishing(activePreset);
      pixalFallback=true;
    }else if(!usePixal()){
      pixalFallback=false;
    }
    const selectedFinish=readFinishing();
    const detail=detailRefinements[selectedFinish];
    const reconstruction=isReconstruction(selectedFinish);
    const fullMesh=selectedFinish==='none';
    const requiresSource=experimental||dense||reconstruction||fullMesh;
    if(requiresSource!==sourceRequired){
      if(requiresSource){previousKeep=$('keepFinishingSource').checked;$('keepFinishingSource').checked=true;}
      else $('keepFinishingSource').checked=previousKeep;
      sourceRequired=requiresSource;
    }
    $('keepFinishingSource').disabled=requiresSource;
    $('denseMasterRepairField').hidden=false;
    $('advancedMasterField').hidden=!dense;
    const detailGroup=advanced.group('hi3d');
    if(detailGroup)detailGroup.hidden=!detail;
    retextureField.hidden=selectedFinish!=='hi3d_1024';
    retexture.disabled=!retextureEligible();
    retextureStatus.textContent=retexture.disabled
      ? 'Choose 1024³ for the 3D resolution to use this option.'
      : detailTexturing.available!==true ? detailTexturing.reason||'The local texturing pipeline is unavailable.' : '';
    retextureStatus.hidden=!retextureStatus.textContent;
    const textureModeRestriction=method=>textureMethodRestriction(method,textureSupport,!isMultiview());
    for(const option of textureMethod.options){
      option.title=textureModeRestriction(option.value);
      option.disabled=!!option.title;
    }
    const textureFallback=textureModeRestriction(preferredTextureMethod);
    textureMethod.value=textureFallback?'trellis':preferredTextureMethod;
    textureMethod.disabled=false;
    textureMethodStatus.textContent=textureFallback?`${textureFallback} Using Native PBR for this generation.`:'';
    textureMethodStatus.hidden=!textureMethodStatus.textContent;
    syncRefinementBudget(Boolean(detail));
    const textureSize=$('textureSize');
    const ultraSelected=textureMethod.value==='ultratex';
    if(ultraSelected){
      if(ultraAtlas===null)ultraAtlas=textureSize.value;
      textureSize.value='2048';
    }else if(ultraAtlas!==null){
      textureSize.value=ultraAtlas;
      ultraAtlas=null;
    }
    textureSize.disabled=ultraSelected;
    if(usePixal()&&read().texture_size===4096){
      pixalAtlas=textureSize.value;
      textureSize.value='2048';
    }else if(!usePixal()&&pixalAtlas!==null){
      if(textureSize.value==='2048')textureSize.value=pixalAtlas;
      pixalAtlas=null;
    }
    const recipe=read();
    const actual = budget(recipe);
    const sourceOption=$('resolution').selectedOptions[0];
    const detailSourceSupported=detailSourceResolutions.includes(actual.resolution)&&sourceOption&&!sourceOption.disabled;
    const detailSourceReason=usePixal()?'Choose TRELLIS.2 · Single image.'
      : detailSourceSupported?'':'Choose a supported 3D resolution in Advanced.';
    finishing.hidden = !!override.value;
    finishing.disabled = finishing.hidden;
    override.disabled = !dense;
    $('masterFinishingOverride').hidden=!override.value;
    $('masterFinishingOverrideLabel').textContent=finishingName(override.value)||'';
    override.querySelector('[value="repair"]').disabled = !repairSupported;
    for (const option of finishing.options) {
      if(!option.value){
        option.disabled=option.hidden=dense;
        continue;
      }
      const info=masterSupport[option.value];
      if(option.value==='none'){
        option.disabled=info?.available!==true;
        option.title=option.disabled?info?.reason||'Requires server support for full mesh export.':'';
      }else if(detailRefinements[option.value]){
        option.disabled=!!detailSourceReason||info?.available!==true;
        option.title=option.disabled
          ? detailSourceReason||info?.reason||'Requires the Hi3D runtime.'
          : detailRefinements[option.value].detail;
      }else{
        option.disabled=actual.resolution<1024||!(info?.available??(dense&&option.value==='studio_768'&&hdSupported));
        option.textContent=finishingName(option.value)+(option.value==='wtivo_1024'&&option.disabled?' · Unavailable':'');
        option.title=option.disabled ? info?.reason || 'Requires 1024 or higher generation resolution.' : '';
      }
    }
    const selectedSupport=masterSupport[selectedFinish];
    const ceiling=detail?detailRefinementLimit:inlineFinishingLimit;
    const requestedTriangles=detail?Number($('triangles').value):recipe.triangles;
    $('monoTrianglesField').hidden=fullMesh;
    $('triangles').disabled=fullMesh;
    $('triangles').max=String(reconstruction?ceiling:8000000);
    let hint=detail?.detail||finishingDetails[selectedFinish]||'';
    if (fullMesh&&finishing.selectedOptions[0]?.disabled) {
      hint=finishing.selectedOptions[0].title;
    } else if (reconstruction&&finishing.selectedOptions[0]?.disabled) {
      hint=detail ? finishing.selectedOptions[0].title
        : actual.resolution<1024
        ? 'Choose 1024 or higher generation resolution in Advanced.'
        : selectedSupport?.reason || 'This reconstruction is unavailable.';
    } else if (reconstruction&&requestedTriangles>ceiling) {
      hint=`Set the triangle ceiling to ${number.format(ceiling)} or less in Advanced.`;
    } else if(detail&&!$('removeBg').checked){
      hint+=' Use a transparent reference or enable background removal.';
    }
    if(wantsRetexture()){
      hint+=' '+(retextureStatus.textContent||'A separate texture pass follows refinement.');
    }
    if(pixalFallback)hint+=' Hi3D requires TRELLIS.2 · Single image. This generation will use the finishing shown above.';
    if(pixalAtlas!==null&&textureSize.value==='2048')hint+=' Pixal3D uses a 2K atlas for this recipe.';
    $('denseMasterFinishingHint').textContent=hint;
    $('detailRefinementSource').hidden=!detail;
    if(detail){
      $('detailRefinementNativeResolution').textContent=`${actual.resolution}³`;
      $('detailRefinementPassResolution').textContent=`${detail.resolution}³`;
    }
    const detailOptions=Object.keys(detailRefinements).map(value=>finishing.querySelector(`[value="${value}"]`));
    const unavailable=$('detailRefinementUnavailable');
    unavailable.hidden=!!detail||finishing.hidden||!masterSupport.hi3d_512||!detailOptions.every(option=>option.disabled);
    unavailable.textContent=unavailable.hidden?'':detailOptions[0].title;
    $('finishingSourceHelp').hidden=requiresSource;
    const dirty=Boolean(override.value&&!selected.variant)||!equal(profileRecipe(recipe),selected.settings);
    const purpose=selected.variant?'':profilePurposes[recipe.preset]||selected.description||'';
    $('profilePurpose').textContent=[purpose, $('keepFinishingSource').checked?'Keeps the source for later finishing.':''].filter(Boolean).join(' ');
    $('profilePurpose').hidden=!$('profilePurpose').textContent;
    const effective=resolved(profileRecipe(recipe));
    history.select(!selected.variant && !isMultiview() ? {...effective, ...actual, profile:selected,
      engine:usePixal()?'pixal3d':'trellis2', generation_options:effective.advanced,
      keep_finishing_source:$('keepFinishingSource').checked, repair_surface:override.value==='repair',
      hd_reconstruction:selectedFinish==='studio_768'&&!masterSupport.studio_768?.available} : null);
    $('profileId').value=recipe.preset===selected.settings.preset?selected.id||selected.sourceProfile||'':'';
    $('profileState').textContent=dirty?'MODIFIED':selected.variant?'VARIANT':selected.experimental?'EXPERIMENTAL':'';
    $('profileState').hidden=!$('profileState').textContent;
    $('profileState').classList.toggle('dirty',dirty);
    const meshSummary=fullMesh?'Full mesh':`${dense||reconstruction?'Up to ':''}${number.format(actual.triangles)} triangles`;
    $('profileSummary').textContent=`${actual.resolution}³ · ${meshSummary} · ${actual.texture_size/1024}K textures`;
    $('monoTrianglesLabel').textContent=dense||reconstruction?'Maximum triangles':'Target triangles';
    $('monoTrianglesHint').textContent=detail?'0 uses the profile ceiling, capped at 1,000,000.'
      : reconstruction?'Up to 3,000,000. 0 uses the profile ceiling.'
      : dense?'0 uses the profile ceiling.':'0 uses the profile target.';
    const caption=selected.variant?'Model settings':profileNames[selected.id]||selected.name;
    $('generationSummary').textContent=[caption,describeMethod(),detail?`${actual.resolution}³ source`:'',finishingName(selectedFinish),textureMethod.selectedOptions[0]?.textContent].filter(Boolean).join(' · ');
    syncProfileActions(dirty);
    const profileBase=$('profileBase');
    for(const option of profileBase.querySelectorAll('[data-historical]'))if(option.value!==recipe.preset)option.remove();
    if(![...profileBase.options].some(option=>option.value===recipe.preset)){
      const option=document.createElement('option');
      option.value=recipe.preset;
      option.textContent='Historical settings';
      option.dataset.historical='true';
      profileBase.append(option);
    }
    profileBase.value=recipe.preset;
    changed();
    if(advanced.ready&&['figure_master','high_detail'].includes(activePreset)){
      const title=selected.variant?'This model’s settings':selected.name;
      const key=`${activePreset}:${title}`;
      if(key!==standardGraphKey){
        removeStandardGraph();
        standardGraph=createStandardWorkflowGraph($('workspace'),title,activePreset,advanced.fields);
        standardGraphKey=key;
        $('assetHint').after(standardGraph.opener);
      }
      standardGraph.sync(usePixal()?'pixal3d':'trellis2',textureMethod.value);
    }else removeStandardGraph();
  }
  function apply(id){
    const profile=profiles.find(p=>p.id===id);if(!profile)return;
    const sameWorkflow=profile.workflow&&selected?.workflow&&profile.settings.preset===selected.settings.preset;
    const currentValues=sameWorkflow?workflowFields?.read():null;
    selected=profile;
    $('profileChoice').querySelector('[data-variant]')?.remove();
    const s=selected.settings;
    $('profilePurpose').textContent=profilePurposes[s.preset]||selected.description||'';
    $('profilePurpose').hidden=!$('profilePurpose').textContent;
    if(selected.workflow){
      const values={...s.workflow_options};
      for(const [stage,fields] of Object.entries(currentValues||{})){
        if(fields.seed!==undefined)values[stage]={...values[stage],seed:fields.seed};
      }
      showWorkflowFields(selected,values);
      savedWorkflowValues=workflowFields.read();
      $('preset').value=s.preset;
      $('profileChoice').value=id;
      $('profileNotice').textContent='';
      sync();
      return;
    }
    workflowFields?.closeGraph();
    workflowFields=savedWorkflowValues=null;
    workflowBox.replaceChildren();
    refinementInputs=null;
    pixalFallback=false;
    pixalAtlas=null;
    ultraAtlas=null;
    standardFinishing=detailRefinements[s.master_finishing]?defaultFinishing(s.preset):s.master_finishing||defaultFinishing(s.preset);
    $('denseMasterFinishing').value=s.master_finishing||standardFinishing;
    $('advancedMasterFinishing').value='';
    retexture.checked=s.retexture_after_refinement===true;
    preferredTextureMethod=textureMethodOf(s);
    sourceRequired=false;
    $('keepFinishingSource').checked=selected.keep_finishing_source===true;
    for(const [key,value] of Object.entries({preset:s.preset,resolution:s.resolution,triangles:s.triangles,textureSize:s.texture_size}))$(key).value=String(value);
    const textureSeed=advanced.read().texture_seed;
    $('removeBg').checked=s.remove_bg;advanced.apply({...s.advanced,texture_seed:textureSeed});
    $('profileChoice').value=id;$('profileId').value=id;
    if(s.preset==='humanoid_experimental')$('assetType').value='humanoid';
    if(!selected.workflow&&(detailRefinements[s.master_finishing]||s.preset==='figure_master')){setWorkflow(null);useNativeMethod();}
    $('profileNotice').textContent='';sync();
  }
  async function request(url,options){const r=await fetch(url,options),data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Profiles unavailable.');return data;}
  const selectableProfiles=()=>profiles.filter(profile=>!profile.retired);
  const availableProfile=(...ids)=>{
    const selectable=selectableProfiles();
    return ids.find(id=>selectable.some(profile=>
      profile.id===id&&(!profile.workflow||profile.workflow.available===true)));
  };
  function loadCatalogue(data){
    profiles=data.profiles;
    serverStartup=availableProfile(data.startup_profile,data.default_profile,...selectableProfiles().map(profile=>profile.id));
  }
  function options(){
    $('profileChoice').replaceChildren();
    for(const builtin of [true,false]){
      const group=document.createElement('optgroup');group.label=builtin?'Built-in profiles':'My profiles';
      for(const p of selectableProfiles().filter(x=>x.builtin===builtin)){
        const name=(p.builtin&&profileNames[p.id])||p.name;
        const count=p.workflow?.target_faces??p.settings.triangles;
        const target=p.settings.master_finishing==='none'?'Full mesh':count>0
          ? new Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:2}).format(count)+' tris':'Dense mesh';
        const option=new Option(name+' · '+target,p.id);
        option.title=profilePurposes[p.settings.preset]||p.description||name;
        group.append(option);
      }
      if(group.children.length)$('profileChoice').append(group);
    }
  }
  try{
    loadCatalogue(await request('/api/profiles'));
    let saved='';
    try{saved=localStorage.getItem(startupKey);}catch{}
    startupProfile=availableProfile(saved,serverStartup);
    options();apply(startupProfile);
    $('profileChoice').disabled=false;
  }catch(e){$('profileNotice').textContent=e.message+' Restart TRELLIS if needed.';}
  $('profileChoice').onchange=()=>apply($('profileChoice').value);
  $('useProfileAtStartup').onclick=()=>{
    try{
      localStorage.setItem(startupKey,selected.id);
      startupProfile=selected.id;
      $('profileNotice').textContent='Startup profile saved for this browser.';
      sync();
    }catch{
      $('profileNotice').textContent='This browser could not save the startup profile.';
    }
  };
  $('form').addEventListener('input',sync);$('form').addEventListener('change',sync);
  textureMethod.addEventListener('input',()=>{preferredTextureMethod=textureMethod.value;});
  textureMethod.addEventListener('change',()=>{preferredTextureMethod=textureMethod.value;});
  $('denseMasterFinishing').addEventListener('change',()=>{
    const method=$('denseMasterFinishing').value;
    pixalFallback=false;
    if(!detailRefinements[method])standardFinishing=method;
    $('advancedMasterFinishing').value='';
    sync();
  });
  $('clearMasterOverride').addEventListener('click',()=>{
    $('advancedMasterFinishing').value='';
    sync();
    $('denseMasterFinishing').focus();
  });
  $('profileBase').addEventListener('change',()=>{$('preset').value=$('profileBase').value;sync();});
  function show(update){
    if(!canSaveProfile()||update&&selected.builtin)return;
    if(selected.workflow&&!workflowFields?.validate())return;
    edit=update;
    $('profileName').value=update?selected.name:'';
    $('profileDialogTitle').textContent=update?'Edit this profile':'Save a copy';
    $('profileDialogError').textContent='';
    $('profileDialog').showModal();$('profileName').focus();
  }
  $('saveProfileAs').onclick=()=>show(false);$('updateProfile').onclick=()=>show(true);
  $('closeProfile').onclick=()=>$('profileDialog').close();
  const deleteDialog=$('deleteProfileDialog');
  $('deleteProfile').onclick=()=>{
    if(!selected?.id||selected.builtin)return;
    deletingProfile=selected;
    $('deleteProfileName').textContent=selected.name;
    $('deleteProfileError').textContent='';
    deleteDialog.showModal();
  };
  deleteDialog.addEventListener('cancel',event=>{if(deleting)event.preventDefault();});
  $('confirmDeleteProfile').onclick=async()=>{
    if(!deletingProfile||deleting)return;
    const {id}=deletingProfile;
    deleting=true;
    $('deleteProfileError').textContent='';
    $('confirmDeleteProfile').disabled=$('cancelDeleteProfile').disabled=true;
    try{
      await request(`/api/profiles/${encodeURIComponent(id)}`,{method:'DELETE'});
      profiles=profiles.filter(profile=>profile.id!==id);
      try{if(localStorage.getItem(startupKey)===id)localStorage.removeItem(startupKey);}catch{}
      let notice='Profile deleted.';
      try{loadCatalogue(await request('/api/profiles'));}
      catch{notice='Profile deleted. Reload Studio to refresh the profile list.';}
      startupProfile=availableProfile(startupProfile,serverStartup);
      options();
      apply(startupProfile);
      deleteDialog.close();
      $('profileChoice').focus();
      $('profileNotice').textContent=notice;
    }catch(error){
      $('deleteProfileError').textContent=error.message;
    }finally{
      deleting=false;
      $('confirmDeleteProfile').disabled=$('cancelDeleteProfile').disabled=false;
    }
  };
  $('profileForm').onsubmit=async e=>{
    e.preventDefault();if(saving||!canSaveProfile()||edit&&selected.builtin)return;
    const valid=selected.workflow ? workflowFields?.validate()
      : [...$('form').querySelectorAll('input[type=number]')].every(input=>input.checkValidity());
    if(!valid){$('profileDialogError').textContent='Correct the generation settings before saving the profile.';return;}
    const settings=selected.workflow
      ? {preset:selected.settings.preset,workflow_options:workflowFields.read()}
      : profileRecipe();
    saving=true;$('confirmProfile').disabled=true;
    try{
      const profile=await request(edit?`/api/profiles/${encodeURIComponent(selected.id)}`:'/api/profiles',{
        method:edit?'PUT':'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:$('profileName').value,settings})});
      loadCatalogue(await request('/api/profiles'));options();apply(profile.id);
      $('profileDialog').close();$('profileNotice').textContent='Profile saved on this computer.';
    }catch(e){$('profileDialogError').textContent=e.message;}
    finally{saving=false;$('confirmProfile').disabled=false;}
  };
  return {sync,updateHistory:history.update,reset:()=>apply(selected?.id||startupProfile),
    setFinishingCapabilities(available, hdAvailable, methods, texturing, textureMethods){
      const supported=methods||{};
      const texturingSupport=texturing||{};
      if(repairSupported===available&&hdSupported===hdAvailable&&JSON.stringify(masterSupport)===JSON.stringify(supported)
        &&JSON.stringify(detailTexturing)===JSON.stringify(texturingSupport)
        &&JSON.stringify(textureSupport)===JSON.stringify(textureMethods))return;
      repairSupported=available;
      hdSupported=hdAvailable;
      masterSupport=supported;
      detailTexturing=texturingSupport;
      textureSupport=textureMethods;
      sync();
    },
    appendFinishing(form){
      const value=readFinishing();
      if (value==='repair') form.set('repair_surface','true');
      if (value==='none'||detailRefinements[value]||value==='wtivo_1024'||(value==='studio_768'&&masterSupport.studio_768?.available)) {
        form.set('master_finishing',value);
      } else if (value==='studio_768') form.set('hd_reconstruction','true');
      if(!usePixal()&&(value==='hi3d_1024'||selected?.settings.retexture_after_refinement)){
        form.set('retexture_after_refinement', String(wantsRetexture()));
      }
      form.set('texture_method',textureMethod.value);
    },
    restoreFinishing(settings){
      refinementInputs=null;
      pixalFallback=false;
      pixalAtlas=null;
      ultraAtlas=null;
      const method=settings.master_finishing||(settings.hd_reconstruction?'studio_768':defaultFinishing(settings.preset));
      standardFinishing=detailRefinements[method]?defaultFinishing(settings.preset):method;
      $('denseMasterFinishing').value=method;
      $('advancedMasterFinishing').value=settings.preset!=='dense_master'||settings.master_finishing||settings.hd_reconstruction
        ? '' : settings.repair_surface ? 'repair' : 'direct';
      retexture.checked=settings.retexture_after_refinement===true;
      preferredTextureMethod=textureMethodOf(settings);
    },
    budget,
    workflowStages: id => profiles.find(profile => profile.id === id)?.workflow?.advanced,
    get workflow(){return selected?.workflow;},
    appendWorkflow(form){
      if(!workflowFields?.validate())return false;
      form.set('workflow_options',JSON.stringify(workflowFields.read()));
      workflowFields.closeGraph();
      return true;
    },
    closeWorkflowGraph(){workflowFields?.closeGraph();standardGraph?.close();},
    restoreWorkflow(settings){
      const previous=settings.profile?.id||settings.workflow_id;
      const base=restoredWorkflows[settings.workflow_id]||settings.workflow_id;
      const candidates=[restoredWorkflows[previous]||previous,base];
      const profile=candidates.map(id=>profiles.find(candidate=>candidate.id===id&&candidate.workflow))
        .find(candidate=>candidate&&(!base||candidate.settings.preset===base));
      if(!profile)return false;
      apply(profile.id);
      if(settings.workflow_options){
        showWorkflowFields(selected,settings.workflow_options);
        sync();
      }
      return true;
    },
    get finishingReady(){
      if(usePixal()&&$('preset').value==='figure_master')return false;
      const value=readFinishing();
      if (value==='repair') return repairSupported;
      if (!value||value==='direct') return true;
      if (value==='none') return masterSupport.none?.available===true;
      const withinLimit=detailRefinements[value]?Number($('triangles').value)<=detailRefinementLimit
        : budget().triangles<=inlineFinishingLimit;
      return !$('denseMasterFinishing').selectedOptions[0]?.disabled&&withinLimit
        &&(!wantsRetexture()||detailTexturing.available===true);
    },
    restoreSource(value){sourceRequired=false;previousKeep=!!value;$('keepFinishingSource').checked=!!value;sync();},
    detach(sourceProfile){
      const matchingProfile=profiles.find(profile=>profile.id===sourceProfile&&profile.settings.preset===$('preset').value);
      selected={id:'',name:'Variant',builtin:true,variant:true,settings:profileRecipe(),sourceProfile:matchingProfile?.id};
      $('profileChoice').querySelector('[data-variant]')?.remove();
      const option=document.createElement('option');option.value='';option.textContent='This model’s settings';option.disabled=true;option.dataset.variant='true';$('profileChoice').append(option);$('profileChoice').value='';
      sync();$('profileNotice').textContent='Model settings restored. Save a copy to create a profile.';
    },
    get ready(){return profiles.length>0;}};
}
