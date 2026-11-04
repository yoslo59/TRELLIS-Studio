"""Import geometry and render a thumbnail in a private, factory-startup process."""
from pathlib import Path
import bpy, json, math, shutil, sys
from mathutils import Vector

job = Path(sys.argv[sys.argv.index('--')+1])
settings = json.loads((job/'settings.json').read_text(encoding='utf-8'))
source = job/settings['original_file']
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.preferences.filepaths.use_scripts_auto_execute = False
suffix = source.suffix.lower()
if suffix == '.glb': bpy.ops.import_scene.gltf(filepath=str(source))
elif suffix == '.fbx': bpy.ops.import_scene.fbx(filepath=str(source), use_anim=True, use_image_search=False)
elif suffix == '.obj': bpy.ops.wm.obj_import(filepath=str(source))
elif suffix == '.stl': bpy.ops.wm.stl_import(filepath=str(source))
elif suffix == '.ply': bpy.ops.wm.ply_import(filepath=str(source))
else: raise ValueError('Format non pris en charge.')
scene = bpy.context.scene
armatures=[o for o in scene.objects if o.type=='ARMATURE']
display_shapes={p.custom_shape for armature in armatures for p in armature.pose.bones if p.custom_shape}
for helper in display_shapes:bpy.data.objects.remove(helper,do_unlink=True)
for armature in armatures:armature.data.pose_position='REST'
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == 'MESH' and len(o.data.vertices)]
if not meshes: raise ValueError('Aucun maillage dans ce fichier.')
vertices = sum(len(o.data.vertices) for o in meshes)
triangles = sum(len(p.vertices)-2 for o in meshes for p in o.data.polygons)
if not triangles: raise ValueError('Le fichier ne contient aucune face de maillage.')
if triangles > 5_000_000: raise ValueError('Le modèle dépasse cinq millions de triangles.')
points = [o.matrix_world @ Vector(corner) for o in meshes for corner in o.bound_box]
low = Vector(tuple(min(p[i] for p in points) for i in range(3)))
high = Vector(tuple(max(p[i] for p in points) for i in range(3)))
size = high-low
if not all(math.isfinite(c) for c in (*low, *high)) or max(size) <= 0: raise ValueError('Dimensions invalides.')
images = [i for i in bpy.data.images if i.type == 'IMAGE' and i.size[0]]
result = dict(imported=True, vertices=vertices, triangles=triangles, height_m=round(size.z,5), dimensions_m=list(size),
              texture_size=max((max(i.size) for i in images),default=0), materials=len(bpy.data.materials),
              bones=sum(len(o.data.bones) for o in scene.objects if o.type=='ARMATURE'), animations=len(bpy.data.actions),
              source_format=suffix[1:].upper(), units='Dimensions du fichier ; aucune remise à l’échelle automatique')
for armature in armatures:armature.data.pose_position='POSE'
bpy.context.view_layer.update()
if suffix == '.glb':
    # Preserve the actual source bytes, including all skins, clips and extensions.
    shutil.copyfile(source, job/'model.glb')
else:
    bpy.ops.export_scene.gltf(filepath=str(job/'model.glb'), export_format='GLB', export_animations=True, export_yup=True, export_cameras=False, export_lights=False)
(job/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
bpy.ops.file.pack_all()
bpy.context.preferences.filepaths.save_version=0
bpy.ops.wm.save_as_mainfile(filepath=str(job/'model.blend'))
# Camera and lights affect the preview only, after the portable model is written.
for obj in list(scene.objects):
    if obj.type in ('CAMERA','LIGHT'): bpy.data.objects.remove(obj,do_unlink=True)
scene.render.engine='CYCLES'
scene.cycles.device='CPU'; scene.cycles.samples=8
scene.render.resolution_x=400; scene.render.resolution_y=400; scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'; scene.render.filepath=str(job/'input.png')
scene.world=bpy.data.worlds.new('Studio import');scene.world.use_nodes=True
scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.12,.15,.13,1)
scene.world.node_tree.nodes['Background'].inputs[1].default_value=.65
center=(low+high)/2; extent=max(size)
bpy.ops.object.camera_add(location=center+Vector((.9,-1.5,.7)).normalized()*extent*2.25)
camera=bpy.context.object;camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.type='ORTHO';camera.data.ortho_scale=extent*1.45;scene.camera=camera
for offset,power,scale in [((2,-3,4),140,3),((-3,-1,2),90,2),((0,3,3),180,2)]:
    bpy.ops.object.light_add(type='AREA',location=center+Vector(offset)*extent)
    light=bpy.context.object;light.data.energy=power*extent*extent;light.data.shape='DISK';light.data.size=scale*extent
    light.rotation_euler=(center-light.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.render.render(write_still=True)
