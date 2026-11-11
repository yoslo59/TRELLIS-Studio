"""CPU FBX fixture for the installed UniRig landmark adapter.

Create with Blender --background --python this-file -- create JOB.
Run the adapter using UniRig's Python: UniRig/apply-landmarks.py JOB.
Then run this script with Blender -- ... -- verify JOB.
This exercises an actual normalized, rotated skeleton FBX, without inference.

For a complete CPU transfer test, also run Blender -- ... -- skin JOB to
create synthetic weights on the corrected FBX. Run UniRig/transfer-skin.py
with JOB/rig-work/{skin-fixture.fbx,input.glb,rigged.glb}, in that order.
Run UniRig/verify-landmarks.py JOB and repeat the Blender verify step.
"""
from pathlib import Path
import json,sys,runpy
import numpy as np
import bpy
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from rig_joint_placement import landmark_anchors
stage,folder=sys.argv[sys.argv.index('--')+1:];job=Path(folder).resolve();work=job/'rig-work'


def mesh_points():return np.array([list(o.matrix_world@v.co) for o in bpy.context.scene.objects if o.type=='MESH' for v in o.data.vertices])
def bone_points(arm):return np.array([list(arm.matrix_world@b.head_local)+list(arm.matrix_world@b.tail_local) for b in arm.data.bones])


if stage=='create':
    if job.exists():raise FileExistsError(job)
    work.mkdir(parents=True)
    bones,parents,names,mapping,options=runpy.run_path(str(ROOT/'tests/rig_landmarks.py'),run_name='fixture')['fixture']()
    (job/'settings.json').write_text(json.dumps({'rig_options':options}))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    def box(position,size):
        bpy.ops.mesh.primitive_cube_add(size=1,location=position);o=bpy.context.object;o.scale=size
        bpy.ops.object.transform_apply(location=True,rotation=True,scale=True)
    box((0,0,1.73),(.23,.20,.34));box((0,0,1.23),(.4,.23,.55));box((0,0,.95),(.28,.22,.2))
    for sign in (-1,1):
        box((sign*.5,.02,1.42),(.7,.12,.1));box((sign*.10,0,.55),(.13,.14,.8))
        box((sign*.10,-.06,.15),(.15,.26,.1))
    box((-.24,-.16,1.0),(.13,.13,.17))  # intentional asymmetry disambiguates yaw
    bpy.ops.object.select_all(action='SELECT');bpy.context.view_layer.objects.active=bpy.context.selected_objects[0]
    bpy.ops.object.join();obj=bpy.context.object
    # prepare_rig.py centres both horizontal axes before UniRig. Reproduce that
    # contract so the upstream merge orientation recovery is tested faithfully.
    target=mesh_points();horizontal=(target.min(0)+target.max(0))/2;horizontal[2]=0
    for vertex in obj.data.vertices:vertex.co-=Vector(horizontal)
    bones=(bones.reshape(-1,3)-horizontal).reshape(-1,6)
    # Reproduce two ancestors with world scale 1.69, as emitted when Studio
    # sizes a character whose imported mesh already belongs to a group.
    outer=bpy.data.objects.new('Studio orientation',None);bpy.context.collection.objects.link(outer)
    inner=bpy.data.objects.new('Source group',None);bpy.context.collection.objects.link(inner)
    outer.scale=(1.69,)*3;inner.parent=outer;obj.parent=inner
    for vertex in obj.data.vertices:vertex.co/=1.69
    outer.select_set(True);inner.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(work/'input.glb'),export_format='GLB',use_selection=True,export_animations=False)
    obj.parent=None
    for vertex in obj.data.vertices:vertex.co*=1.69
    bpy.data.objects.remove(inner,do_unlink=True);bpy.data.objects.remove(outer,do_unlink=True)
    bpy.context.view_layer.update()
    target=mesh_points();center=(target.min(0)+target.max(0))/2;scale=np.ptp(target,axis=0).max()/2
    matrix=np.eye(4);matrix[:3,:3]=np.diag([-scale,-scale,scale]);matrix[:3,3]=center
    inv=np.linalg.inv(matrix)
    for vertex in obj.data.vertices:
        vertex.co=Vector((np.asarray(vertex.co)-center)@inv[:3,:3].T)
    normalized=(bones.reshape(-1,3)-center)@inv[:3,:3].T;normalized=normalized.reshape(-1,6)
    bpy.ops.object.armature_add();arm=bpy.context.object;bpy.ops.object.mode_set(mode='EDIT')
    for old in list(arm.data.edit_bones):arm.data.edit_bones.remove(old)
    for i,(name,points) in enumerate(zip(names,normalized)):
        b=arm.data.edit_bones.new(name);b.head=points[:3];b.tail=points[3:]
        if parents[i] is not None:b.parent=arm.data.edit_bones[names[parents[i]]]
    bpy.ops.object.mode_set(mode='OBJECT');bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.fbx(filepath=str(work/'skeleton.fbx'),use_selection=True,add_leaf_bones=False,bake_anim=False,object_types={'ARMATURE','MESH'})
    (work/'fixture.json').write_text(json.dumps(dict(matrix=matrix.tolist(),mapping=mapping,names=names,world_bones=bones.tolist())))
    print('CREATED CPU normalized FBX fixture',job)
elif stage=='skin':
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(work/'skeleton-landmarks.fbx'))
    arm=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
    mesh=next(o for o in bpy.context.scene.objects if o.type=='MESH')
    modifier=mesh.modifiers.new('Fixture skin','ARMATURE');modifier.object=arm
    upper=mesh.vertex_groups.new(name='Head');lower=mesh.vertex_groups.new(name='Pelvis')
    for vertex in mesh.data.vertices:
        group=upper if (mesh.matrix_world@vertex.co).z>0 else lower
        group.add([vertex.index],1,'REPLACE')
    bpy.ops.export_scene.fbx(filepath=str(work/'skin-fixture.fbx'),add_leaf_bones=False,bake_anim=False,
                             object_types={'ARMATURE','MESH'})
elif stage=='verify':
    report=json.loads((work/'landmarks-report.json').read_text());options=json.loads((job/'settings.json').read_text())['rig_options']
    matrix=np.asarray(report['normalization_to_model'])
    bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.fbx(filepath=str(work/'skeleton.fbx'))
    before_mesh=mesh_points()
    bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.fbx(filepath=str(work/'skeleton-landmarks.fbx'))
    after_mesh=mesh_points();arm=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
    assert before_mesh.shape==after_mesh.shape and np.allclose(before_mesh,after_mesh,atol=2e-6),'Skeleton editing changed the mesh'
    names=[b.name for b in arm.data.bones];points=bone_points(arm).reshape(-1,3)
    aligned=points@matrix[:3,:3].T+matrix[:3,3]
    anchors=landmark_anchors(names,report['mapping'])
    fixture=json.loads((work/'fixture.json').read_text());expected=np.asarray(fixture['world_bones']).reshape(-1,3)
    for key,index in anchors.items():
        x,y=options['landmarks'][key]
        assert np.allclose(aligned[index,[0,2]],[x*options['height_m'],y*options['height_m']+options['offset_m']],atol=2e-5),(key,aligned[index])
        assert abs(aligned[index,1]-report['anchors'][key]['before'][1])<2e-5
        original_index=fixture['names'].index(report['anchors'][key]['bone'])*2+(index%2)
        assert np.allclose(report['anchors'][key]['before'],expected[original_index],atol=2e-5),(key,'wrong normalization/alignment')
    extracted=work/'skeleton-landmarks'/'raw_data.npz'
    if extracted.exists():
        with np.load(extracted,allow_pickle=True) as data:
            joints=np.stack([data['joints'],data['tails']],axis=1).reshape(-1,3)
            joints=joints@matrix[:3,:3].T+matrix[:3,3]
            for key,index in landmark_anchors(data['names'].tolist(),report['mapping']).items():
                assert np.linalg.norm(joints[index]-report['anchors'][key]['after'])<2e-5
        print('PASS: upstream extraction passes the corrected joint coordinates to skin input')
    if (work/'rigged.glb').exists():
        assert report['final_rig_verification']['passed'],'Final rig moved the requested landmarks'
        bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=str(work/'rigged.glb'))
        mesh=next(o for o in bpy.context.scene.objects if o.type=='MESH' and o.vertex_groups)
        for vertex in mesh.data.vertices:
            strongest=max(vertex.groups,key=lambda group:group.weight)
            expected_group='Head' if (mesh.matrix_world@vertex.co).z>matrix[2,3] else 'Pelvis'
            assert mesh.vertex_groups[strongest.group].name==expected_group,'Skin weights shifted across world space'
        print('PASS: nested non-unit target transforms preserve final landmarks and synthetic weights')
    print('PASS: corrected FBX round-trip preserves mesh, all 12 metre-space joint anchors and predicted depth')
elif stage=='prepare':
    options=json.loads((job/'settings.json').read_text())['rig_options']
    bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=str(work/'input.glb'))
    points=mesh_points();low=points.min(0);high=points.max(0)
    assert abs((high-low)[2]-options['height_m'])<2e-5
    assert abs(low[2]-options['offset_m'])<2e-5
    assert np.abs(((low+high)/2)[:2]).max()<2e-5
    print('PASS: rotated model matches exact requested height, ground offset and horizontal centring')
else:raise ValueError(stage)
