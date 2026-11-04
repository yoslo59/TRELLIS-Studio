import * as THREE from 'three';
// One shared renderer for the visible cards; previews use the actual clip recipes.
export function createMotionPreviews(){
 let renderer;try{renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});}catch{return {attach(){},clear(){}};}
 renderer.setSize(240,270,false);renderer.setPixelRatio(1);renderer.toneMapping=THREE.ACESFilmicToneMapping;
 const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(32,240/270,.01,20);camera.position.set(1.9,1.45,3.0);camera.lookAt(0,.87,0);
 scene.add(new THREE.HemisphereLight(0xe9f6ff,0x4b5947,3));const key=new THREE.DirectionalLight(0xfff5dc,4);key.position.set(2,4,3);scene.add(key);const rim=new THREE.DirectionalLight(0xc6edb8,2);rim.position.set(-3,2,-2);scene.add(rim);
 const mat=new THREE.MeshStandardMaterial({color:0xd9e2ce,roughness:.36,metalness:.12}),dark=new THREE.MeshStandardMaterial({color:0x56634d,roughness:.9});
 const root=new THREE.Group(),joints={};scene.add(root);
 function ball(parent,position,scale){const mesh=new THREE.Mesh(new THREE.SphereGeometry(1,20,12),mat);mesh.position.set(...position);mesh.scale.set(...scale);parent.add(mesh);return mesh;}
 function capsule(parent,length,radius){const mesh=new THREE.Mesh(new THREE.CapsuleGeometry(radius,Math.max(.001,length-2*radius),6,14),mat);mesh.position.y=-length/2;parent.add(mesh);ball(parent,[0,-length,0],[radius*.92,radius*.92,radius*.92]);}
 function joint(name,parent,position){const group=new THREE.Group();group.position.set(...position);parent.add(group);joints[name]=group;return group;}
 ball(root,[0,0,0],[.19,.13,.12]);const spine=joint('spine',root,[0,.10,0]);ball(spine,[0,.19,0],[.245,.29,.13]);ball(spine,[0,.40,0],[.18,.12,.115]);const head=joint('head',spine,[0,.53,0]);ball(head,[0,.08,0],[.097,.13,.095]);ball(spine,[0,.46,0],[.06,.09,.06]);
 for(const [side,sign] of [['left',1],['right',-1]]){
  const arm=joint(side+'_arm',spine,[sign*.255,.32,0]);ball(arm,[0,0,0],[.064,.072,.065]);capsule(arm,.31,.053);const forearm=joint(side+'_forearm',arm,[0,-.31,0]);capsule(forearm,.285,.045);ball(forearm,[0,-.325,0],[.043,.067,.027]);
  const leg=joint(side+'_leg',root,[sign*.105,-.04,0]);capsule(leg,.43,.073);const shin=joint(side+'_shin',leg,[0,-.43,0]);capsule(shin,.40,.055);const foot=joint(side+'_foot',shin,[0,-.40,0]);ball(foot,[0,-.035,.065],[.057,.048,.12]);
 }
 const floor=new THREE.Mesh(new THREE.CylinderGeometry(.57,.62,.035,56),dark);floor.position.y=.005;scene.add(floor);const ring=new THREE.Mesh(new THREE.RingGeometry(.60,.605,64),new THREE.MeshBasicMaterial({color:0x99b47d,transparent:true,opacity:.22,side:THREE.DoubleSide}));ring.rotation.x=-Math.PI/2;ring.position.y=.027;scene.add(ring);
 let cards=[],last=0;const quat=new THREE.Quaternion(),axisX=new THREE.Vector3(1,0,0),axisY=new THREE.Vector3(0,1,0),axisZ=new THREE.Vector3(0,0,1);
 function angles(target,values){target.quaternion.identity();for(const [axis,value]of[[axisX,values[0]],[axisZ,-values[1]],[axisY,values[2]]])target.quaternion.premultiply(quat.setFromAxisAngle(axis,THREE.MathUtils.degToRad(value)));}
 function renderCard(card,now){const frames=card.clip.preview;if(!frames?.length)return;const index=Math.floor((now/3000)%1*(frames.length-1)),[poses,offset,rootAngles]=frames[index];
   Object.values(joints).forEach(g=>g.quaternion.identity());root.position.set(offset[0]*1.7,.94+offset[2]*1.7,-offset[1]*1.7);angles(root,rootAngles);for(const [semantic,value]of Object.entries(poses))if(joints[semantic])angles(joints[semantic],value);
   renderer.render(scene,camera);card.context.clearRect(0,0,240,270);card.context.drawImage(renderer.domElement,0,0);
 }
 function draw(now){requestAnimationFrame(draw);if(now-last<90||document.hidden)return;last=now;
  for(const card of cards){if(!card.canvas.isConnected)continue;const rect=card.canvas.getBoundingClientRect();if(rect.bottom<0||rect.top>innerHeight)continue;renderCard(card,now);}
 }
 requestAnimationFrame(draw);
 return {attach(container,clip){const canvas=document.createElement('canvas');canvas.width=240;canvas.height=270;canvas.setAttribute('aria-label','Animated preview: '+clip.name);canvas.setAttribute('role','img');container.append(canvas);const card={canvas,clip,context:canvas.getContext('2d')};cards.push(card);renderCard(card,500);},clear(){cards=[];}};
}
