import {icon} from './card-menu.js?v=2.6.1';
import {createMotionPreviews} from './authored-previews.js?v=2.6';
export async function createAnimationStudio({api,getJob,isBusy,startJob,rigPreview}){
 const $=id=>document.getElementById(id),data=await api('/api/animations');
 let tab='library',catalog=data.animations;const previews=createMotionPreviews();
 const categories=[...new Set(catalog.map(c=>c.category))];
 for(const category of categories){const option=document.createElement('option');option.value=category;option.textContent=category;$('animationCategory').append(option);}
 const nameOf=j=>j?.metadata?.display_name||j?.settings.name||'Choose a model from the collection';
 $('animationDuration').oninput=()=>{$('animationDurationValue').value=Number($('animationDuration').value).toLocaleString('en-US')+' s';};
 async function run(path,body){$('animationNotice').textContent='';try{const result=await api(path,{method:'POST',...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});await startJob(result.id,{rig:path.endsWith('/rig')});}catch(e){$('animationNotice').textContent=e.message;}render();}
 const rotation=[];
 for(const axis of ['X','Y','Z']){const label=document.createElement('label');label.className='rig-rotation';const caption=document.createElement('span');caption.textContent='Rotation '+axis;const slider=document.createElement('input');slider.type='range';slider.min=-180;slider.max=180;slider.step=1;slider.value=0;slider.setAttribute('aria-label','Rotation '+axis);const value=document.createElement('output');value.value='0°';const reset=document.createElement('button');reset.type='button';reset.className='quiet';reset.append(icon('rotate'));reset.setAttribute('aria-label','Reset rotation '+axis);reset.onclick=()=>{slider.value=0;changeRig();};slider.oninput=changeRig;label.append(caption,slider,value,reset);rotation.push(slider);$('rigRotationControls').append(label);}
 function rigOptions(){return {rotation:rotation.map(s=>Number(s.value)),height_m:Number($('rigHeight').value),offset_m:Number($('rigOffset').value)};}
 function changeRig(){rotation.forEach(s=>s.nextElementSibling.value=s.value+'°');$('rigHeightValue').value=Number($('rigHeight').value).toLocaleString('en-US',{minimumFractionDigits:2})+' m';$('rigOffsetValue').value=Number($('rigOffset').value).toLocaleString('en-US')+' m';rigPreview(rigOptions());}
 function closeRig(){rigPreview(null);$('rigSetup').hidden=true;$('animationLibraryControls').hidden=false;}
 $('createRig').onclick=()=>{$('rigSetup').hidden=false;$('animationLibraryControls').hidden=true;rotation.forEach(s=>s.value=0);$('rigHeight').value=Math.min(3,Math.max(.2,getJob()?.result?.height_m||1.7));$('rigOffset').value=0;changeRig();};
 $('rigHeight').oninput=changeRig;$('rigOffset').oninput=changeRig;
 $('cancelRigSetup').onclick=closeRig;$('resetRigSetup').onclick=()=>{rotation.forEach(s=>s.value=0);$('rigHeight').value=1.7;$('rigOffset').value=0;changeRig();};
 $('confirmRigSetup').onclick=()=>{const options=rigOptions(),j=getJob();closeRig();if(j)run(`/api/jobs/${j.id}/rig`,options);};
 function render(){
  const job=getJob(),busy=isBusy(),hasRig=!!(job?.result?.bones||job?.result?.rigged),done=job?.state==='done';
  $('animationModel').textContent=nameOf(job);$('createRig').hidden=hasRig;$('createRig').disabled=!done||busy;
  $('animationRigState').textContent=hasRig?`${job.result.bones} bones · ready to animate`:done?'Add a rig to use the motion library.':'Open a completed model from the collection.';
  const q=$('animationSearch').value.toLocaleLowerCase('en'),added=job?.settings.animation_clips||[];
  $('animationAddedCount').textContent=added.length;
  const list=catalog.filter(c=>c.name.toLocaleLowerCase('en').includes(q)&&(!$('animationCategory').value||c.category===$('animationCategory').value)&&(tab==='library'||added.some(a=>a.id===c.id)));
  previews.clear();$('animationCards').replaceChildren();$('animationAvailableCount').textContent=list.length+' motions';
  for(const clip of list){
   const present=added.find(c=>c.id===clip.id),card=document.createElement('article');card.className='animation-card motion-'+clip.id;
   const preview=document.createElement('div');preview.className='motion-preview';previews.attach(preview,clip);preview.append(icon('motion'));const chip=document.createElement('span');chip.className='motion-chip';chip.textContent=present?'✓ ADDED':'3D PREVIEW';preview.append(chip);
   const title=document.createElement('h3');title.textContent=clip.name;const text=document.createElement('p');text.textContent=present?`Added · ${present.duration.toLocaleString('en-US')} s`:clip.description;
   const add=document.createElement('button');add.type='button';add.className=present?'secondary':'primary';add.append(icon(present?'version':'plus'),document.createTextNode(present?'Update':'Add to model'));add.disabled=!done||!hasRig||busy;add.dataset.clip=clip.id;
   add.onclick=()=>run(`/api/jobs/${getJob().id}/animations`,{clip:clip.id,duration:Number($('animationDuration').value)});
   card.append(preview,title,text,add);if(present)card.classList.add('added');$('animationCards').append(card);
  }
  if(!list.length){const p=document.createElement('p');p.className='hint';p.textContent=tab==='added'?'No animations have been added to this model.':'No motions match your search.';$('animationCards').append(p);}
 }
 for(const name of ['library','added'])$('animationTab'+(name==='library'?'Library':'Added')).onclick=()=>{tab=name;for(const t of ['Library','Added'])$('animationTab'+t).classList.toggle('active',t.toLowerCase()===name);render();};
 $('animationSearch').oninput=render;$('animationCategory').onchange=render;
 let signature='';return {label(name){const match=/^TS_(.+)_([0-9.]+)s$/.exec(name);return match?(catalog.find(c=>c.id===match[1])?.name||match[1])+' · '+Number(match[2]).toLocaleString('en-US')+' s':name;},update(){const job=getJob(),key=JSON.stringify([job?.id,job?.state,job?.result?.bones,job?.settings.animation_clips,isBusy()]);if(signature!==key){signature=key;render();}},render};
}
