import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {clone} from 'three/addons/utils/SkeletonUtils.js';
export function createMotionPreviews(){
 let renderer;try{renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});}catch{return {attach(){},clear(){}};}
 renderer.setSize(240,270,false);renderer.toneMapping=THREE.ACESFilmicToneMapping;
 const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(32,240/270,.01,20);camera.position.set(1.85,1.35,3.05);camera.lookAt(0,.86,0);
 scene.add(new THREE.HemisphereLight(0xeef7ff,0x404c35,3));const light=new THREE.DirectionalLight(0xfff5dd,3);light.position.set(2,4,3);scene.add(light);const rim=new THREE.DirectionalLight(0xd4efbb,2);rim.position.set(-2,2,-2);scene.add(rim);
 const floor=new THREE.Mesh(new THREE.CylinderGeometry(.6,.64,.025,56),new THREE.MeshStandardMaterial({color:0x44533b,roughness:.9}));floor.position.y=-.025;scene.add(floor);
 const cache=new Map();let cards=[],last=0;
 const load=url=>{if(!cache.has(url))cache.set(url,new GLTFLoader().loadAsync(url));return cache.get(url);};
 function render(card,time){if(!card.model)return;scene.add(card.model);card.mixer.setTime(time%Math.max(card.clipDuration,.001));renderer.render(scene,camera);card.context.clearRect(0,0,240,270);card.context.drawImage(renderer.domElement,0,0);scene.remove(card.model);}
 function tick(time){requestAnimationFrame(tick);if(time-last<90||document.hidden)return;last=time;for(const card of cards){const rect=card.canvas.getBoundingClientRect();if(rect.bottom>0&&rect.top<innerHeight)render(card,time/1000);}}
 requestAnimationFrame(tick);
 return {attach(container,clip){const canvas=document.createElement('canvas');canvas.width=240;canvas.height=270;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Animated preview: '+clip.name);container.append(canvas);const card={canvas,context:canvas.getContext('2d')};cards.push(card);
  load(clip.preview_url).then(gltf=>{if(!cards.includes(card))return;const model=clone(gltf.scene),mixer=new THREE.AnimationMixer(model),action=gltf.animations.find(c=>c.name===clip.source_clip);if(!action)return;card.model=model;card.mixer=mixer;card.clipDuration=action.duration;mixer.clipAction(action).play();render(card,.25);}).catch(()=>{container.dataset.error='Preview unavailable';});
 },clear(){cards.forEach(c=>c.mixer?.stopAllAction());cards=[];}};
}
