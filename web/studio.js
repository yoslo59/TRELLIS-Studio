import {createStudioWorkbench} from './studio-workbench.js?v=3.3.5';
import {createViewerLighting} from './viewer-lighting.js?v=3.3.5';
import {createStudioTools} from './studio-tools.js?v=3.3.5';
import {createAssetDetails,assetTypeName} from './asset-details.js?v=3.3.5';
import {createPrintCopy} from './print-copy.js?v=3.3.5';
import {createAnimationStudio} from './animation-studio.js?v=3.3.5';
import {createRigSetupViewer} from './rig-setup-viewer.js?v=3.3.5';
import {createModelImport} from './import-model.js?v=3.3.5';
import {createMultiviewInputs} from './multiview-inputs.js?v=3.3.5';
import {createReferencePreview} from './reference-preview.js?v=3.3.5';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { createAdvancedControls } from './advanced-controls.js?v=3.3.5';
import { createProfileControls } from './profiles.js?v=3.3.5';
import {createCardMenu,updateCardStatus,icon} from './card-menu.js?v=3.3.5';
import {createCardTurntables} from './card-turntable.js?v=3.3.5';
const $=id=>document.getElementById(id);
const cardTurntables=createCardTurntables($('libraryGrid'));
let cardTurntablesAvailable=false;
const cardMenu=createCardMenu({version:reuseJob,remove:requestDelete,details:openDetails,print:j=>printCopy.open(j),compare:j=>studioTools.compare.open(j),diagnostics:j=>studioTools.diagnostics.open(j),export:j=>studioTools.exports.open(j),history:j=>workbench.history.open(j),folder:j=>workbench.workspace.move(j),lod:j=>workbench.prepare.open(j,'lod'),game:j=>workbench.prepare.open(j,'game'),refinish:openRefinish,finishingBoard:j=>workbench.finishingBoard.open(j)});
const labels={done:'Completed',running:'Generating',error:'Failed',cancelled:'Cancelled',queued:'Queued'},reviews={review:'To review',retouch:'Needs changes',retained:'Retained',rejected:'Rejected'};
let studioTools,viewerLighting,workbench,rigSetupViewer;
let selectedId=null,loadedId=null,loadingId=null,activeId=null,records=[],busy=false,ready=false,shutDown=false,features=false;
let page=0,trashMode=false,trashRecords=[],metadataId=null,inspectId=null,inspectVersion=0,textureOpen=false,refreshing=false;
let submittingGeneration=false,restoringReferences=false;
let boneDots=null;const boneDotMatrix=new THREE.Matrix4(),boneDotPosition=new THREE.Vector3();
let loadedRevision=null;
let animationStudio,animationMode=false,pendingSelection=null,mixer=null,clips=[],clipAction=null,clipPlaying=true,skeletonHelper=null;
const clock=new THREE.Clock();
let renderer,scene,camera,controls,model,grid,profileControls,rotating=false,wire=false,loadVersion=0;
const categoryName=value=>({Mobilier:'Furniture',Industriel:'Industrial','Mobilier urbain':'Street furniture',Autre:'Other'}[value]||value);
const viewport=$('viewport'),nameOf=j=>j.metadata?.display_name||j.settings?.name||'Untitled',jobOf=()=>records.find(j=>j.id===selectedId);
const formatTriangles=count=>Number.isSafeInteger(count)&&count>0?`${count.toLocaleString('en-US')} triangles`:'Count unavailable';
const detailControls=await createAssetDetails();
const printCopy=createPrintCopy({api,startJob:startStudioJob});
studioTools=createStudioTools({api,getRecords:()=>records,getJob:jobOf,refresh:async()=>{metadataId=null;await refresh();},printCopy,labelAnimation:name=>animationStudio?.label(name)||name,openPreparation:(job,action)=>workbench.prepare.open(job,action)});
const advancedControls=await createAdvancedControls($('advancedControls'));
const multiview=createMultiviewInputs({
  changed({role,file,mode,profile}){
    if(role||mode)referencePreview.discard(role);
    if(role==='front'){
      $('parentId').value='';
      if(file&&$('name').value==='New model')$('name').value=file.name.replace(/\.[^.]+$/,'').slice(0,80);
    }
    $('notice').textContent='';
    if(mode||profile)profileControls?.sync();
    updateButtons();
  },
  invalid:message=>errorAt('notice',message),
});
const referencePreview=createReferencePreview({api,multiview,removeBackground:$('removeBg')});
$('removeBg').addEventListener('change',()=>{referencePreview.discard();updateButtons();});
function applyDefaultGeneration(){const d=advancedControls.defaults;if(!d)return;for(const[id,value]of Object.entries({preset:d.preset,resolution:d.resolution,triangles:d.triangles,textureSize:d.texture_size}))$(id).value=String(value);}
applyDefaultGeneration();
const settingsDownload=document.createElement('a');settingsDownload.className='download-settings';settingsDownload.textContent='Download settings JSON ↓';settingsDownload.download='trellis-reglages.json';settingsDownload.hidden=true;$('assetDetails').after(settingsDownload);
$('resetAdvanced').disabled=!advancedControls.ready;
$('resetAdvanced').onclick=()=>{profileControls?.reset();explainCost();$('notice').textContent='Game - Default restored. This model’s seed and height are preserved.';};
const resolutionOf=j=>Number(j.result?.resolution||j.settings?.resolution||512);
let availableResolutions=[512],availableTextures=[1024,2048];
function syncVersionNotice(status) {
  const uiRevision = document.querySelector('meta[name="trellis-ui-revision"]')?.content;
  const outdated = uiRevision && status.ui_revision && status.ui_revision !== uiRevision;
  const olderServer = outdated && String(status.ui_revision).localeCompare(uiRevision, 'en', {numeric:true}) < 0;
  const message = !features || olderServer
    ? 'Restart TRELLIS Studio to enable the updated server features.'
    : 'The Studio interface has been updated.';
  const text = $('versionNoticeText');
  if (text.textContent !== message) text.textContent = message;
  $('reloadStudio').hidden = !features || !outdated || olderServer;
  $('versionNotice').hidden = features && !outdated;
}
function syncCapabilities(status){multiview.setCapabilities(status.pixal3d);profileControls?.setRepairCapability(status.features?.includes('repair_surface')===true);availableResolutions=status.supported_resolutions||[512];availableTextures=status.texture_sizes||[1024,2048];for(const option of $('resolution').options)option.disabled=!availableResolutions.includes(Number(option.value));for(const option of $('textureSize').options)option.disabled=Number(option.value)!==0&&!availableTextures.includes(Number(option.value));explainCost();}
function explainCost(){const hint=$('qualityHint');if(!hint)return;const notes=[];if(Number($('resolution').value)===1536)notes.push('1536³ requires more processing time and memory.');if(Number($('textureSize').value)===4096)notes.push('4K uses larger textures; reserve it for close-up objects and large surfaces.');hint.textContent=notes.join(' ');hint.hidden=!notes.length;}
$('resolution').addEventListener('change',explainCost);$('textureSize').addEventListener('change',explainCost);
profileControls=await createProfileControls(advancedControls,{changed:()=>{explainCost();updateButtons();}});

function node(tag,className,value){const el=document.createElement(tag);if(className)el.className=className;if(value!==undefined)el.textContent=value;return el;}
async function api(url,options){const r=await fetch(url,options),v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'This operation is unavailable.');return v;}
function errorAt(id,e){$(id).textContent=e.message||String(e);}
function duration(s){if(!s)return '—';const m=Math.floor(s/60);s=Math.floor(s%60);return m?`${m} min ${String(s).padStart(2,'0')} s`:`${s} s`;}
try{
  renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;viewport.prepend(renderer.domElement);
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(40,1,.001,1000);camera.position.set(2,1.6,2.5);controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.target.set(0,.5,0);
  const pmrem=new THREE.PMREMGenerator(renderer),room=new RoomEnvironment();scene.environment=pmrem.fromScene(room,.04).texture;room.dispose();pmrem.dispose();scene.add(new THREE.HemisphereLight(0xffffff,0x48545d,.9));
  grid=new THREE.GridHelper(10,20,0x7d929a,0x596970);grid.material.transparent=true;grid.material.opacity=.22;scene.add(grid);grid.visible=false;
  viewerLighting=createViewerLighting({scene,renderer,getModel:()=>model});
  rigSetupViewer=createRigSetupViewer({scene,camera,controls,viewport,getModel:()=>model,stopRotation(){rotating=false;$('rotate').setAttribute('aria-pressed','false');}});
  new ResizeObserver(()=>{const w=Math.max(1,viewport.clientWidth),h=Math.max(1,viewport.clientHeight);renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();}).observe(viewport);
  renderer.setAnimationLoop(()=>{if(document.hidden||textureOpen||document.getElementById('compareDialog')?.open||document.body.classList.contains('library-mode'))return;if(rotating&&model)model.rotation.y+=.004;if(mixer&&clipPlaying){mixer.update(Math.min(clock.getDelta(),.1));if(clipAction){$('clipTime').value=clipAction.time;$('clipTimeValue').value=clipAction.time.toFixed(1)+' s';}}else clock.getDelta();if(skeletonHelper&&boneDots){boneDots.visible=skeletonHelper.visible;model?.updateMatrixWorld(true);skeletonHelper.bones.forEach((bone,index)=>{boneDotPosition.setFromMatrixPosition(bone.matrixWorld);boneDotMatrix.makeTranslation(boneDotPosition.x,boneDotPosition.y,boneDotPosition.z);boneDots.setMatrixAt(index,boneDotMatrix);});boneDots.instanceMatrix.needsUpdate=true;}controls.update();rigSetupViewer?.update();renderer.render(scene,camera);});
}catch(e){$('viewerError').hidden=false;errorAt('viewerError',e);}
function disposeModel(root){if(!root)return;const mats=new Set(),textures=new Set();root.traverse(x=>{x.geometry?.dispose();for(const m of [...(Array.isArray(x.material)?x.material:[x.material]),...(x.userData.baseMaterials||[])]){if(!m)continue;mats.add(m);for(const v of Object.values(m))if(v?.isTexture)textures.add(v);}});textures.forEach(x=>x.dispose());mats.forEach(x=>x.dispose());}
function clearModel(){animationStudio?.closeRig();rigSetupViewer?.close();viewerLighting?.clear();if(boneDots){scene.remove(boneDots);boneDots.geometry.dispose();boneDots.material.dispose();boneDots=null;}if(predictedSkeleton){scene.remove(predictedSkeleton);predictedSkeleton.geometry.dispose();predictedSkeleton.material.dispose();predictedSkeleton=null;predictedId=null;}mixer?.stopAllAction();mixer=null;clips=[];clipAction=null;$('animationPlayer').hidden=true;if(skeletonHelper){scene.remove(skeletonHelper);skeletonHelper.geometry.dispose();skeletonHelper.material.dispose();skeletonHelper=null;}if(model){scene.remove(model);disposeModel(model);}model=null;loadedId=null;loadingId=null;}
function fit(angle){if(!model||!camera)return;model.updateMatrixWorld(true);model.traverse(x=>{if(x.isSkinnedMesh){x.skeleton.update();x.computeBoundingBox();}});const box=new THREE.Box3().setFromObject(model,true),size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3()),d=Math.max(size.x,size.y,size.z,.01)*1.8,views={front:[0,.05,1],back:[0,.05,-1],left:[-1,.05,0],right:[1,.05,0],top:[0,1,0]};camera.up.set(0,angle==='top'?0:1,angle==='top'?-1:0);camera.position.copy(center).add(new THREE.Vector3(...(views[angle]||[.8,.55,1])).multiplyScalar(d));camera.near=Math.max(.001,d/1000);camera.far=Math.max(100,d*100);camera.updateProjectionMatrix();controls.target.copy(center);controls.minDistance=d*.1;controls.maxDistance=d*8;controls.update();viewerLighting?.fit();}
function materials(){
 const mode=$('materialMode').value;
 model?.traverse(mesh=>{
  if(!mesh.isMesh)return;
  const base=mesh.userData.baseMaterials;
  const current=Array.isArray(mesh.material)?mesh.material:[mesh.material];
  for(const material of current)if(!base.includes(material))material.dispose();
  const chosen=mode==='pbr'?base:base.map(({side})=>{
   if(mode==='clay')return new THREE.MeshStandardMaterial({color:0xa8b4b5,roughness:.7,metalness:0,side});
   return new THREE.MeshNormalMaterial({side});
  });
  chosen.forEach(material=>material.wireframe=wire);
  mesh.material=mesh.userData.wasArray?chosen:chosen[0];
 });
}
async function loadModel(id){const current=records.find(j=>j.id===id),rigPending=current?.settings.asset_type==='rigged'&&current?.settings.source_kind==='unirig'&&['queued','running'].includes(current?.state),revision=rigPending?'preview-'+(current.progress>=8):current?.result?.revision||0;if(!renderer||(loadedId===id&&loadedRevision===revision)||loadingId===id)return;const version=++loadVersion;loadingId=id;try{const gltf=await new GLTFLoader().loadAsync(rigPending?`/api/jobs/${id}/rig-preview-model?v=${revision}`:`/api/jobs/${id}/files/model.glb?v=${revision}`);if(version!==loadVersion||selectedId!==id){disposeModel(gltf.scene);return;}clearModel();model=gltf.scene;loadedId=id;loadedRevision=revision;model.traverse(x=>{if(x.isMesh){x.userData.wasArray=Array.isArray(x.material);x.userData.baseMaterials=x.userData.wasArray?[...x.material]:[x.material];}});scene.add(model);setupClips(gltf);materials();$('empty').hidden=true;$('viewerError').hidden=true;fit();}catch(e){if(version===loadVersion){$('viewerError').hidden=false;errorAt('viewerError','Unable to load model: '+e.message);}}finally{if(loadingId===id)loadingId=null;}}
$('gridToggle').onclick=()=>{if(!grid)return;grid.visible=!grid.visible;$('gridToggle').setAttribute('aria-pressed',grid.visible);};
$('fit').onclick=()=>fit();$('rotate').onclick=()=>{if(rigSetupViewer?.active)return;rotating=!rotating;$('rotate').setAttribute('aria-pressed',rotating);};$('wireframe').onclick=()=>{wire=!wire;$('wireframe').setAttribute('aria-pressed',wire);materials();};$('materialMode').onchange=materials;
document.querySelectorAll('[data-angle]').forEach(b=>b.onclick=()=>{if(rigSetupViewer?.active){rigSetupViewer.frame();return;}rotating=false;$('rotate').setAttribute('aria-pressed','false');if(model)model.rotation.y=0;fit(b.dataset.angle);});
function downloads(j){
 const done=j?.state==='done',terrain=j?.settings.asset_type==='terrain_unity';
 for(const[id,file,enabled]of[['downloadBlender','Blender.zip',features],['downloadGlb','model.glb',true],['downloadUnity','Unity.zip',true]]){
  const el=$(id),ok=done&&enabled;
  el.classList.toggle('disabled',!ok);el.setAttribute('aria-disabled',!ok);
  if(ok)el.href=id==='downloadUnity'&&!terrain?'#exportDialog':`/api/jobs/${j.id}/files/${file}`;else el.removeAttribute('href');
 }
 const unityLabel=terrain?'Download native Unity terrain':'Export to Unity';
 $('downloadUnity').title=unityLabel;$('downloadUnity').setAttribute('aria-label',unityLabel);
 const printable=done&&j.settings.asset_type==='print';
 $('downloadStl').hidden=!printable;
 if(printable)$('downloadStl').href=`/api/jobs/${j.id}/files/model.stl`;else $('downloadStl').removeAttribute('href');
 $('downloadHeight').hidden=!(terrain&&done);
 if(terrain)$('downloadHeight').href=`/api/jobs/${j.id}/files/heightmap.png`;
}
$('downloadUnity').onclick=e=>{
 const j=jobOf();
 if(j?.state!=='done'){e.preventDefault();return;}
 if(j.settings.asset_type!=='terrain_unity'){
  e.preventDefault();studioTools.exports.open(j,'unity');
 }
};
function fillMetadata(j){if(metadataId===j?.id)return;metadataId=j?.id||null;const m=j?.metadata||{};$('displayName').value=j?nameOf(j):'';detailControls.setValue('category',categoryName(m.category)||'');detailControls.setValue('detailAssetType',m.asset_type||j?.settings.asset_type||'object');$('reviewState').value=m.review_state||'review';$('tags').value=(m.tags||[]).join(', ');$('notes').value=m.notes||'';$('metadataNotice').textContent='';}
function renderSelected(){const j=jobOf();studioTools?.update(j,records,trashMode);workbench?.update(j);detailControls.update(j);animationStudio?.update();$('animateModel').disabled=!j||j.state!=='done';downloads(j);fillMetadata(j);settingsDownload.hidden=!j;if(j)settingsDownload.href=`/api/jobs/${j.id}/files/settings.json`;$('favorite').disabled=!j||!features;$('favorite').textContent=j?.metadata?.favorite?'★':'☆';$('favorite').setAttribute('aria-pressed',!!j?.metadata?.favorite);$('saveMetadata').disabled=!j||!features;$('viewTextures').disabled=!j||j.state!=='done'||!features;
  if(!j){$('modelTitle').textContent='Your next model';$('stats').textContent='';$('sourceThumb').hidden=true;$('assetDetails').replaceChildren(node('dt','','Model'),node('dd','','No model selected'));$('textureSummary').textContent='Select a completed model.';$('textureGallery').replaceChildren();if(model||loadingId){++loadVersion;clearModel();}$('empty').hidden=false;return;}
  $('modelTitle').textContent=nameOf(j);const terrain=j.settings.asset_type==='terrain_unity',external=['imported','rigged','print'].includes(j.settings.asset_type),pixal=j.settings.engine==='pixal3d';$('engineLabel').textContent=external?(j.settings.asset_type==='print'?'3D PRINT COPY':j.settings.asset_type==='rigged'?'RIG & ANIMATION':'IMPORTED MODEL'):terrain?'PROCEDURAL TERRAIN':pixal?`PIXAL3D · ${j.settings.views?.length||0} VIEWS`:`TRELLIS.2 · ${resolutionOf(j)}³`;$('downloadGlb').title=j.settings.asset_type==='print'?'Download print preview GLB in metres':terrain?'Download terrain GLB':'Download PBR GLB with all animations';$('sourceThumb').hidden=terrain||external;if(!terrain&&!external&&$('sourcePreview').dataset.id!==j.id){$('sourcePreview').src=`/api/jobs/${j.id}/files/input.png`;$('sourcePreview').dataset.id=j.id;}
  $('exportHint').textContent=j.settings.asset_type==='print'?'The STL uses millimetres. The Blender package includes the static source and the topology report.':terrain?'The Blender package contains the terrain mesh; the Unity export keeps a native Terrain.':'GLB includes UVs and materials. The Blender package also provides separate textures and UV layouts.';const r=j.result;$('stats').textContent=j.state==='done'?(r?(terrain?`${r.dimensions_m?.[0]||j.settings.size_m} m square · elevation ${r.height_m} m · ${r.resolution}² map`:`${Number(r.triangles||0).toLocaleString('en-US')} triangles${r.bones?' · '+r.bones+' bones':''} · ${Number(r.height_m||0).toLocaleString('en-US',{maximumFractionDigits:2})} m`):'Completed model · ready to inspect'):'';
  if(j.state==='done'||j.operation==='animation'||j.settings.source_kind==='unirig')loadModel(j.id);else if(loadedId!==j.id){++loadVersion;clearModel();$('empty').hidden=false;}const entries=[['Asset type',assetTypeName(j.metadata?.asset_type||j.settings.asset_type)],['Engine',external?(j.settings.asset_type==='print'?'Blender voxel remesh':j.result?.rig_engine||'Local import'):terrain?'Procedural':pixal?`Pixal3D / ${resolutionOf(j)}³`:`TRELLIS.2 / ${resolutionOf(j)}³`],['Height',j.settings.height_m+' m'],['Texture',j.settings.texture_size+' px'],['Seed',j.settings.seed??'—'],['Status',labels[j.state]||j.state],['Created',new Date(j.settings.created*1000).toLocaleDateString('en-US')]];const o=j.generation_options;
  const triangleBudget=[j.settings.adaptive_finishing?'Triangle ceiling':'Target triangles',Number(j.settings.triangles).toLocaleString('en-US')];
  if(pixal){
    entries.push(['Reference views',(j.settings.views||[]).map(view=>view.role).join(' · ')],['Side view angle',j.settings.side_angle+'°'],triangleBudget);
    const projection=j.result?.face_projection;
    if(projection?.state==='applied')entries.push(['Face reprojection','Applied']);
    else if(projection?.state==='skipped')entries.push(['Face reprojection',projection.reason?'Skipped. '+projection.reason:'Skipped']);
    else if(j.settings.reproject_face)entries.push(['Face reprojection',j.state==='done'?'No report':'Requested']);
  }else if(o&&!terrain)entries.push(['Steps · structure / shape / texture',`${o.structure_steps} / ${o.shape_steps} / ${o.texture_steps}`],['Guidance · structure / shape / texture',`${o.structure_guidance} / ${o.shape_guidance} / ${o.texture_guidance}`],triangleBudget,['Finishing',o.mesh_mode==='direct'?'Direct simplification':'Remesh and simplify'],['Pre-simplify to 2M',o.pre_simplify?'Yes':'No'],['Seed texture',o.texture_seed<0?'Main random sequence':o.texture_seed],['Angle UV',o.uv_angle+'°']);
  if (r?.adaptive_finishing) {
    entries.push(['Surface tolerance', r.adaptive_finishing.target_met ? 'Met' : 'Exceeded at the triangle ceiling']);
  }
  $('assetDetails').replaceChildren(...entries.flatMap(([k,v])=>[node('dt','',k),node('dd','',String(v))]));if(textureOpen&&inspectId!==j.id)inspectTextures();
}
function renderNavigation(){const select=$('librarySelect'),signature=records.map(j=>j.id+nameOf(j)+j.state).join('|');if(select.dataset.signature!==signature){select.replaceChildren(node('option','','Choose from the library…'));select.firstChild.value='';for(const j of records){const o=node('option','',nameOf(j)+' · '+(labels[j.state]||j.state));o.value=j.id;select.append(o);}select.dataset.signature=signature;}select.value=selectedId||'';$('count').textContent=records.length;$('collectionCount').textContent=records.length;const recentKey=JSON.stringify([selectedId,records.slice(0,6).map(j=>[j.id,nameOf(j),j.state])]);if($('history').dataset.signature===recentKey)return;$('history').dataset.signature=recentKey;$('history').replaceChildren();for(const j of records.slice(0,6)){const b=node('button','history-card'+(j.id===selectedId?' selected':''));b.type='button';const image=node('img');image.src=`/api/jobs/${j.id}/files/${j.settings.asset_type==='terrain_unity'?'heightmap.png':'input.png'}?v=${j.result?.revision||j.state}`;image.alt='';image.loading='lazy';image.onerror=()=>image.style.visibility='hidden';const t=node('span');t.append(node('strong','',nameOf(j)),node('small','',labels[j.state]||j.state));b.append(image,t);b.onclick=()=>selectJob(j.id);$('history').append(b);}if(!records.length)$('history').append(node('p','history-empty','No models yet.'));}
function setMode(terrain){animationStudio?.closeRig();animationMode=false;document.body.classList.remove('animation-mode');document.body.classList.remove('library-mode');$('rigPanel').hidden=true;$('rigTab').setAttribute('aria-pressed','false');showCollection(false);$('openLibrary').setAttribute('aria-pressed','false');$('form').hidden=terrain;$('terrainForm').hidden=!terrain;$('imageTab').setAttribute('aria-pressed',!terrain);$('terrainTab').setAttribute('aria-pressed',terrain);document.querySelector('h1').textContent=terrain?'Create terrain':'Image to 3D';document.querySelector('.intro').textContent=terrain?'Textured procedural terrain, ready to adapt to your scene.':'Turn a reference image into a textured 3D model.';}
function selectJob(id){const keepAnimation=animationMode;if(!records.some(j=>j.id===id))return;if(id!==selectedId){selectedId=id;metadataId=null;inspectId=null;++inspectVersion;$('textureGallery').replaceChildren();$('textureSummary').textContent='Inspecting textures…';}setMode(jobOf()?.settings.asset_type==='terrain_unity');if(keepAnimation)openAnimationStudio();renderNavigation();renderSelected();renderLibrary();updateButtons();refreshLog();}
$('librarySelect').onchange=()=>selectJob($('librarySelect').value);$('imageTab').onclick=()=>{$('parentId').value='';setMode(false);};$('terrainTab').onclick=()=>{$('terrainForm').dataset.parentId='';setMode(true);};$('openLibrary').onclick=()=>{animationStudio?.closeRig();document.body.classList.remove('collection-collapsed');document.body.classList.add('library-mode');showCollection(false);for(const id of['imageTab','terrainTab','rigTab'])$(id).setAttribute('aria-pressed','false');$('openLibrary').setAttribute('aria-pressed','true');renderLibrary();};
function renderLibrary(){studioTools?.selection.sync(records,trashMode);const q=$('librarySearch').value.toLocaleLowerCase('en'),filter=$('libraryFilter').value,list=(trashMode?trashRecords:records).filter(j=>{const m=j.metadata||{},text=[nameOf(j),m.category,assetTypeName(m.asset_type||j.settings.asset_type),...(m.tags||[])].join(' ').toLocaleLowerCase('en');return (!workbench||workbench.workspace.matches(j))&&text.includes(q)&&(filter==='all'||filter==='favorite'&&m.favorite||filter===j.state||filter===j.settings.asset_type||filter===m.review_state||filter==='type:'+(m.asset_type||j.settings.asset_type));}),pages=Math.max(1,Math.ceil(list.length/24));page=Math.max(0,Math.min(page,pages-1));studioTools?.selection.visible(list.slice(page*24,page*24+24),list);const signature=JSON.stringify([q,filter,page,trashMode,features,selectedId,cardTurntablesAvailable,list.map(j=>[j.id,nameOf(j),j.state,j.metadata,j.media,j.presentation?.thumbnail?.revision,j.presentation?.library_turntable?.revision])]);if($('libraryGrid').dataset.signature===signature){for(const card of $('libraryGrid').querySelectorAll('[data-job-id]')){const j=list.find(x=>x.id===card.dataset.jobId);if(j)updateCardStatus(card,j);}return;}cardMenu.close();cardTurntables.stop();$('libraryGrid').dataset.signature=signature;$('pageLabel').textContent=`${page+1} / ${pages}`;$('pagePrev').disabled=page===0;$('pageNext').disabled=page>=pages-1;$('libraryTotal').textContent=`${list.length} model${list.length>1?'s':''}`;$('showTrash').replaceChildren(icon(trashMode?'grid':'trash'));$('showTrash').title=trashMode?'Back to library':'Open trash';$('showTrash').setAttribute('aria-label',$('showTrash').title);$('showTrash').disabled=!features;$('collectionTitle').textContent=trashMode?'Trash':'My models';$('collectionCount').textContent=trashMode?trashRecords.length:records.length;$('libraryGrid').replaceChildren();
  for(const j of list.slice(page*24,page*24+24)){const card=node('article','asset-card'+(j.id===selectedId?' selected':'')),b=node('button','card-open');card.dataset.jobId=j.id;b.type='button';b.disabled=trashMode;const img=node('img');img.src=trashMode?'/web/asset-placeholder.svg':j.presentation?.thumbnail?`/api/jobs/${j.id}/presentation/thumbnail.png?v=${j.presentation.thumbnail.revision}`:`/api/jobs/${j.id}/files/${j.settings.asset_type==='terrain_unity'?'heightmap.png':'input.png'}?v=${j.result?.revision||j.state}`;img.loading='lazy';img.alt='Reference for '+nameOf(j);img.onerror=()=>{img.onerror=null;img.src='/web/asset-placeholder.svg';};const body=node('span','card-body');body.append(node('strong','',nameOf(j)),node('small','',(categoryName(j.metadata?.category)||'Uncategorised')+' · '+(labels[j.state]||j.state)),node('span','badge',(j.metadata?.favorite?'★ · ':'')+(reviews[j.metadata?.review_state]||'To review')));b.append(img,body,studioTools.mediaBadge(j));b.onclick=()=>{if(studioTools.selection.mode)studioTools.selection.toggle(j);else selectJob(j.id);};card.append(b);if(trashMode){const restore=node('button','secondary restore','Restore');restore.onclick=async()=>{restore.disabled=true;try{await api(`/api/trash/${j.id}/restore`,{method:'POST'});trashRecords=await api('/api/trash');await refresh();renderLibrary();}catch(e){restore.textContent=e.message;restore.disabled=false;}};card.append(restore);}else{card.append(node('span','reference-caption',j.settings.asset_type==='terrain_unity'?'HEIGHTMAP':['imported','rigged','print'].includes(j.settings.asset_type)?(j.settings.asset_type==='print'?'3D PRINT COPY':'3D MODEL'):'REFERENCE IMAGE'));card.append(cardMenu.button(j,{busy,features,activeId}));studioTools.selection.attach(card,j);if(cardTurntablesAvailable)cardTurntables.attach(card,img,j,nameOf(j));}updateCardStatus(card,j);$('libraryGrid').append(card);}if(!list.length)$('libraryGrid').append(node('div','library-empty',trashMode?'The trash is empty.':'No models match these filters.'));
}
for(const id of['librarySearch','libraryFilter'])$(id).addEventListener(id==='librarySearch'?'input':'change',()=>{page=0;renderLibrary();});$('pagePrev').onclick=()=>{--page;renderLibrary();};$('pageNext').onclick=()=>{++page;renderLibrary();};$('showTrash').onclick=async()=>{try{trashMode=!trashMode;if(trashMode)trashRecords=await api('/api/trash');page=0;renderLibrary();}catch(e){trashMode=false;errorAt('metadataNotice',e);}};
async function patchMetadata(value){const id=selectedId;if(!id)return;const m=await api(`/api/jobs/${id}/metadata`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)}),j=records.find(x=>x.id===id);if(j)j.metadata=m;renderNavigation();renderSelected();renderLibrary();return m;}
$('metadataForm').onsubmit=async e=>{e.preventDefault();$('saveMetadata').disabled=true;try{await patchMetadata({display_name:$('displayName').value,category:$('category').value,asset_type:$('detailAssetType').value,review_state:$('reviewState').value,tags:$('tags').value.split(',').map(x=>x.trim()).filter(Boolean),notes:$('notes').value});$('metadataNotice').textContent='Details saved.';}catch(e){errorAt('metadataNotice',e);}finally{$('saveMetadata').disabled=!features||!jobOf();}};$('favorite').onclick=async()=>{try{await patchMetadata({favorite:!jobOf()?.metadata?.favorite});}catch(e){errorAt('metadataNotice',e);}};
let deleteId=null;function requestDelete(j){if(!j)return;deleteId=j.id;$('deleteName').textContent=nameOf(j);$('confirmDelete').disabled=false;$('deleteDialog').showModal();}$('confirmDelete').onclick=async()=>{$('confirmDelete').disabled=true;try{await api(`/api/jobs/${deleteId}`,{method:'DELETE'});if(selectedId===deleteId){selectedId=null;metadataId=null;inspectId=null;++inspectVersion;}$('deleteDialog').close();await refresh();}catch(e){errorAt('deleteName',e);$('confirmDelete').disabled=false;}};
function openImage(url,alt){$('largeImage').src=url;$('largeImage').alt=alt;$('imageDialog').showModal();}$('sourceThumb').onclick=()=>openImage($('sourcePreview').src,'Model reference');$('closeImage').onclick=()=>$('imageDialog').close();
function setView(textures){textureOpen=textures;viewport.hidden=textures;$('texturePanel').hidden=!textures;$('view3d').classList.toggle('active',!textures);$('viewTextures').classList.toggle('active',textures);if(textures)inspectTextures();}$('view3d').onclick=()=>setView(false);$('viewTextures').onclick=()=>setView(true);
async function inspectTextures(){const j=jobOf();if(!j||j.state!=='done'||!features)return;const id=j.id;if(inspectId===id)return;inspectId=id;const version=++inspectVersion;$('textureSummary').textContent='Extracting texture maps and UV layouts…';$('textureGallery').replaceChildren();try{const d=await api(`/api/jobs/${id}/inspect`);if(version!==inspectVersion||selectedId!==id)return;$('textureSummary').textContent=`${d.meshes.length} mesh(es) · ${d.has_uv?'UVs present':'UVs missing'} · ${d.has_pbr?'roughness and metalness present':'PBR maps incomplete'} · ${d.normals_map_present?'normal map present':'no normal map in this GLB'}`;const items=[...d.textures.map(t=>({...t,detail:`${t.width} × ${t.height} · ${t.color_space}`})),...d.meshes.filter(m=>m.uv_preview).map(m=>({file:m.uv_preview,label:'UV layout · '+m.name,detail:m.uv_warning||'Painting guide · PNG / SVG included in the package'}))];for(const t of items){const b=node('button','texture-card');b.type='button';const img=node('img');img.src=`/api/jobs/${id}/blender/${t.file}`;img.alt=t.label;b.append(img,node('strong','',t.label),node('small','',t.detail));b.onclick=()=>openImage(img.src,t.label);$('textureGallery').append(b);}if(!items.length)$('textureGallery').append(node('p','hint','This model contains no image textures.'));}catch(e){if(version===inspectVersion){inspectId=null;errorAt('textureSummary',e);}}}
function updateButtons(){
  const label=$('generate').querySelector('span');
  if(label)label.textContent=busy?'Add to queue':multiview.active?'Generate character':'Generate model';
  const supported=multiview.active?multiview.ready:ready&&advancedControls.ready&&profileControls?.ready&&
    availableResolutions.includes(Number($('resolution').value))&&availableTextures.includes(Number($('textureSize').value)||($('preset').value==='fast'?1024:2048));
  $('generate').disabled=!multiview.hasFront||!supported||shutDown||submittingGeneration||restoringReferences;
  referencePreview.sync(shutDown||submittingGeneration||restoringReferences);
  updateRefinishTool(jobOf());
  if(multiview.active)$('generationSummary').textContent=multiview.summary;
  $('cancel').hidden=!busy;
  $('cancel').disabled=!activeId||jobOf()?.settings.asset_type==='terrain_unity';
  $('createTerrain').disabled=busy||shutDown||!features;
}
$('copyReferencePrompt').onclick=async()=>{const prompt=`Production reference for a contemporary stylised realism game. Subject: ${$('name').value}. ${$('referencePrompt').value}\nOne complete object fully visible and centred, isolated on a plain neutral grey background, single three-quarter view. Credible construction, readable detail and carefully worked materials. Soft diffuse light and minimal shadow. No surrounding scene, text, logo or multi-view sheet. Keep thin parts readable and show the whole subject without cropping. The grey background is not part of the object.`;try{await navigator.clipboard.writeText(prompt);$('notice').textContent='Brief copied. Use it with your image generator.';}catch{$('referencePrompt').value=prompt;$('referencePrompt').closest('details').open=true;$('referencePrompt').focus();$('referencePrompt').select();$('notice').textContent='The brief is ready in the context field for you to copy.';}};
$('form').onsubmit=async event=>{
  event.preventDefault();
  if($('generate').disabled)return;
  submittingGeneration=true;
  updateButtons();
  $('notice').textContent='';
  try{
    const usePixal=multiview.active;
    const form=usePixal?multiview.buildRequest():new FormData($('form'));
    if(!usePixal){
      form.set('remove_bg',String($('removeBg').checked));
      form.set('queue_only','true');
      form.set('advanced_settings',JSON.stringify(advancedControls.read()));
    }
    const repair = $('denseMasterRepair');
    if (!repair.disabled && repair.checked) form.set('repair_surface', 'true');
    referencePreview.append(form);
    const v=await api(usePixal?'/api/generate-multiview':'/api/generate',{method:'POST',body:form});
    if(!selectedId){selectedId=v.id;metadataId=null;}
    revealNewCreation();
    await refresh();
  }catch(error){errorAt('notice',error);}
  finally{submittingGeneration=false;updateButtons();}
};
$('cancel').onclick=async()=>{if(!activeId)return;try{await api(`/api/jobs/${activeId}/cancel`,{method:'POST'});await refresh();}catch(e){errorAt('notice',e);}};
function revealNewCreation(){trashMode=false;page=0;$('librarySearch').value='';$('libraryFilter').value='all';document.body.classList.remove('collection-collapsed','library-mode');$('toggleCollection').setAttribute('aria-pressed','true');showCollection(false);$('libraryGrid').scrollTop=0;}
document.querySelectorAll('[data-material]').forEach(b=>b.onclick=()=>{$('materialMode').value=b.dataset.material;document.querySelectorAll('[data-material]').forEach(x=>x.setAttribute('aria-pressed',x===b));materials();});
function ranges(){$('roughnessValue').textContent=Math.round(Number($('roughness').value)*100)+' %';$('flatValue').textContent=Math.round(Number($('flatZone').value)*100)+' %';}$('roughness').oninput=ranges;$('flatZone').oninput=ranges;
$('terrainForm').onsubmit=async e=>{e.preventDefault();if(busy)return;busy=true;updateButtons();$('notice').textContent='Creating terrain and textures…';const data={name:$('terrainName').value,terrain_style:$('terrainStyle').value,size_m:Number($('terrainSize').value),height_m:Number($('terrainHeight').value),seed:Number($('terrainSeed').value),roughness:Number($('roughness').value),flat_zone:Number($('flatZone').value),resolution:Number($('terrainResolution').value),terrain_palette:$('terrainPalette').value,parent_id:$('terrainForm').dataset.parentId||''};try{const v=await api('/api/terrain',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});selectedId=v.id;metadataId=null;$('notice').textContent='Terrain created.';setView(false);await refresh();}catch(e){busy=false;errorAt('notice',e);updateButtons();}};
async function reuseJob(j){
  if(!j||busy||submittingGeneration||restoringReferences)return;
  const s=j.settings;
  if(['imported','rigged','print'].includes(s.asset_type)){
    try{
      const v=await api(`/api/jobs/${j.id}/version`,{method:'POST'});
      await startStudioJob(v.id);
    }catch(error){errorAt('notice',error);}
    return;
  }
  if(s.asset_type==='terrain_unity'){
    setMode(true);
    $('terrainForm').dataset.parentId=j.id;
    for(const [id,v] of Object.entries({terrainName:nameOf(j)+' version',terrainStyle:s.terrain_style,terrainSize:s.size_m,terrainHeight:s.height_m,terrainSeed:s.seed,roughness:s.roughness,flatZone:s.flat_zone,terrainResolution:s.resolution,terrainPalette:s.terrain_palette||'blackwater'}))$(id).value=String(v);
    ranges();
    return;
  }
  restoringReferences=true;
  updateButtons();
  try{
    await multiview.restore(j);
    referencePreview.restore(s.reference_edits,s.remove_bg);
    $('denseMasterRepair').checked = s.repair_surface === true;
    setMode(false);
    $('parentId').value=j.id;
    $('name').value=(nameOf(j)+' version').slice(0,80);
    $('height').value=String(s.height_m);
    if(multiview.active){
      profileControls.restoreSource(s.keep_finishing_source);
      $('notice').textContent='Saved views and Pixal3D settings restored. Adjust them to compare a new version.';
      return;
    }
    for(const [id,v] of Object.entries({preset:s.preset,seed:s.seed,triangles:s.triangles,textureSize:s.texture_size,resolution:s.resolution||j.result?.resolution||512,assetType:s.asset_type||'object',referencePrompt:j.metadata?.reference_prompt||''}))$(id).value=String(v);
    const restoredOptions = s.repair_surface ? s.generation_options : j.generation_options;
    advancedControls.apply(restoredOptions||s.generation_options||{});
    $('removeBg').checked=s.remove_bg;
    detailControls.hint();
    profileControls.detach();profileControls.restoreSource(s.keep_finishing_source);
    $('profileBase').value=$('preset').value;
    explainCost();
    $('generationSummary').textContent='VARIANT · LOCAL COMPUTE';
    $('notice').textContent='Reference, geometry, texture and finishing settings restored. Adjust them to compare a new version.';
  }catch(error){errorAt('notice',error);}
  finally{restoringReferences=false;updateButtons();}
}

function updateRefinishTool(job){
  const control=$('toolbarRefinishControl'),button=$('toolbarRefinish');
  control.hidden=job?.state!=='done';
  button.disabled=control.hidden||!job.can_refinish||busy||shutDown;
  const panel=$('refinishDialog');
  if(panel.matches(':popover-open')&&(button.disabled||panel.dataset.source!==job?.id))panel.hidePopover();
  if(control.hidden)return;
  control.title=shutDown?'Restart TRELLIS Studio to remesh.':!job.can_refinish?
    'Finishing source unavailable. Enable “Keep source for finishing variants” when generating a new model.':
    busy?'Wait for the active computation to finish.':'Remesh · choose triangle budget';
  if(button.disabled){control.tabIndex=0;control.setAttribute('aria-label',`Remesh. ${control.title}`);}
  else{control.removeAttribute('tabindex');control.removeAttribute('aria-label');}
}

const refinishPanel = $('refinishDialog');
const refinishButton = $('toolbarRefinish');
const refinishForm = $('refinishForm');
const refinishFields = refinishForm.elements;
let refinishRequest = 0;
let submittingRefinish = false;

function positionRefinish() {
  if (!refinishPanel.matches(':popover-open')) return;
  const anchor = refinishButton.getBoundingClientRect();
  if (!anchor.width || anchor.bottom < 0 || anchor.top > innerHeight) {
    refinishPanel.hidePopover();
    return;
  }
  const {width, height} = refinishPanel.getBoundingClientRect();
  const gap = 10;
  const left = anchor.left + (anchor.width - width) / 2;
  const above = anchor.top - height - gap;
  const below = anchor.bottom + gap;
  const top = above >= gap || below + height > innerHeight - gap ? above : below;
  refinishPanel.style.left = Math.max(gap, Math.min(innerWidth - width - gap, left)) + 'px';
  refinishPanel.style.top = Math.max(gap, Math.min(innerHeight - height - gap, top)) + 'px';
}

function syncRefinishMode() {
  const adaptive = refinishFields.budget_mode.value === 'adaptive';
  const preset = refinishFields.budget_preset.value;
  const manual = preset === 'manual';
  const repair = !refinishFields.repair_surface.disabled && refinishFields.repair_surface.checked;
  $('refinishBudgetField').hidden = !manual;
  refinishFields.triangles.disabled = !manual;
  $('refinishBudgetLabel').textContent = adaptive ? 'Maximum triangles' : 'Target triangles';
  $('refinishManualOptions').hidden = !manual || adaptive || repair;
  $('startRefinishLabel').textContent = repair ? 'Create repaired variant' : 'Create variant';
  const budgetHint = adaptive ? 'Tests lighter meshes up to the chosen ceiling.' : 'The final count can be lower.';
  const presetHint = {
    medium: 'Budget for a game model.',
    ultra: 'Can exceed the preview mesh. Dense variants take longer to process.',
  }[preset];
  $('refinishBudgetHint').textContent = [presetHint, budgetHint].filter(Boolean).join(' ');
}

async function openRefinish(job) {
  if (busy || shutDown || submittingRefinish || !job?.can_refinish) return;
  if (selectedId !== job.id || document.body.classList.contains('library-mode')) selectJob(job.id);
  setView(false);
  refinishButton.scrollIntoView({block: 'nearest', inline: 'nearest'});
  const request = ++refinishRequest;
  refinishPanel.dataset.source = job.id;
  $('refinishSource').textContent = nameOf(job);
  $('refinishSource').title = nameOf(job);
  $('refinishControls').hidden = true;
  $('refinishStatus').hidden = false;
  $('refinishStatus').textContent = 'Loading triangle budgets…';
  $('refinishError').hidden = true;
  $('startRefinish').disabled = true;
  $('refinishOptions').open = false;
  if (!refinishPanel.matches(':popover-open')) refinishPanel.showPopover({source: refinishButton});
  positionRefinish();

  try {
    const config = await api(`/api/jobs/${job.id}/finishing-board`);
    if (request !== refinishRequest || !refinishPanel.matches(':popover-open')) return;
    const {triangle_limits: limits, triangle_presets: presets, texture_sizes: textures} = config;
    $('refinishDisplayedTriangles').textContent = formatTriangles(job.result?.triangles);
    $('refinishSourceTriangles').textContent = formatTriangles(config.source_triangles);
    for (const item of refinishPanel.querySelectorAll('[data-budget]')) {
      const count = presets[item.dataset.budget];
      item.textContent = count >= 1000000 ? `${count / 1000000}M` : `${count / 1000}k`;
      item.closest('label').title = `${count.toLocaleString('en-US')} triangles`;
    }
    refinishFields.budget_mode.value = 'fixed';
    refinishFields.budget_preset.value = 'medium';
    refinishFields.repair_surface.checked = false;
    refinishFields.repair_surface.disabled = config.repair_surface !== true;
    $('refinishRepairHint').textContent = refinishFields.repair_surface.disabled ?
      'Restart Studio to enable hole repair.' : 'Closes gaps using the cached source. Fine edges may soften.';
    refinishFields.triangles.min = limits.min;
    refinishFields.triangles.max = limits.max;
    const suggested = job.result?.triangles || job.settings.triangles || presets.medium;
    refinishFields.triangles.value = Math.max(limits.min, Math.min(limits.max, suggested));
    const options = job.generation_options || job.settings.generation_options || {};
    refinishFields.mesh_mode.value = options.mesh_mode || 'direct';
    refinishFields.repair_holes.checked = !!options.repair_holes;
    refinishFields.pre_simplify.checked = !!options.pre_simplify;
    refinishFields.texture_size.replaceChildren(...textures.map(size => new Option(`${size / 1024}K`, size)));
    refinishFields.texture_size.value = textures.includes(job.settings.texture_size) ? job.settings.texture_size : textures[0];
    refinishFields.bake_normals.disabled = !config.normal_bake;
    refinishFields.bake_normals.checked = !!config.normal_bake;
    $('refinishBake').hidden = !config.normal_bake;
    $('refinishStatus').hidden = true;
    $('refinishControls').hidden = false;
    $('startRefinish').disabled = false;
    syncRefinishMode();
    positionRefinish();
    if ([document.body, refinishButton, $('closeRefinish')].includes(document.activeElement)) {
      refinishForm.querySelector('[name="budget_mode"]:checked').focus({preventScroll: true});
    }
  } catch (error) {
    if (request === refinishRequest && refinishPanel.matches(':popover-open')) {
      $('refinishStatus').textContent = error.message || String(error);
      positionRefinish();
    }
  }
}

refinishButton.onclick = event => {
  event.preventDefault();
  if (refinishPanel.matches(':popover-open')) refinishPanel.hidePopover();
  else openRefinish(jobOf());
};
$('closeRefinish').onclick = () => refinishPanel.hidePopover();
refinishPanel.addEventListener('toggle', event => {
  const open = event.newState === 'open';
  refinishButton.setAttribute('aria-expanded', open);
  if (!open && refinishPanel.contains(document.activeElement)) refinishButton.focus({preventScroll: true});
});
new ResizeObserver(positionRefinish).observe(refinishPanel);
window.addEventListener('resize', positionRefinish);
window.addEventListener('scroll', positionRefinish, {capture: true, passive: true});

refinishForm.addEventListener('change', event => {
  const {name} = event.target;
  if (name === 'budget_preset') {
    refinishFields.bake_normals.checked = !refinishFields.bake_normals.disabled && refinishFields.budget_preset.value === 'medium';
  }
  if (['budget_mode', 'budget_preset', 'repair_surface'].includes(name)) syncRefinishMode();
});

refinishForm.onsubmit = async event => {
  event.preventDefault();
  const source = refinishPanel.dataset.source;
  if (!source || submittingRefinish || $('startRefinish').disabled) return;
  const preset = refinishFields.budget_preset.value;
  const data = {
    texture_size: Number(refinishFields.texture_size.value),
    adaptive_finishing: refinishFields.budget_mode.value === 'adaptive',
  };
  if (!refinishFields.repair_surface.disabled && refinishFields.repair_surface.checked) data.repair_surface = true;
  if (!refinishFields.bake_normals.disabled) data.bake_normals = refinishFields.bake_normals.checked;
  if (preset === 'manual') {
    data.triangles = refinishFields.triangles.valueAsNumber;
    if (!data.adaptive_finishing && !data.repair_surface) {
      data.mesh_mode = refinishFields.mesh_mode.value;
      data.repair_holes = refinishFields.repair_holes.checked;
      data.pre_simplify = refinishFields.pre_simplify.checked;
    }
  } else data.budget_preset = preset;

  submittingRefinish = true;
  $('startRefinish').disabled = true;
  $('refinishError').hidden = true;
  try {
    const result = await api(`/api/jobs/${source}/refinish`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
    if (refinishPanel.matches(':popover-open')) refinishPanel.hidePopover();
    await startStudioJob(result.id);
  } catch (error) {
    errorAt('refinishError', error);
    $('refinishError').hidden = false;
    if (!refinishPanel.matches(':popover-open')) errorAt('notice', error);
  } finally {
    submittingRefinish = false;
    $('startRefinish').disabled = false;
  }
};

async function refreshLog(){if(!selectedId)return;try{const d=await api(`/api/jobs/${selectedId}/log`);$('log').textContent=d.text||'No log entries yet.';}catch{}}
async function refresh(){if(shutDown||refreshing)return;refreshing=true;try{const[status,jobs]=await Promise.all([api('/api/status'),api('/api/jobs')]);features=!!status.studio_version;cardTurntablesAvailable=status.features?.includes('library_turntable')===true;syncCapabilities(status);ready=status.ready;activeId=status.active;busy=!!activeId;records=jobs;if(pendingSelection){const pending=records.find(j=>j.id===pendingSelection);if(pending?.state==='done'){if(pending.operation_error)errorAt('animationNotice',pending.operation_error);selectedId=pendingSelection;pendingSelection=null;metadataId=null;inspectId=null;}else if(pending&&['error','cancelled'].includes(pending.state)){errorAt('animationNotice',pending.error||pending.stage);pendingSelection=null;}}if(selectedId&&!records.some(j=>j.id===selectedId)){selectedId=null;metadataId=null;inspectId=null;++inspectVersion;}syncVersionNotice(status);$('runtime').textContent=ready?`LOCAL · ${status.ram_free_gib} GB RAM available`:'WORKSHOP · generation engine not installed';$('hardware').textContent=status.gpu||'Local compute';updateButtons();renderNavigation();renderSelected();renderLibrary();if(document.querySelector('.journal').open)refreshLog();if(jobOf()?.settings.source_kind==='unirig'&&jobOf()?.state==='running')refreshRigPreview();}catch(error){console.error('Studio refresh failed:',error);$('runtime').textContent='Disconnected · restart TRELLIS';ready=false;multiview.setCapabilities({message:'Studio is disconnected. Restart TRELLIS Studio.'});updateButtons();}finally{refreshing=false;}}
$('shutdown').onclick=async()=>{try{await api('/api/shutdown',{method:'POST'});shutDown=true;ready=false;busy=false;renderer?.setAnimationLoop(null);clearModel();renderer?.dispose();$('notice').textContent='Run Open TRELLIS.cmd to restart the studio.';$('runtime').textContent='STUDIO STOPPED';$('shutdown').disabled=true;downloads(null);$('downloadHeight').hidden=true;updateButtons();}catch(e){errorAt('notice',e);}};
function showCollection(details){$('libraryView').hidden=details;$('inspectorPanel').hidden=!details;$('collectionPanel').classList.toggle('details-open',details);}
function openDetails(j){if(!j)return;selectJob(j.id);document.body.classList.remove('library-mode','collection-collapsed');$('openLibrary').setAttribute('aria-pressed','false');showCollection(true);}
$('backToCollection').onclick=()=>showCollection(false);
$('toggleCollection').onclick=()=>{const collapsed=document.body.classList.toggle('collection-collapsed');$('toggleCollection').setAttribute('aria-pressed',!collapsed);};
function openAnimationStudio(){setMode(false);animationMode=true;document.body.classList.add('animation-mode');$('form').hidden=true;$('rigPanel').hidden=false;$('imageTab').setAttribute('aria-pressed','false');$('rigTab').setAttribute('aria-pressed','true');document.querySelector('h1').textContent='Animation studio';document.querySelector('.intro').textContent='Choose a motion and add it to your model.';animationStudio?.render();}
$('rigTab').onclick=openAnimationStudio;$('animateModel').onclick=openAnimationStudio;

document.addEventListener('paste',event=>{
  if(referencePreview.open||event.target.closest('input:not([type=file]),textarea,[contenteditable=true]'))return;
  const file=[...(event.clipboardData?.files||[])].find(value=>value.type.startsWith('image/'));
  if(file){
    event.preventDefault();
    setMode(false);
    multiview.accept(multiview.pasteRole(event.target),file);
  }
});
async function startStudioJob(id,options={}){if(options.rig){selectedId=id;metadataId=null;}busy=true;pendingSelection=id;revealNewCreation();updateButtons();await refresh();}
animationStudio=await createAnimationStudio({api,getJob:jobOf,isBusy:()=>busy,startJob:startStudioJob,rigSetup:rigSetupViewer});createModelImport({api,startJob:startStudioJob});
workbench=await createStudioWorkbench({api,getRecords:()=>records,getJob:jobOf,refresh,startJob:startStudioJob,renderLibrary,tools:studioTools,canUseCardTurntables:()=>cardTurntablesAvailable,labelAnimation:name=>animationStudio?.label(name)||name,getViewer:()=>({scene,renderer,camera,controls,model,loadedId,lighting:viewerLighting})});
document.querySelector('.journal').addEventListener('toggle',refreshLog);await refresh();const entry=new URLSearchParams(location.search),entryModel=entry.get('model');if(entryModel&&records.some(j=>j.id===entryModel))selectJob(entryModel);if(entry.get('workspace')==='animation')openAnimationStudio();if(entry.get('workspace')==='details'&&jobOf())openDetails(jobOf());const comparison=records.find(j=>j.id===entry.get('compare')&&j.state==='done');if(comparison&&jobOf()?.state==='done')studioTools.compare.open(jobOf(),comparison);setInterval(refresh,3000);

function setupClips(gltf){
 clips=gltf.animations;mixer=clips.length?new THREE.AnimationMixer(model):null;
 $('clipSelect').replaceChildren(...clips.map((c,i)=>{const o=node('option','',animationStudio?.label(c.name)||c.name);o.value=i;return o;}));
 skeletonHelper=new THREE.SkeletonHelper(model);skeletonHelper.visible=jobOf()?.settings.source_kind==='unirig';skeletonHelper.material.depthTest=false;skeletonHelper.material.vertexColors=false;skeletonHelper.material.color.set(0xc4f58a);skeletonHelper.renderOrder=10;scene.add(skeletonHelper);if(skeletonHelper.bones.length){const radius=Math.max(new THREE.Box3().setFromObject(model,true).getSize(new THREE.Vector3()).y*.004,.003);boneDots=new THREE.InstancedMesh(new THREE.SphereGeometry(radius,8,6),new THREE.MeshBasicMaterial({color:0xc4f58a,depthTest:false}),skeletonHelper.bones.length);boneDots.renderOrder=11;boneDots.frustumCulled=false;scene.add(boneDots);}$('clipSkeleton').setAttribute('aria-pressed',skeletonHelper.visible);
 $('animationPlayer').hidden=!clips.length&&!skeletonHelper.bones.length;
 $('clipSelect').hidden=!clips.length;$('clipTime').hidden=!clips.length;$('clipPlay').hidden=!clips.length;if(clips.length)selectClip(0);
}
function selectClip(index){mixer?.stopAllAction();clipAction=mixer.clipAction(clips[index]);clipAction.play();mixer.update(0);clipPlaying=true;clock.getDelta();document.querySelector('#clipPlay use').setAttribute('href','/web/icons.svg#pause');$('clipTime').max=clips[index].duration;}
$('clipSelect').onchange=()=>selectClip(Number($('clipSelect').value));
$('clipPlay').onclick=()=>{clipPlaying=!clipPlaying;$('clipPlay').querySelector('use').setAttribute('href','/web/icons.svg#'+(clipPlaying?'pause':'play'));};
$('clipTime').oninput=()=>{if(!clipAction)return;clipPlaying=false;clipAction.time=Number($('clipTime').value);mixer.update(0);$('clipPlay').querySelector('use').setAttribute('href','/web/icons.svg#play');$('clipTimeValue').value=clipAction.time.toFixed(1)+' s';};
$('clipSkeleton').onclick=()=>{if(predictedSkeleton&&(!skeletonHelper||!skeletonHelper.bones.length)){predictedSkeleton.visible=!predictedSkeleton.visible;$('clipSkeleton').setAttribute('aria-pressed',predictedSkeleton.visible);return;}if(skeletonHelper){skeletonHelper.visible=!skeletonHelper.visible;$('clipSkeleton').setAttribute('aria-pressed',skeletonHelper.visible);}};
let predictedSkeleton=null,predictedId=null;
async function refreshRigPreview(){const j=jobOf();if(!j||predictedId===j.id)return;try{const data=await api(`/api/jobs/${j.id}/rig-preview`);if(!data.ready||selectedId!==j.id)return;const vertices=data.bones.flatMap(b=>[...b.head,...b.tail]);const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));if(predictedSkeleton){scene.remove(predictedSkeleton);predictedSkeleton.geometry.dispose();predictedSkeleton.material.dispose();}predictedSkeleton=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:0xc4f58a,depthTest:false,transparent:true,opacity:.95}));predictedSkeleton.renderOrder=12;scene.add(predictedSkeleton);predictedId=j.id;$('animationPlayer').hidden=false;$('clipSkeleton').setAttribute('aria-pressed','true');}catch{}}
