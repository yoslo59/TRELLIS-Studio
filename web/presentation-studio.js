import * as THREE from 'three';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {clone as cloneSkeleton} from 'three/addons/utils/SkeletonUtils.js';
import {renderTurntable} from './turntable-webm.js?v=3.3.5';
import {button,dialog,el,field,input,select,download} from './workbench-ui.js?v=3.3.5';

const cardCapture = {width: 384, height: 384, seconds: 6, fps: 12, bitrate: 360000};
const cardMaxBytes = 6 * 1024 * 1024;

export function createPresentationStudio({api,getJob,getViewer,refresh,canUseCardTurntables=()=>false}){
  const ui=dialog('presentationDialog','Present your model','CAMERA & LIGHT',
    'Capture the current angle, pose, materials and preview lighting.','presentation-dialog');
  const size=select([['1024','1024 × 1024'],['1920','1920 × 1080'],['2048','2048 × 2048']],'1024');
  const transparent=input('checkbox',''),duration=select([['6','6 seconds'],['10','10 seconds']],'6');
  transparent.checked=true;
  const preview=el('img','capture-preview');
  preview.alt='Captured model preview';
  preview.hidden=true;
  const progress=el('progress');
  progress.max=100;
  progress.hidden=true;
  const thumbnail=button('Save thumbnail','image');
  const png=button('Capture PNG','camera');
  const video=button('Record turntable','rotate','primary');
  const cancel=button('Cancel recording','close');
  cancel.hidden=true;
  ui.foot.append(thumbnail,png,video,cancel);

  const library=el('section','presentation-library');
  const libraryHint=el('p','hint');
  const libraryButtons=el('div','presentation-library-actions');
  const libraryVideo=button('Save card turntable','rotate');
  const useLastVideo=button('Use last recording','play');
  const removeLibrary=button('Remove preview','trash');
  libraryButtons.append(libraryVideo,useLastVideo,removeLibrary);
  library.append(el('strong','','Library preview'),libraryHint,libraryButtons);
  ui.body.append(field('Image resolution',size),field('Transparent PNG background',transparent),
    field('Turntable duration',duration),library,preview,progress);
  let running=false,cancelled=false,objectUrl=null,openedJobId=null;

  function enable(on,cancellable=false){
    for(const control of [thumbnail,png,video,libraryVideo,useLastVideo,removeLibrary,size,transparent,duration]){
      control.disabled=!on;
    }
    cancel.hidden=on||!cancellable;
  }

  function syncLibrary(presentation={}){
    library.hidden=!canUseCardTurntables();
    const saved=presentation.library_turntable;
    const last=presentation.turntable;
    removeLibrary.hidden=!saved;
    useLastVideo.hidden=!(last?.bytes>0&&last.bytes<=cardMaxBytes);
    const state=saved?'Saved on this card. ':'';
    libraryHint.textContent=state+'A small, silent preview plays on hover or when you press Play.';
  }

  function captureRenderer(job,width,height){
    const view=getViewer();
    if(!view.model||!view.scene||view.loadedId!==job.id)throw Error('Wait for this model to load before recording.');
    const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true,preserveDrawingBuffer:true});
    renderer.setSize(width,height,false);
    renderer.setPixelRatio(1);
    renderer.toneMapping=view.renderer.toneMapping;
    renderer.toneMappingExposure=view.renderer.toneMappingExposure;
    renderer.shadowMap.enabled=view.renderer.shadowMap.enabled;
    renderer.shadowMap.type=view.renderer.shadowMap.type;
    const camera=view.camera.clone();
    camera.aspect=width/height;
    camera.updateProjectionMatrix();
    // A render-target texture belongs to its WebGL context. Rebuild the same
    // studio environment for capture and keep the live viewer independent.
    const scene=new THREE.Scene(),pmrem=new THREE.PMREMGenerator(renderer),room=new RoomEnvironment();
    const environment=pmrem.fromScene(room,.04);
    room.dispose();
    pmrem.dispose();
    scene.environment=environment.texture;
    scene.environmentIntensity=view.scene.environmentIntensity;
    scene.environmentRotation.copy(view.scene.environmentRotation);
    scene.background=transparent.checked?null:new THREE.Color('#252c27');
    const model=cloneSkeleton(view.model);
    scene.add(model);
    for(const light of view.scene.children.filter(o=>o.isLight)){
      const copy=light.clone();
      if(light.target){copy.target=light.target.clone();scene.add(copy.target);}
      scene.add(copy);
    }
    const restore=()=>{scene.traverse(o=>{if(o.isLight)o.shadow?.dispose();});environment.dispose();renderer.dispose();};
    return {view,scene,model,renderer,camera,restore};
  }

  function recipe({view,camera},target,dimensions){
    return {
      camera:{position:camera.position.toArray(),quaternion:camera.quaternion.toArray(),target:target.toArray()},
      lighting:view.lighting?.settings||{},resolution:dimensions.width,transparent:transparent.checked,
      ...dimensions,
    };
  }

  async function save(job,kind,blob,settings){
    const form=new FormData();
    form.set('kind',kind);
    form.set('recipe',JSON.stringify(settings));
    const extension=blob.type==='video/webm'?'webm':'png';
    form.set('file',blob,kind+'.'+extension);
    return api('/api/jobs/'+job.id+'/presentation',{method:'POST',body:form});
  }

  function imageDimensions(){
    const width=Number(size.value);
    return {width,height:width===1920?1080:width};
  }

  function selectedJob(){
    const job=getJob();
    if(job?.state==='done'&&job.id===openedJobId)return job;
    ui.notice.textContent='Reopen Present model for the selected asset.';
    return null;
  }

  async function image(kind){
    if(running)return;
    const job=selectedJob();
    if(!job)return;
    running=true;
    enable(false);
    let capture;
    try{
      const dimensions=imageDimensions();
      capture=captureRenderer(job,dimensions.width,dimensions.height);
      capture.renderer.render(capture.scene,capture.camera);
      const blob=await new Promise(resolve=>capture.renderer.domElement.toBlob(resolve,'image/png'));
      if(!blob)throw Error('This browser could not capture the model.');
      await save(job,kind,blob,recipe(capture,capture.view.controls.target,dimensions));
      if(objectUrl)URL.revokeObjectURL(objectUrl);
      objectUrl=URL.createObjectURL(blob);
      preview.src=objectUrl;
      preview.hidden=false;
      if(kind==='capture')download(blob,(job.metadata?.display_name||'Model')+'.png');
      ui.notice.textContent=kind==='thumbnail'?'Thumbnail saved to the model card.':'PNG saved and downloaded.';
      await refresh();
    }catch(e){ui.notice.textContent=e.message;}
    finally{capture?.restore();running=false;enable(true);}
  }

  async function turntable(forCard){
    if(running)return;
    const job=selectedJob();
    if(!job)return;
    const options=forCard?cardCapture:{...imageDimensions(),seconds:Number(duration.value),fps:24};
    running=true;
    cancelled=false;
    enable(false,true);
    progress.hidden=false;
    progress.value=0;
    let capture;
    try{
      capture=captureRenderer(job,options.width,options.height);
      capture.scene.background=new THREE.Color('#252c27');
      const center=capture.view.controls.target.clone();
      const start=capture.camera.position.clone().sub(center);
      if(forCard){
        const bounds=new THREE.Box3().setFromObject(capture.model,true).getBoundingSphere(new THREE.Sphere());
        center.copy(bounds.center);
        if(start.lengthSq()===0)start.set(1,.5,1);
        const distance=Math.max(.01,bounds.radius)/Math.sin(THREE.MathUtils.degToRad(capture.camera.fov/2))*1.08;
        start.setLength(distance);
        capture.camera.near=Math.max(.001,distance/1000);
        capture.camera.far=Math.max(100,distance*4);
        capture.camera.updateProjectionMatrix();
        capture.camera.position.copy(center).add(start);
        capture.camera.lookAt(center);
      }
      const settings=recipe(capture,center,{width:options.width,height:options.height,fps:options.fps,
        duration:options.seconds,transparent:false});
      const axis=new THREE.Vector3(0,1,0);
      ui.notice.textContent=forCard?'Rendering the library preview…':'Rendering the turntable · 24 fps…';
      const blob=await renderTurntable({...options,canvas:capture.renderer.domElement,cancelled:()=>cancelled,
        progress:value=>progress.value=value,draw:u=>{
          capture.camera.position.copy(start).applyAxisAngle(axis,u*Math.PI*2).add(center);
          capture.camera.lookAt(center);
          capture.renderer.render(capture.scene,capture.camera);
        }});
      if(!blob||cancelled){
        ui.notice.textContent='Recording cancelled.';
        return;
      }
      const manifest=await save(job,forCard?'library_turntable':'turntable',blob,settings);
      if(!forCard)download(blob,(job.metadata?.display_name||'Model')+'-turntable.webm');
      syncLibrary(manifest);
      ui.notice.textContent=forCard?'Turntable saved to the model card.':'Turntable saved and downloaded · 24 fps.';
      await refresh();
    }catch(e){ui.notice.textContent=e.message;}
    finally{capture?.restore();running=false;progress.hidden=true;enable(true);}
  }

  async function changeLibrary(method){
    if(running)return;
    const job=selectedJob();
    if(!job)return;
    running=true;
    enable(false);
    ui.notice.textContent='';
    try{
      const manifest=await api('/api/jobs/'+job.id+'/presentation/library-turntable',{method});
      syncLibrary(manifest);
      ui.notice.textContent=method==='POST'?'Last recording saved to the model card.':'Card turntable removed.';
      await refresh();
    }catch(e){ui.notice.textContent=e.message;}
    finally{running=false;enable(true);}
  }

  thumbnail.onclick=()=>image('thumbnail');
  png.onclick=()=>image('capture');
  video.onclick=()=>turntable(false);
  libraryVideo.onclick=()=>turntable(true);
  useLastVideo.onclick=()=>changeLibrary('POST');
  removeLibrary.onclick=()=>changeLibrary('DELETE');
  cancel.onclick=()=>{cancelled=true;};
  ui.element.addEventListener('cancel',event=>{if(running){event.preventDefault();cancelled=true;}});
  ui.element.addEventListener('close',()=>{cancelled=true;});
  return {open(){
    const job=getJob();
    if(running||job?.state!=='done')return;
    openedJobId=job.id;
    preview.hidden=true;
    syncLibrary(job.presentation);
    ui.show();
  },get recording(){return running;}};
}
