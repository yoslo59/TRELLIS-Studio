// Recipes are persisted by the local server, independently from object history.
const $=id=>document.getElementById(id);
const steps={game_d:20,high_detail:20,humanoid_experimental:20,dense_master:20,fast:8,balanced:12,detail:20};
const startupKey='trellis.studio.startup-profile';
export async function createProfileControls(advanced,{changed=()=>{}}={}){
  let profiles=[],selected=null,saving=false,edit=false,sourceRequired=false,previousKeep=false;
  let repairSupported=false;
  let startupProfile='';
  function read(){const {texture_seed,...options}=advanced.read();return {preset:$('preset').value,resolution:Number($('resolution').value),
    triangles:Number($('triangles').value)||({game_d:28000,high_detail:250000,humanoid_experimental:250000,dense_master:1200000,fast:25000,balanced:50000,detail:100000}[$('preset').value]),
    texture_size:Number($('textureSize').value)||($('preset').value==='fast'?1024:2048),
    remove_bg:$('removeBg').checked,advanced:options};}
  function resolved(s){const copy=structuredClone(s);for(const key of ['structure_steps','shape_steps','texture_steps'])if(copy.advanced[key]===0)copy.advanced[key]=steps[copy.preset];return copy;}
  const equal=(a,b)=>{const x=resolved(a),y=resolved(b);return Object.keys(x).every(k=>k==='advanced'?Object.keys(x[k]).every(f=>x[k][f]===y[k][f]):x[k]===y[k]);};
  function sync(){
    if(!selected)return;
    const activePreset=$('pixalMode').checked?$('pixalProfileChoice').value:$('preset').value;
    const experimental=activePreset==='humanoid_experimental', dense=activePreset==='dense_master';
    const requiresSource=experimental||dense;
    if(requiresSource!==sourceRequired){
      if(requiresSource){previousKeep=$('keepFinishingSource').checked;$('keepFinishingSource').checked=true;}
      else $('keepFinishingSource').checked=previousKeep;
      sourceRequired=requiresSource;
    }
    $('keepFinishingSource').disabled=requiresSource;
    $('humanoidProfileHelp').hidden=!experimental;
    if($('denseMasterProfileHelp'))$('denseMasterProfileHelp').hidden=!dense;
    $('denseMasterRepairField').hidden=!dense;
    $('denseMasterRepair').disabled=!dense||!repairSupported;
    $('denseMasterRepairHint').textContent=repairSupported
      ?'Some fine edges may soften during repair.'
      :'Restart Studio to enable hole repair.';
    $('finishingSourceHelp').hidden=requiresSource;
    if(experimental&&!$('pixalMode').checked)$('assetType').value='humanoid';
    const recipe=read(),dirty=!equal(recipe,selected.settings);
    $('profileId').value=recipe.preset===selected.settings.preset?selected.id:'';
    $('profileState').textContent=dirty?'MODIFIED':selected.variant?'VARIANT':selected.experimental?'EXPERIMENTAL':selected.id==='dense_master'?'MASTER':selected.builtin?'REFERENCE':'CUSTOM';
    $('profileState').classList.toggle('dirty',dirty);
    $('profileSummary').textContent=`${recipe.resolution}³ · ${dense?'up to ':''}${recipe.triangles.toLocaleString('en-US')} triangles · texture ${recipe.texture_size/1024}K`;
    $('monoTrianglesLabel').textContent=dense?'Maximum triangles':'Target triangles';
    $('monoTrianglesHint').textContent=dense
      ?'0 = profile ceiling. Studio chooses a lighter mesh when it meets the surface tolerance.'
      :'0 = profile · up to 8,000,000. The result may be lower.';
    $('generationSummary').textContent=`${selected.name.replace(' · recommended','')}${dirty?' · modified':''} · local compute`;
    $('updateProfile').hidden=selected.builtin;
    const startup=$('useProfileAtStartup');
    const isStartup=selected.id===startupProfile;
    startup.disabled=!selected.id||dirty||isStartup;
    startup.setAttribute('aria-pressed',String(isStartup));
    startup.textContent=isStartup?'Startup profile':'Use at startup';
    if(![...$('profileBase').options].some(o=>o.value===recipe.preset)){const option=document.createElement('option');option.value=recipe.preset;option.textContent='Historical settings';$('profileBase').append(option);}$('profileBase').value=recipe.preset;
    changed();
  }
  function apply(id){
    selected=profiles.find(p=>p.id===id);if(!selected)return;
    $('profileChoice').querySelector('[data-variant]')?.remove();
    const s=selected.settings;
    for(const [key,value] of Object.entries({preset:s.preset,resolution:s.resolution,triangles:s.triangles,textureSize:s.texture_size}))$(key).value=String(value);
    const textureSeed=advanced.read().texture_seed;
    $('removeBg').checked=s.remove_bg;advanced.apply({...s.advanced,texture_seed:textureSeed});
    $('profileChoice').value=id;$('profileId').value=id;
    if(s.preset==='humanoid_experimental')$('assetType').value='humanoid';
    $('profileNotice').textContent='';sync();
  }
  async function request(url,options){const r=await fetch(url,options),data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Profiles unavailable.');return data;}
  function options(){
    $('profileChoice').replaceChildren();
    for(const builtin of [true,false]){
      const group=document.createElement('optgroup');group.label=builtin?'Built-in profiles':'My profiles';
      for(const p of profiles.filter(x=>x.builtin===builtin)){const o=document.createElement('option');o.value=p.id;o.textContent=p.name;group.append(o);}
      if(group.children.length)$('profileChoice').append(group);
    }
  }
  try{
    const data=await request('/api/profiles');profiles=data.profiles;
    let saved='';
    try{saved=localStorage.getItem(startupKey);}catch{}
    startupProfile=[saved,data.startup_profile,data.default_profile].find(id=>profiles.some(p=>p.id===id));
    options();apply(startupProfile);
    $('profileChoice').disabled=false;$('saveProfileAs').disabled=!advanced.ready;
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
  $('profileBase').addEventListener('change',()=>{$('preset').value=$('profileBase').value;sync();});
  function show(update){if(!selected)return;edit=update;$('profileName').value=update?selected.name:'';$('profileDialogTitle').textContent=update?'Edit this profile':'Save a copy';$('profileDialogError').textContent='';$('profileDialog').showModal();$('profileName').focus();}
  $('saveProfileAs').onclick=()=>show(false);$('updateProfile').onclick=()=>show(true);
  $('closeProfile').onclick=()=>$('profileDialog').close();
  $('profileForm').onsubmit=async e=>{
    e.preventDefault();if(saving||!selected)return;
    const invalid=[...$('form').querySelectorAll('input[type=number]')].find(x=>!x.checkValidity());
    if(invalid){$('profileDialogError').textContent='Correct the numeric fields before saving the profile.';return;}
    saving=true;$('confirmProfile').disabled=true;
    try{
      const profile=await request(edit?`/api/profiles/${selected.id}`:'/api/profiles',{
        method:edit?'PUT':'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:$('profileName').value,settings:read()})});
      const data=await request('/api/profiles');profiles=data.profiles;options();apply(profile.id);
      $('profileDialog').close();$('profileNotice').textContent='Profile saved on this computer.';
    }catch(e){$('profileDialogError').textContent=e.message;}
    finally{saving=false;$('confirmProfile').disabled=false;}
  };
  return {sync,reset:()=>apply('game_d'),
    setRepairCapability(available){
      if(repairSupported===available)return;
      repairSupported=available;
      sync();
    },
    restoreSource(value){sourceRequired=false;previousKeep=!!value;$('keepFinishingSource').checked=!!value;sync();},
    detach(){
      selected={id:'',name:'Variant',builtin:true,variant:true,settings:read()};
      $('profileChoice').querySelector('[data-variant]')?.remove();
      const option=document.createElement('option');option.value='';option.textContent='This model’s settings';option.disabled=true;option.dataset.variant='true';$('profileChoice').append(option);$('profileChoice').value='';
      $('profileId').value='';sync();$('profileNotice').textContent='Model settings restored. Save a copy to create a profile.';
    },
    get ready(){return profiles.length>0;}};
}
