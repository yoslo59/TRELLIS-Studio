import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {createLightingRig} from './viewer-lighting.js?v=3.2.0';
export async function createPreparationPreview(container,url){
 const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.toneMapping=THREE.ACESFilmicToneMapping;container.prepend(renderer.domElement);const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(40,1,.001,1000),controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=false;const room=new RoomEnvironment(),pmrem=new THREE.PMREMGenerator(renderer),env=pmrem.fromScene(room,.04);scene.environment=env.texture;room.dispose();pmrem.dispose();const grid=new THREE.GridHelper(10,40,0x63765b,0x3b4735);grid.material.transparent=true;grid.material.opacity=.2;scene.add(grid);const rig=createLightingRig(scene,renderer),axes=new THREE.AxesHelper(.2);axes.material.depthTest=false;axes.renderOrder=12;scene.add(axes);const box=new THREE.Box3Helper(new THREE.Box3(),0xc4f58a);box.visible=false;scene.add(box);let root=null,closed=false,mixer=null,clip=null,clock=new THREE.Clock(),dots=[],contactReport=null;
 const observer=new ResizeObserver(()=>{const w=Math.max(1,container.clientWidth),h=Math.max(1,container.clientHeight);renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();});observer.observe(container);
 function frame(){const b=new THREE.Box3().setFromObject(root,true),s=b.getSize(new THREE.Vector3()),c=b.getCenter(new THREE.Vector3()),d=Math.max(s.x,s.y,s.z,.01)*1.9;camera.position.copy(c).add(new THREE.Vector3(.7,.3,1).multiplyScalar(d));camera.near=Math.max(.00001,d/1000);camera.far=Math.max(100,d*100);camera.updateProjectionMatrix();controls.target.copy(c);controls.update();axes.scale.setScalar(Math.max(s.y,1));rig.fit(root);}
 renderer.setAnimationLoop(()=>{if(closed)return;if(mixer&&clip)mixer.update(Math.min(clock.getDelta(),.1));else clock.getDelta();if(root&&box.visible)box.box.setFromObject(root,true);if(contactReport&&clip){const u=clip.time/clip.getClip().duration;dots.forEach(({mesh,points,contacts})=>{const i=Math.min(points.length-1,Math.round(u*(points.length-1)));mesh.position.fromArray(points[i]);mesh.material.color.set(contacts[i]?0xc4f58a:0x7392b0);});}renderer.render(scene,camera);});
 let gltf;try{gltf=await new GLTFLoader().loadAsync(url);if(closed)return;root=gltf.scene;scene.add(root);frame();}catch(e){observer.disconnect();renderer.setAnimationLoop(null);controls.dispose();rig.dispose();axes.geometry.dispose();axes.material.dispose();grid.geometry.dispose();grid.material.dispose();box.geometry.dispose();box.material.dispose();env.dispose();renderer.dispose();renderer.domElement.remove();throw e;}
 const edgeLines = [];
 function showEdges(overlay){
  for(const line of edgeLines.splice(0)){scene.remove(line);line.geometry.dispose();line.material.dispose();}
  if(!overlay)return;
  for(const [kind,color] of [['boundary',0xf8c865],['nonmanifold',0xf685b7]]){
   const vertices=overlay.positions[kind];
   if(!vertices.length)continue;
   const geometry=new THREE.BufferGeometry();
   geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));
   const material=new THREE.LineBasicMaterial({color,depthTest:false,transparent:true,opacity:.9});
   const line=new THREE.LineSegments(geometry,material);
   line.renderOrder=14;scene.add(line);edgeLines.push(line);
  }
 }
 return {get root(){return root;},clips:gltf.animations,frame,showEdges,
  game(options){root.position.set(0,0,0);root.scale.setScalar(options.scale);root.rotation.set(...options.rotation.map(THREE.MathUtils.degToRad),'ZYX');root.updateMatrixWorld(true);const b=new THREE.Box3().setFromObject(root,true),c=b.getCenter(new THREE.Vector3());if(options.pivot!=='source')root.position.set(-c.x,-(options.pivot==='ground'?b.min.y:c.y),-c.z);root.position.add(new THREE.Vector3(...options.offset));root.updateMatrixWorld(true);box.visible=options.collider!=='none';box.box.setFromObject(root,true);rig.fit(root);},
  play(index){mixer?.stopAllAction();mixer=new THREE.AnimationMixer(root);clip=mixer.clipAction(gltf.animations[index]).play();clock.getDelta();},
  contacts(report){for(const d of dots){scene.remove(d.mesh);d.mesh.geometry.dispose();d.mesh.material.dispose();}dots=[];contactReport=report;for(const foot of Object.values(report?.contacts||{})){const height=new THREE.Box3().setFromObject(root,true).getSize(new THREE.Vector3()).y;const mesh=new THREE.Mesh(new THREE.SphereGeometry(Math.max(.005,height*.013),12,8),new THREE.MeshBasicMaterial({color:0xc4f58a,depthTest:false}));mesh.renderOrder=15;scene.add(mesh);dots.push({mesh,points:foot.positions,contacts:foot.contact});}},
  dispose(){closed=true;observer.disconnect();renderer.setAnimationLoop(null);controls.dispose();root?.traverse(o=>{o.geometry?.dispose();for(const m of Array.isArray(o.material)?o.material:[o.material])if(m){for(const value of Object.values(m))if(value?.isTexture)value.dispose();m.dispose();}});this.contacts(null);showEdges(null);rig.dispose();axes.geometry.dispose();axes.material.dispose();grid.geometry.dispose();grid.material.dispose();box.geometry.dispose();box.material.dispose();env.dispose();renderer.dispose();renderer.domElement.remove();}}
}
