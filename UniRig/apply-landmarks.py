"""Apply user landmarks between UniRig skeleton prediction and skin inference."""
from pathlib import Path
import json,sys,os
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[key]='2'
import bpy
import numpy as np
from mathutils import Matrix,Vector
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rig_landmarks import validate_rig_options
from rig_joint_placement import place_joints
from humanoid_mapping import infer
from skeleton_alignment import mesh_points,align


def main(job):
    work=job/'rig-work'
    options=validate_rig_options(json.loads((job/'settings.json').read_text())['rig_options'])
    if not options.get('landmarks'):raise ValueError('No landmark placement requested.')
    source=work/'input.glb' if (work/'input.glb').exists() else job/'source.glb'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source));target=mesh_points(bpy.context.scene)
    low=Vector(target.min(0));high=Vector(target.max(0))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(work/'skeleton.fbx'),use_image_search=False)
    arms=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
    if len(arms)!=1:raise ValueError('Landmark placement requires one predicted humanoid skeleton.')
    arm=arms[0];bone_list=list(arm.data.bones);names=[b.name for b in bone_list]
    parents=[names.index(b.parent.name) if b.parent else None for b in bone_list]
    predicted=np.array([list(arm.matrix_world@b.head_local)+list(arm.matrix_world@b.tail_local) for b in bone_list])
    aligned,matrix=align(mesh_points(bpy.context.scene),target,predicted)
    original_world=arm.matrix_world.copy()
    arm.matrix_world=Matrix(matrix.tolist())@original_world
    mapping=infer(arm,low,high)
    arm.matrix_world=original_world
    adjusted,report=place_joints(aligned,parents,names,mapping,options)
    inverse=np.linalg.inv(matrix)
    corrected=adjusted.reshape(-1,3)@inverse[:3,:3].T+inverse[:3,3]
    corrected=corrected.reshape(-1,6)
    bpy.ops.object.select_all(action='DESELECT');arm.select_set(True);bpy.context.view_layer.objects.active=arm
    bpy.ops.object.mode_set(mode='EDIT');local=arm.matrix_world.inverted()
    for b in arm.data.edit_bones:b.use_connect=False
    for name,coords in zip(names,corrected):
        b=arm.data.edit_bones[name]
        b.head=local@Vector(coords[:3]);b.tail=local@Vector(coords[3:])
    bpy.ops.object.mode_set(mode='OBJECT')
    # Meshes and original prediction stay untouched; skin uses this new file.
    bpy.ops.object.select_all(action='SELECT')
    output=work/'skeleton-landmarks.fbx'
    bpy.ops.export_scene.fbx(filepath=str(output),use_selection=True,add_leaf_bones=False,bake_anim=False,
                             object_types={'ARMATURE','MESH'},path_mode='AUTO')
    report.update(version=1,applied=True,skin_input=output.name,mapping=mapping,landmarks=options['landmarks'],
                  normalization_to_model=matrix.tolist(),height_m=options['height_m'],offset_m=options['offset_m'])
    (work/'landmarks-report.json').write_text(json.dumps(report,indent=2))
    print('User joint landmarks applied before skin inference',flush=True)


if __name__=='__main__':main(Path(sys.argv[1]).resolve())
