"""Undo UniRig skin's mesh+joint normalization before textured-model transfer.

The skin transform normalizes the union of mesh vertices and joint heads. A
manually placed finger can extend outside the mesh bounds, shifting/scaling
both. The upstream final merger assumes the original mesh-only frame. Restore
the exact input frame for both mesh and skeleton, keeping all skin weights.
"""
from pathlib import Path
import json,sys,os
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[key]='2'
import bpy
import numpy as np
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rig_joint_placement import skin_normalization_frame

job=Path(sys.argv[1]);work=job/'rig-work'
with np.load(work/'skeleton-landmarks'/'raw_data.npz',allow_pickle=True) as data:
    vertices=data['vertices'].astype(float);joints=data['joints'].astype(float)
center,scale=skin_normalization_frame(vertices,joints)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=str(work/'skin.fbx'),use_image_search=False)
arms=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
if len(arms)!=1:raise ValueError('Skin normalization recovery requires one armature.')
arm=arms[0]
for ob in [o for o in bpy.context.scene.objects if o.type=='MESH']:
    inverse=ob.matrix_world.inverted()
    for vertex in ob.data.vertices:
        world=np.asarray(ob.matrix_world@vertex.co);vertex.co=inverse@Vector(world*scale+center)
    ob.data.update()
bpy.ops.object.select_all(action='DESELECT');arm.select_set(True);bpy.context.view_layer.objects.active=arm
bpy.ops.object.mode_set(mode='EDIT');inverse=arm.matrix_world.inverted()
saved={bone.name:(arm.matrix_world@bone.head,arm.matrix_world@bone.tail) for bone in arm.data.edit_bones}
for bone in arm.data.edit_bones:bone.use_connect=False
for bone in arm.data.edit_bones:
    head,tail=saved[bone.name]
    bone.head=inverse@Vector(np.asarray(head)*scale+center)
    bone.tail=inverse@Vector(np.asarray(tail)*scale+center)
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.object.select_all(action='SELECT')
bpy.ops.export_scene.fbx(filepath=str(work/'skin-model-frame.fbx'),use_selection=True,add_leaf_bones=False,
                         bake_anim=False,object_types={'ARMATURE','MESH'})
report=json.loads((work/'landmarks-report.json').read_text())
report['skin_frame_restoration']=dict(center_normalized=center.tolist(),scale_normalized=scale,
    reason='Undo upstream skin normalization of mesh vertices plus joint heads',skin_weights_unchanged=True,
    source='skin.fbx',output='skin-model-frame.fbx')
(work/'landmarks-report.json').write_text(json.dumps(report,indent=2))
print('Restored skin to the original normalized mesh frame',center.tolist(),scale,flush=True)
