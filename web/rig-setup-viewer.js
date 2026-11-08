import * as THREE from 'three';

export const RIG_MARKERS = [
  ['chin', 'Chin', '#6bd7e8', 0, .87],
  ['left_shoulder', 'Left shoulder', '#e6d56a', .10, .80],
  ['right_shoulder', 'Right shoulder', '#e6d56a', -.10, .80],
  ['left_elbow', 'Left elbow', '#8fdf83', .24, .77],
  ['right_elbow', 'Right elbow', '#8fdf83', -.24, .77],
  ['left_wrist', 'Left wrist', '#d990d9', .37, .75],
  ['right_wrist', 'Right wrist', '#d990d9', -.37, .75],
  ['groin', 'Groin', '#e2e8db', 0, .51],
  ['left_knee', 'Left knee', '#e8a365', .055, .28],
  ['right_knee', 'Right knee', '#e8a365', -.055, .28],
  ['left_ankle', 'Left ankle', '#b6d76b', .055, .055],
  ['right_ankle', 'Right ankle', '#b6d76b', -.055, .055],
];

// These are editable starting positions, not an inferred skeleton.
export function initialRigLandmarks() {
  return Object.fromEntries(RIG_MARKERS.map(([key,,, x, y]) => [key, [x, y]]));
}

export function createRigSetupViewer({scene, camera, controls, viewport, getModel, stopRotation}) {
  const helpers = new THREE.Group();
  helpers.name = 'Rig preparation helpers';
  const overlay = document.createElement('div');
  overlay.className = 'rig-landmark-overlay';
  overlay.hidden = true;
  viewport.append(overlay);
  const legend = document.createElement('span');
  legend.className = 'rig-scale-label';
  legend.textContent = 'Reference · 1.70 m';
  overlay.append(legend);
  let base = null, options = null, points = null, markerMode = false, symmetric = true, onChange = null;
  let drag = null, selected = 'chin';
  const buttons = new Map(), ray = new THREE.Raycaster();
  const dragPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  const mannequin = new THREE.Group();
  // A flat silhouette is only a scale reference; it never enters the asset.
  const rightOutline = [
    [0,1.70],[.046,1.693],[.071,1.666],[.076,1.624],[.064,1.575],
    [.040,1.546],[.039,1.507],[.095,1.488],[.161,1.468],[.183,1.432],
    [.205,1.332],[.225,1.222],[.235,1.125],[.255,1.021],[.266,.927],
    [.260,.881],[.244,.867],[.229,.888],[.221,.936],[.213,.996],
    [.194,1.084],[.178,1.184],[.150,1.282],[.135,1.343],
    [.129,1.235],[.113,1.119],[.140,1.021],[.151,.938],
    [.145,.822],[.131,.653],[.123,.514],[.137,.383],[.121,.235],
    [.105,.085],[.111,.038],[.121,.016],[.101,.002],[.063,0],
    [.047,.023],[.052,.087],[.061,.239],[.059,.381],[.053,.507],
    [.034,.699],[.028,.852],[0,.902],
  ];
  const outline = [...rightOutline, ...rightOutline.slice(1,-1).reverse().map(([x,y])=>[-x,y])];
  const curve = new THREE.CatmullRomCurve3(outline.map(([x,y])=>new THREE.Vector3(x,y,0)),true,'centripetal');
  const shape = new THREE.Shape(curve.getPoints(512).map(p=>new THREE.Vector2(p.x,p.y)));
  const silhouetteGeometry = new THREE.ShapeGeometry(shape);
  silhouetteGeometry.computeBoundingBox();
  const bounds=silhouetteGeometry.boundingBox, silhouetteScale=1.7/(bounds.max.y-bounds.min.y);
  silhouetteGeometry.translate(0,-bounds.min.y,0);silhouetteGeometry.scale(silhouetteScale,silhouetteScale,1);
  const silhouette = new THREE.Mesh(silhouetteGeometry,new THREE.MeshBasicMaterial({
    color:0x9aa391,transparent:true,opacity:.32,depthWrite:false,side:THREE.DoubleSide,
  }));
  mannequin.add(silhouette);
  helpers.add(mannequin);
  const ruler = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(-.31,0,0),new THREE.Vector3(-.31,1.7,0),
    new THREE.Vector3(-.35,0,0),new THREE.Vector3(-.27,0,0),
    new THREE.Vector3(-.35,1.7,0),new THREE.Vector3(-.27,1.7,0),
  ]),new THREE.LineBasicMaterial({color:0x9cb284}));
  mannequin.add(ruler);
  function emit() { onChange?.(getLandmarks(), selected); }
  function move(key, value) {
    points[key] = [Math.max(-1,Math.min(1,value[0])),Math.max(0,Math.min(1.05,value[1]))];
    if (symmetric && /^(left|right)_/.test(key)) {
      const other = key.replace(/^(left|right)_/, key.startsWith('left_') ? 'right_' : 'left_');
      points[other] = [-points[key][0], points[key][1]];
    } else if (symmetric && (key === 'chin' || key === 'groin')) points[key][0] = 0;
    selected = key; emit(); update();
  }
  for (const [key, label, color] of RIG_MARKERS) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'rig-landmark'; button.style.setProperty('--marker-color', color);
    button.title = label + ' · drag or use arrow keys'; button.setAttribute('aria-label', button.title);
    button.textContent = key.startsWith('left_') ? 'L' : key.startsWith('right_') ? 'R' : '•';
    button.dataset.landmark = key; button.hidden = true;
    button.onpointerdown = event => {
      if (!markerMode) return;
      event.preventDefault(); event.stopPropagation(); selected = key; emit();
      button.setPointerCapture(event.pointerId); drag = {key, id:event.pointerId}; controls.enabled = false;
    };
    button.onpointermove = event => {
      if (!drag || drag.key !== key) return;
      const rect = viewport.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),camera);
      const hit = ray.ray.intersectPlane(dragPlane,new THREE.Vector3());
      if (hit) move(key,[hit.x/options.height_m,(hit.y-options.offset_m)/options.height_m]);
    };
    const end = () => { drag = null; if (base) controls.enabled = base.controlsEnabled; };
    button.onpointerup = end; button.onpointercancel = end; button.onlostpointercapture = end;
    button.onfocus = () => {selected = key; emit();};
    button.onkeydown = event => {
      const delta = {ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,1],ArrowDown:[0,-1]}[event.key];
      if (!delta || !points) return;
      event.preventDefault(); const step=event.shiftKey ? .01 : .002;
      move(key,points[key].map((v,i)=>v+delta[i]*step));
    };
    overlay.append(button); buttons.set(key,button);
  }
  function frame() {
    if (!base) return;
    const box = new THREE.Box3().setFromObject(base.model,true);
    if (!markerMode) box.union(new THREE.Box3().setFromObject(helpers,true));
    const center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3());
    const halfFov=THREE.MathUtils.degToRad(camera.fov/2);
    const distance=Math.max(size.y/2/Math.tan(halfFov),size.x/2/Math.tan(halfFov)/camera.aspect)*1.25;
    camera.up.set(0,1,0);camera.position.set(center.x,center.y,center.z+distance);
    camera.near=.001;camera.far=Math.max(100,distance*100);camera.updateProjectionMatrix();
    controls.minDistance=distance*.1;controls.maxDistance=distance*8;controls.target.copy(center);controls.update();update();
  }
  function preview(next) {
    if (!next) return close();
    const model=getModel(); if (!model) return false;
    const first=!base;
    if (first) {
      base={model,position:model.position.clone(),quaternion:model.quaternion.clone(),scale:model.scale.clone(),
        cameraPosition:camera.position.clone(),cameraUp:camera.up.clone(),target:controls.target.clone(),
        controlsEnabled:controls.enabled,enableRotate:controls.enableRotate,
        near:camera.near,far:camera.far,minDistance:controls.minDistance,maxDistance:controls.maxDistance};
      stopRotation();scene.add(helpers);overlay.hidden=false;legend.hidden=false;controls.enableRotate=false;
    }
    options=next;
    model.position.copy(base.position);model.quaternion.copy(base.quaternion);model.scale.copy(base.scale);
    model.rotation.set(...next.rotation.map(THREE.MathUtils.degToRad));model.updateMatrixWorld(true);
    const box=new THREE.Box3().setFromObject(model,true),size=box.getSize(new THREE.Vector3());
    model.scale.multiplyScalar(next.height_m/Math.max(size.y,.00001));model.updateMatrixWorld(true);box.setFromObject(model,true);
    const center=box.getCenter(new THREE.Vector3());model.position.add(new THREE.Vector3(-center.x,-box.min.y+next.offset_m,-center.z));
    model.updateMatrixWorld(true);box.setFromObject(model,true);
    mannequin.position.x=box.min.x-.38;helpers.visible=!markerMode;
    frame();return true;
  }
  function startMarkers(config={}) {
    if (!base) return;
    markerMode=true;helpers.visible=false;legend.hidden=true;
    points=config.points?structuredClone(config.points):initialRigLandmarks();
    symmetric=config.symmetry??true;onChange=config.onChange;
    for (const button of buttons.values()) button.hidden=false;
    frame();emit();
  }
  function endMarkers() {
    markerMode=false;onChange=null;helpers.visible=!!base;legend.hidden=false;
    for (const button of buttons.values()) button.hidden=true;
    if (base) {controls.enabled=base.controlsEnabled;frame();}
  }
  function update() {
    if (!base) return;
    const rect=viewport.getBoundingClientRect();
    for (const [key,button] of buttons) {
      if (!markerMode) continue;
      const p=new THREE.Vector3(points[key][0]*options.height_m,points[key][1]*options.height_m+options.offset_m,0).project(camera);
      button.style.left=(p.x+1)*rect.width/2+'px';button.style.top=(1-p.y)*rect.height/2+'px';
      button.classList.toggle('selected',key===selected);
    }
    const label=new THREE.Vector3(mannequin.position.x,1.8,0).project(camera);
    legend.style.left=(label.x+1)*rect.width/2+'px';legend.style.top=(1-label.y)*rect.height/2+'px';
  }
  function getLandmarks() {return points?structuredClone(points):null;}
  function close() {
    if (!base) return;
    const saved=base;
    saved.model.position.copy(saved.position);saved.model.quaternion.copy(saved.quaternion);saved.model.scale.copy(saved.scale);
    camera.position.copy(saved.cameraPosition);camera.up.copy(saved.cameraUp);controls.target.copy(saved.target);
    controls.enabled=saved.controlsEnabled;controls.enableRotate=saved.enableRotate;
    camera.near=saved.near;camera.far=saved.far;camera.updateProjectionMatrix();
    controls.minDistance=saved.minDistance;controls.maxDistance=saved.maxDistance;controls.update();
    scene.remove(helpers);overlay.hidden=true;base=null;options=null;points=null;markerMode=false;drag=null;onChange=null;
    for (const button of buttons.values()) button.hidden=true;
  }
  return {preview,startMarkers,endMarkers,getLandmarks,update,close,frame,
    get active(){return !!base;},get ready(){return !!getModel();},setSymmetry(value){symmetric=value;},
    resetMarkers(){points=initialRigLandmarks();emit();update();}};
}
