"""Authored humanoid library retargeting in a private Blender process.

Geometric bone mapping is deliberately conservative. A non-humanoid keeps its
UniRig skeleton, without attaching inappropriate walk/run/crouch clips.
"""
from pathlib import Path
import bpy, json, math, sys
from mathutils import Vector, Quaternion
sys.path.insert(0,str(Path(__file__).resolve().parent))
from retarget_motions import author as author_natural_motions

job=Path(sys.argv[sys.argv.index('--')+1]);work=job/'rig-work'
settings=json.loads((job/'settings.json').read_text(encoding='utf-8'))
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(work/'rigged.glb'))
scene=bpy.context.scene;scene.render.fps=24;scene.frame_start=0
arms=[o for o in scene.objects if o.type=='ARMATURE']
if len(arms)!=1: raise ValueError('Ce premier outil nécessite un seul squelette par modèle.')
arm=arms[0];bones=list(arm.data.bones)
meshes=[o for o in scene.objects if o.type=='MESH']
arm.data.pose_position='REST';bpy.context.view_layer.update()
for obj in list(meshes):
    if obj.name.startswith('Icosphere') and len(obj.data.vertices)==42 and not any(m.type=='ARMATURE' for m in obj.modifiers):
        meshes.remove(obj);bpy.data.objects.remove(obj,do_unlink=True)
points=[o.matrix_world@Vector(c) for o in meshes for c in o.bound_box]
low=Vector(tuple(min(p[i] for p in points) for i in range(3)))
high=Vector(tuple(max(p[i] for p in points) for i in range(3)));height=high.z-low.z
arm.data.pose_position='POSE';bpy.context.view_layer.update()
if height<=0: raise ValueError('Hauteur du personnage invalide.')
world={b.name:(arm.matrix_world@b.head_local,arm.matrix_world@b.tail_local) for b in bones}
roots=[b for b in bones if not b.parent]
mapping={};warning=''
try:
    if len(roots)!=1: raise ValueError('Le squelette possède plusieurs racines.')
    root=roots[0];cx=world[root.name][0].x
    def limb(sign,kind):
        candidates=[]
        for b in bones:
            h,t=world[b.name];v=t-h
            if sign*(h.x-cx)<height*.025 or v.length<height*.065: continue
            children=[c for c in b.children if (world[c.name][1]-world[c.name][0]).length>height*.065]
            if not children: continue
            c=max(children,key=lambda c:(world[c.name][1]-world[c.name][0]).length)
            ch,ct=world[c.name]
            if kind=='leg':
                if not (low.z+height*.32<h.z<low.z+height*.68 and v.z<-height*.14 and ct.z<ch.z-height*.13):continue
                score=abs((h.z-low.z)/height-.54)+abs(h.x-cx)/height*.2
            else:
                if not (low.z+height*.57<h.z<low.z+height*.9 and abs(h.x-cx)>height*.07 and sign*v.x>height*.065 and abs(v.z)<height*.23 and sign*(ct.x-ch.x)>height*.06):continue
                score=abs(h.x-cx)/height+abs(v.z)/height
            candidates.append((score,b,c))
        if not candidates: raise ValueError('Membres humanoïdes non reconnus avec assez de confiance.')
        _,upper,lower=min(candidates,key=lambda x:x[0]);return upper,lower
    for label,sign in [('left',1),('right',-1)]:
        for kind in ('arm','leg'):
            upper,lower=limb(sign,kind)
            mapping[label+'_'+kind]=upper.name;mapping[label+'_'+('forearm' if kind=='arm' else 'shin')]=lower.name
        shin=arm.data.bones[mapping[label+'_shin']]
        if shin.children:mapping[label+'_foot']=max(shin.children,key=lambda b:b.length).name
    if len(set(mapping.values()))!=len(mapping): raise ValueError('Correspondance ambiguë entre les membres.')
    left_parent=arm.data.bones[mapping['left_leg']].parent
    right_parents=[arm.data.bones[mapping['right_leg']],*arm.data.bones[mapping['right_leg']].parent_recursive]
    while left_parent and left_parent not in right_parents:left_parent=left_parent.parent
    if left_parent:root=left_parent
    mapping['root']=root.name
    for side in ('left','right'):
        upper=arm.data.bones[mapping[side+'_arm']]
        if upper.parent and upper.parent.name!=root.name:mapping[side+'_clavicle']=upper.parent.name
        forearm=arm.data.bones[mapping[side+'_forearm']]
        if forearm.children:mapping[side+'_hand']=max(forearm.children,key=lambda b:b.length).name
        foot=arm.data.bones.get(mapping.get(side+'_foot',''))
        if foot and foot.children:mapping[side+'_toe']=max(foot.children,key=lambda b:b.length).name

    central=[b for b in bones if abs(world[b.name][0].x-cx)<height*.12 and world[b.name][0].z>world[root.name][0].z+height*.04]
    if central:
        mapping['spine']=min(central,key=lambda b:abs(world[b.name][0].z-(world[root.name][0].z+height*.14))).name
        mapping['head']=max(central,key=lambda b:world[b.name][0].z).name
        head=arm.data.bones[mapping['head']]
        chain=[b for b in reversed(head.parent_recursive) if b!=root and abs(world[b.name][0].x-cx)<height*.12]
        if len(chain)>=4:
            mapping['spine_1']=chain[0].name;mapping['spine_2']=chain[1].name;mapping['spine']=chain[2].name;mapping['neck']=chain[-1].name


except ValueError as error:
    warning=str(error);mapping={}

rest={p.name:(p.location.copy(),p.rotation_quaternion.copy(),p.scale.copy()) for p in arm.pose.bones}
for p in arm.pose.bones:p.rotation_mode='QUATERNION'
def reset():
    for p in arm.pose.bones:p.location=Vector();p.rotation_quaternion=Quaternion();p.scale=Vector((1,1,1))
def rotate(name,axis,degrees):
    p=arm.pose.bones[name];basis=arm.matrix_world.to_3x3()@p.bone.matrix_local.to_3x3()
    return Quaternion((basis.inverted()@Vector(axis)).normalized(),math.radians(degrees))
def apply(name,axis,degrees):arm.pose.bones[name].rotation_quaternion=rotate(name,axis,degrees)@arm.pose.bones[name].rotation_quaternion
def drop_arm(name,sign):
    b=arm.data.bones[name];h,t=world[name];direction=(t-h).normalized()
    target=Vector((sign*.22,0,-.98)).normalized();q=direction.rotation_difference(target)
    basis=(arm.matrix_world.to_3x3()@b.matrix_local.to_3x3()).to_quaternion()
    arm.pose.bones[name].rotation_quaternion=basis.inverted()@q@basis

requests=settings.get('animation_clips',[])
if 'regenerate_ids' in settings:requests=[r for r in requests if r['id'] in settings['regenerate_ids']]
authored=[]
if mapping and requests:
    authored=author_natural_motions(scene,arm,mapping,requests,height)
elif not mapping:
    warning+=' Humanoid joints could not be identified confidently.'
selected=[arm]+meshes
bpy.ops.object.select_all(action='DESELECT')
for o in selected:o.select_set(True)
bpy.context.view_layer.objects.active=arm
args=dict(filepath=str(job/'model.glb'),export_format='GLB',use_selection=True,export_animations=True,export_skins=True,
          export_animation_mode='ACTIONS',export_force_sampling=True,export_frame_range=False,export_anim_single_armature=True,export_yup=True)
supported=bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
bpy.ops.export_scene.gltf(**{k:v for k,v in args.items() if k in supported})
bpy.ops.file.pack_all()
bpy.context.preferences.filepaths.save_version=0
bpy.ops.wm.save_as_mainfile(filepath=str(job/'model.blend'))
if 'previous_animation_set' in settings:
    replaced={r['id'] for r in requests};authored=[r for r in settings['previous_animation_set'] if r['id'] not in replaced]+authored
result=dict(bones=len(bones),vertices=sum(len(o.data.vertices) for o in meshes),triangles=sum(len(p.vertices)-2 for o in meshes for p in o.data.polygons),
            height_m=height,animation_set=authored,humanoid_mapping=mapping,animation_warning=warning,
            limitations='Authored Quaternius motions retargeted to this rig. Review foot contacts, proportions, clothing and transitions on each character.',rigged=True)
(job/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print('ANIMATION_SET',json.dumps(result,ensure_ascii=False),flush=True)
