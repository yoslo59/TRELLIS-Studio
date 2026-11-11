import {icon} from './card-menu.js?v=2.6.1';
import {createMotionPreviews} from './authored-previews.js?v=2.6';
export async function createAnimationStudio({api,getJob,isBusy,startJob,refresh,rigSetup,getClips,playClip}){
 const $=id=>document.getElementById(id),data=await api('/api/animations');
 let tab='library';const catalog=data.animations,previews=createMotionPreviews();
 let batchAvailable=false,removeAvailable=false;
 let selecting=false,selectionTarget=null,submitting=false;
 let clipNotice=null,removal=null;
 const selectedClips=new Set();
 const batchLimit=Number.isInteger(data.max_batch_clips)?data.max_batch_clips:catalog.length;
 const categories=[...new Set(catalog.map(c=>c.category))];
 for(const category of categories){const option=document.createElement('option');option.value=category;option.textContent=category;$('animationCategory').append(option);}
 const nameOf=j=>j?.metadata?.display_name||j?.settings.name||'Choose a model from the collection';
 $('animationDuration').oninput=()=>{$('animationDurationValue').value=Number($('animationDuration').value).toLocaleString('en-US')+' s';};
 const hasRig=job=>Boolean(job?.result?.bones||job?.result?.rigged||job?.media?.bones);
 const canEdit=job=>job?.state==='done'&&!isBusy()&&!submitting;
 const canAdd=job=>canEdit(job)&&hasRig(job);
 const canRemove=job=>canEdit(job)&&removeAvailable;

 function authoredMotion(name){
  const match=/^TS_(.+)_([0-9.]+)s$/.exec(name||'');
  if(!match)return null;
  return {id:match[1],title:catalog.find(clip=>clip.id===match[1])?.name||match[1],duration:Number(match[2])};
 }

 function embeddedClips(job){
  if(!job)return [];
  const loaded=getClips(job);
  const items=job.media?.animation_items
   ?? (loaded?.map(({name},index)=>({index,name}))||job.media?.animation_names?.map((name,index)=>({index,name}))||[]);
  const entries=items.map(({index,name})=>{
   const expectedName=name||`animation_${index}`;
   const preview=loaded?.[index]?.name===expectedName?loaded[index]:null;
   const authored=authoredMotion(name);
   const duration=preview?.duration??authored?.duration??job.result?.animation_set?.find(clip=>clip.clip===name)?.duration_s;
   return {index,name,expectedName,duration,recipeId:authored?.id,playable:!!preview,title:authored?.title||name||`Animation ${index+1}`};
  });
  const duplicates=new Set(entries.filter((entry,index)=>entries.findIndex(other=>other.title===entry.title)!==index).map(entry=>entry.title));
  return entries.map(entry=>duplicates.has(entry.title)?{...entry,title:`${entry.title} (${entry.index+1})`}:entry);
 }

 function showClipNotice(job){
  const message=clipNotice?.id===job?.id?clipNotice?.message||'':'';
  if($('animationClipNotice').textContent!==message)$('animationClipNotice').textContent=message;
 }

 function setClipNotice(id,message){
  clipNotice={id,message};
  showClipNotice(getJob());
 }

 function resetSelection(){
  selecting=false;
  selectedClips.clear();
 }

 function updateSelectionControls(job){
  const enabled=canAdd(job),count=selectedClips.size;
  $('createRig').disabled=!canEdit(job)||!rigSetup?.ready;
  $('animationSelection').hidden=tab==='added'||!batchAvailable||!batchLimit;
  $('selectAnimations').disabled=!enabled;
  $('selectAnimations').setAttribute('aria-pressed',String(selecting));
  $('selectAnimations').querySelector('span').textContent=selecting?'Cancel selection':'Select clips';
  $('selectedAnimationCount').hidden=!selecting;
  const countLabel=`${count} ${count===1?'clip':'clips'} selected`;
  if($('selectedAnimationCount').textContent!==countLabel)$('selectedAnimationCount').textContent=countLabel;
  $('animationSelectionActions').hidden=!selecting;
  $('clearAnimationSelection').disabled=!count||submitting;
  $('addAnimationSelection').disabled=!count||!enabled;
  for(const button of $('animationCards').querySelectorAll('button[data-clip]'))button.disabled=!enabled;
  for(const button of $('animationCards').querySelectorAll('button[data-remove-clip]'))button.disabled=!canRemove(job);
  for(const input of $('animationCards').querySelectorAll('input[data-select-clip]')){
   input.checked=selectedClips.has(input.dataset.selectClip);
   input.disabled=!enabled||(!input.checked&&count>=batchLimit);
   input.closest('.animation-card').classList.toggle('selected-motion',input.checked);
   input.nextElementSibling.textContent=input.checked?'Selected':'Select';
  }
 }

 async function editClips(job,method,body,success,batch=false){
  const path=`/api/v1/models/${job.id}/animations`;
  setClipNotice(job.id,method==='DELETE'?'Removing clip…':'Saving animations…');
  submitting=true;
  updateSelectionControls(job);
  try{
   const result=await api(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const statusUrl=result.status_url||`/api/v1/jobs/${job.id}`;
   while(true){
    const status=await api(statusUrl,{cache:'no-store'});
    if(status.state==='done'&&!status.operation_error){
     if(batch&&selectionTarget===job.id)resetSelection();
     setClipNotice(job.id,success);
     break;
    }
    if(status.state==='error'||status.state==='cancelled'||status.operation_error){
     throw Error(status.operation_error||status.error||status.stage||'Animation update did not complete.');
    }
    if(status.stage)setClipNotice(job.id,status.stage);
    await new Promise(resolve=>setTimeout(resolve,900));
   }
  }catch(error){setClipNotice(job.id,error.message);}
  finally{
   try{await refresh();}
   finally{submitting=false;render();}
  }
 }

 function submitMotions(ids,batch=false){
  const job=getJob();
  if(!canAdd(job)||!ids.length||(batch&&!batchAvailable))return;
  const duration=Number($('animationDuration').value);
  const clips=ids.map(clip=>({clip,duration}));
  return editClips(job,'POST',batch?{clips}:clips[0],ids.length===1?'Animation saved.':'Animations saved.',batch);
 }

 function confirmRemoval(job,clip){
  if(!canRemove(job))return;
  removal={id:job.id,index:clip.index,name:clip.name,revision:job.media?.animation_revision};
  $('animationRemoveName').textContent=clip.title;
  $('animationRemoveDialog').returnValue='cancel';
  $('animationRemoveDialog').showModal();
 }

 $('animationRemoveDialog').addEventListener('close',()=>{
  const target=removal,job=getJob();
  removal=null;
  if($('animationRemoveDialog').returnValue==='remove'&&target?.id===job?.id&&canRemove(job)){
   editClips(job,'DELETE',{index:target.index,clip:target.name,revision:target.revision},'Animation removed.');
  }
 });

 $('selectAnimations').onclick=()=>{
  if(selecting)resetSelection();else selecting=true;
  render();
 };
 $('clearAnimationSelection').onclick=()=>{
  selectedClips.clear();
  updateSelectionControls(getJob());
 };
 $('addAnimationSelection').onclick=()=>submitMotions([...selectedClips],true);
 const rotation=[];let rigStep=0,rigJobId=null,savedLandmarks=null;
 for(const axis of ['X','Y','Z']){const label=document.createElement('label');label.className='rig-rotation';const caption=document.createElement('span');caption.textContent='Rotation '+axis;const slider=document.createElement('input');slider.type='range';slider.min=-180;slider.max=180;slider.step=1;slider.value=0;slider.setAttribute('aria-label','Rotation '+axis);const value=document.createElement('output');value.value='0°';const reset=document.createElement('button');reset.type='button';reset.className='quiet';reset.append(icon('rotate'));reset.setAttribute('aria-label','Reset rotation '+axis);reset.onclick=()=>{slider.value=0;changeRig();};slider.oninput=changeRig;label.append(caption,slider,value,reset);rotation.push(slider);$('rigRotationControls').append(label);}
 function rigOptions(){return {rotation:rotation.map(s=>Number(s.value)),height_m:Number($('rigHeight').value),offset_m:Number($('rigOffset').value)};}
 function changeRig(){rotation.forEach(s=>s.nextElementSibling.value=s.value+'°');$('rigHeightValue').value=Number($('rigHeight').value).toLocaleString('en-US',{minimumFractionDigits:2})+' m';$('rigHeightNumber').value=$('rigHeight').value;$('rigOffsetValue').value=Number($('rigOffset').value).toLocaleString('en-US')+' m';rigSetup?.preview(rigOptions());}
 function showRigStep(step){rigStep=step;$('rigTransformStep').hidden=step!==0;$('rigLandmarkStep').hidden=step!==1;$('rigStepLabel').textContent=step===0?'01 / Size & orientation':'02 / Joint placement';$('confirmRigSetup').textContent=step===0?'Next · place joints':'Confirm & create rig';$('cancelRigSetup').textContent=step===0?'Cancel':'Back';}
 function closeRig(){document.body.classList.remove('rig-preparing');rigSetup?.close();rigJobId=null;savedLandmarks=null;$('rigSetup').hidden=true;$('animationLibraryControls').hidden=false;}
 $('createRig').onclick=()=>{if(!rigSetup?.ready)return;document.body.classList.add('rig-preparing');rigJobId=getJob()?.id;savedLandmarks=null;$('rigSetup').hidden=false;$('animationLibraryControls').hidden=true;showRigStep(0);rotation.forEach(s=>s.value=0);$('rigHeight').value=Math.min(3,Math.max(.2,getJob()?.result?.height_m||1.7));$('rigOffset').value=0;$('rigSymmetry').checked=true;changeRig();};
 $('rigHeight').oninput=changeRig;$('rigOffset').oninput=changeRig;
 $('rigHeightNumber').oninput=()=>{const input=$('rigHeightNumber');if(!input.validity.valid||!input.value)return;$('rigHeight').value=input.value;changeRig();};
 $('cancelRigSetup').onclick=()=>{if(rigStep===1){savedLandmarks=rigSetup.getLandmarks();rigSetup.endMarkers();showRigStep(0);}else closeRig();};
 $('resetRigSetup').onclick=()=>{if(rigStep===1){rigSetup.resetMarkers();return;}savedLandmarks=null;rotation.forEach(s=>s.value=0);$('rigHeight').value=1.7;$('rigOffset').value=0;changeRig();};
 $('rigSymmetry').onchange=()=>rigSetup.setSymmetry($('rigSymmetry').checked);
 $('confirmRigSetup').onclick=async()=>{
  const options=rigOptions(),j=getJob(),button=$('confirmRigSetup');
  if(!j||j.id!==rigJobId){closeRig();return;}
  if(rigStep===0){
   if(!$('rigHeightNumber').reportValidity())return;
   $('animationNotice').textContent='';showRigStep(1);
   rigSetup.startMarkers({points:savedLandmarks,symmetry:$('rigSymmetry').checked,onChange:(points,key)=>{
    $('rigSelectedPoint').textContent=key.replaceAll('_',' ')+' · '+points[key].map(v=>(v*options.height_m).toFixed(3)).join(', ')+' m';
   }});return;
  }
  options.landmarks=rigSetup.getLandmarks();button.disabled=true;$('animationNotice').textContent='';
  try{
   const status=await api('/api/status');
   if(!status.features?.includes('rig_landmarks'))throw Error('Restart TRELLIS to enable manual joint placement before creating this rig.');
   if(getJob()?.id!==j.id||rigJobId!==j.id)return;
   const result=await api(`/api/jobs/${j.id}/rig`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(options)});
   closeRig();await startJob(result.id,{rig:true});
  }catch(error){$('animationNotice').textContent=error.message;}
  finally{button.disabled=false;render();}
 };
 function renderAdded(job,clips,query){
  const list=clips.filter(clip=>clip.title.toLocaleLowerCase('en').includes(query));
  $('animationAvailableCount').textContent=`${list.length} ${list.length===1?'clip':'clips'}`;
  for(const clip of list){
   const row=document.createElement('article');row.className='added-motion';
   const heading=document.createElement('div');heading.className='added-motion-heading';
   const title=document.createElement('h3');title.textContent=clip.title;
   heading.append(icon('motion'),title);
   if(Number.isFinite(clip.duration)&&clip.duration>0){
    const time=document.createElement('span');
    time.textContent=clip.duration.toLocaleString('en-US',{maximumFractionDigits:2})+' s';
    heading.append(time);
   }
   const actions=document.createElement('div');actions.className='added-motion-actions';
   const play=document.createElement('button');play.type='button';play.className='secondary has-icon';
   play.append(icon('play'),document.createTextNode('Play on character'));
   play.setAttribute('aria-label',`Play ${clip.title} on character`);
   play.disabled=!clip.playable;
   play.onclick=()=>playClip(clip.index,clip.expectedName);
   actions.append(play);
   if(removeAvailable){
    const remove=document.createElement('button');remove.type='button';remove.className='quiet has-icon';remove.dataset.removeClip=clip.index;
    remove.setAttribute('aria-label',`Remove ${clip.title}`);
    remove.append(icon('trash'),document.createTextNode('Remove'));
    remove.onclick=()=>confirmRemoval(job,clip);
    actions.append(remove);
   }
   row.append(heading,actions);
   $('animationCards').append(row);
  }
  if(!list.length){
   const p=document.createElement('p');p.className='hint';
   p.textContent=clips.length?'No added clips match your search.':'This model has no embedded animations.';
   $('animationCards').append(p);
  }
 }

 function render(){
  const job=getJob(),rigged=hasRig(job),done=job?.state==='done';
  if(selectionTarget!==job?.id||!batchAvailable)resetSelection();
  selectionTarget=job?.id;
  $('animationModel').textContent=nameOf(job);$('createRig').hidden=rigged;
  const bones=job?.result?.bones||job?.media?.bones;
  $('animationRigState').textContent=rigged?(bones?`${bones} bones · ready to animate`:'Rigged · ready to animate'):done?'Add a rig to use the motion library.':'Open a completed model from the collection.';
  const q=$('animationSearch').value.toLocaleLowerCase('en'),added=embeddedClips(job),addedTab=tab==='added';
  $('animationAddedCount').textContent=added.length;
  $('animationDurationControl').hidden=addedTab;
  $('animationCategory').hidden=addedTab;
  $('animationSearch').placeholder=addedTab?'Search added clips…':'Search animations…';
  $('animationCards').classList.toggle('added-motion-list',addedTab);
  showClipNotice(job);
  previews.clear();$('animationCards').replaceChildren();
  if(addedTab){
   renderAdded(job,added,q);
   updateSelectionControls(job);
   return;
  }
  const list=catalog.filter(c=>c.name.toLocaleLowerCase('en').includes(q)&&(!$('animationCategory').value||c.category===$('animationCategory').value));
  $('animationAvailableCount').textContent=list.length+' motions';
  for(const clip of list){
   const present=added.find(c=>c.recipeId===clip.id),card=document.createElement('article');card.className='animation-card motion-'+clip.id;
   const preview=document.createElement('div');preview.className='motion-preview';previews.attach(preview,clip);preview.append(icon('motion'));const chip=document.createElement('span');chip.className='motion-chip';chip.textContent=present?'✓ ADDED':'3D PREVIEW';preview.append(chip);
   const title=document.createElement('h3');title.textContent=clip.name;const text=document.createElement('p');text.textContent=present&&Number.isFinite(present.duration)?`Added · ${present.duration.toLocaleString('en-US',{maximumFractionDigits:2})} s`:clip.description;
   card.append(preview,title,text);
   if(selecting){
    const label=document.createElement('label');label.className='motion-select';
    const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.dataset.selectClip=clip.id;
    checkbox.setAttribute('aria-label',`Select ${clip.name}`);
    checkbox.onchange=()=>{
     if(checkbox.checked)selectedClips.add(clip.id);else selectedClips.delete(clip.id);
     updateSelectionControls(getJob());
    };
    label.append(checkbox,document.createElement('span'));
    card.append(label);
   }else{
    const add=document.createElement('button');add.type='button';add.className=present?'secondary':'primary';add.dataset.clip=clip.id;
    add.append(icon(present?'version':'plus'),document.createTextNode(present?'Update':'Add to model'));
    add.onclick=()=>submitMotions([clip.id]);
    card.append(add);
   }
   if(present)card.classList.add('added');$('animationCards').append(card);
  }
  if(!list.length){const p=document.createElement('p');p.className='hint';p.textContent='No motions match your search.';$('animationCards').append(p);}
  updateSelectionControls(job);
 }
 for(const name of ['library','added'])$('animationTab'+(name==='library'?'Library':'Added')).onclick=()=>{tab=name;for(const t of ['Library','Added'])$('animationTab'+t).classList.toggle('active',t.toLowerCase()===name);render();};
 $('animationSearch').oninput=render;$('animationCategory').onchange=render;
 let signature='';
 return {
  closeRig,
  label(name){
   const clip=authoredMotion(name);
   return clip?`${clip.title} · ${clip.duration.toLocaleString('en-US')} s`:name;
  },
  setCapabilities(status){
   batchAvailable=status.features?.includes('animation_batch')===true;
   removeAvailable=status.features?.includes('animation_clip_removal')===true;
  },
  modelLoaded(){
   if(tab==='added'||!Array.isArray(getJob()?.media?.animation_items))render();
  },
  update(){
   const job=getJob();
   if(rigJobId&&rigJobId!==job?.id)closeRig();
   const key=JSON.stringify([job?.id,job?.state,job?.result?.bones,job?.result?.rigged,job?.result?.revision,
    job?.media?.bones,job?.media?.animation_items,job?.media?.animation_names,job?.media?.animation_revision,isBusy(),rigSetup?.ready,batchAvailable,removeAvailable]);
   if(signature!==key){signature=key;render();}
  },
  render,
 };
}
