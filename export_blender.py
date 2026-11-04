"""Private DCC conversion: all clips, original geometry and explicit target units."""
from pathlib import Path
import bpy,json,sys
from mathutils import Vector,Matrix

args=sys.argv[sys.argv.index('--')+1:];folder=Path(args[0]);target=args[1]
config=json.loads((folder/'export-config.json').read_text(encoding='utf-8'))
bpy.ops.wm.read_factory_settings(use_empty=True);bpy.context.preferences.filepaths.use_scripts_auto_execute=False
bpy.ops.import_scene.gltf(filepath=str(folder/'.conversion.glb'))
scene=bpy.context.scene;arms=[o for o in scene.objects if o.type=='ARMATURE']
shapes={p.custom_shape for a in arms for p in a.pose.bones if p.custom_shape}
for helper in shapes:bpy.data.objects.remove(helper,do_unlink=True)
for obj in list(scene.objects):
    if obj.type in ('CAMERA','LIGHT'):bpy.data.objects.remove(obj,do_unlink=True)
for a in arms:a.data.pose_position='REST'
bpy.context.view_layer.update();meshes=[o for o in scene.objects if o.type=='MESH'];points=[o.matrix_world@Vector(c) for o in meshes for c in o.bound_box]
low=[min(v[i] for v in points) for i in range(3)];high=[max(v[i] for v in points) for i in range(3)]
for a in arms:a.data.pose_position='POSE'
scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
# glTF import keys are expressed in the current scene FPS. Keep that rate so
# a source clip's seconds survive FBX baking exactly.
for action in bpy.data.actions:action.use_fake_user=True
# Export a clean bind/rest pose as the default. The animation takes are baked
# separately; an active glTF NLA strip must not become the FBX's initial pose.
for obj in scene.objects:
    if obj.animation_data:
        obj.animation_data.action=None
        for track in obj.animation_data.nla_tracks:track.mute=True
for armature in arms:
    for bone in armature.pose.bones:bone.matrix_basis=Matrix.Identity(4)
bpy.context.view_layer.update()
bpy.ops.file.pack_all();bpy.context.preferences.filepaths.save_version=0
if target=='blender':bpy.ops.wm.save_as_mainfile(filepath=str(folder/'model.blend'))
else:
    # With units in FBX metadata Unity sees metre-sized transforms. For Unreal the
    # exporter multiplies coordinates by 100 and writes centimetre metadata.
    bpy.ops.export_scene.fbx(filepath=str(folder/'model.fbx'),use_selection=False,object_types={'MESH','ARMATURE','EMPTY'},
        global_scale=1,apply_unit_scale=True,apply_scale_options=config['coordinates']['fbx_scale_mode'],
        axis_forward=config['coordinates']['forward'],axis_up=config['coordinates']['up'],use_space_transform=True,bake_space_transform=False,
        add_leaf_bones=False,use_armature_deform_only=False,armature_nodetype='NULL',mesh_smooth_type='FACE',
        use_mesh_modifiers=True,bake_anim=bool(config['animations']),bake_anim_use_all_actions=True,bake_anim_use_nla_strips=False,
        bake_anim_simplify_factor=0,path_mode='RELATIVE',embed_textures=False)
report=dict(target=target,dimensions_m=[high[i]-low[i] for i in range(3)],height_m=high[2]-low[2],
    bones=sum(len(a.data.bones) for a in arms),actions=[dict(name=a.name,frames=list(a.frame_range)) for a in bpy.data.actions],
    triangles=sum(len(p.vertices)-2 for m in meshes for p in m.data.polygons),materials=[m.name for m in bpy.data.materials],fps=scene.render.fps)
(folder/'conversion-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

if (folder/'Collisions/collision.glb').is_file():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(folder/'Collisions/collision.glb'))
    bpy.context.scene.unit_settings.system='METRIC';bpy.context.scene.unit_settings.scale_length=1
    if target in ('unity','unreal'):
        bpy.ops.export_scene.fbx(filepath=str(folder/'Collisions/collision.fbx'),object_types={'MESH','EMPTY'},global_scale=1,apply_unit_scale=True,apply_scale_options=config['coordinates']['fbx_scale_mode'],axis_forward=config['coordinates']['forward'],axis_up=config['coordinates']['up'],use_space_transform=True,bake_space_transform=False,bake_anim=False,add_leaf_bones=False)
    else:
        bpy.ops.wm.save_as_mainfile(filepath=str(folder/'Collisions/collision.blend'))
