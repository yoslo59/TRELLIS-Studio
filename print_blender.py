"""Static, scaled voxel surface with millimetre STL and metre GLB/Blender outputs."""
from pathlib import Path
import bpy,bmesh,json,math,sys
from mathutils import Vector

JOB=Path(sys.argv[sys.argv.index('--')+1]);settings=json.loads((JOB/'settings.json').read_text(encoding='utf-8'))
options=settings['print_options'];height=options['height_mm']/1000
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.preferences.filepaths.use_scripts_auto_execute=False
bpy.ops.import_scene.gltf(filepath=str(JOB/'source.glb'))
scene=bpy.context.scene
armatures=[o for o in scene.objects if o.type=='ARMATURE']
display_shapes={p.custom_shape for a in armatures for p in a.pose.bones if p.custom_shape}
for helper in display_shapes:bpy.data.objects.remove(helper,do_unlink=True)
for a in armatures:a.data.pose_position='REST'
bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
sources=[o for o in scene.objects if o.type=='MESH' and len(o.data.polygons)]
if not sources:raise ValueError('The source has no surface.')
if sum(len(p.vertices)-2 for o in sources for p in o.data.polygons)>5_000_000:raise ValueError('The source exceeds five million triangles.')
combined=bmesh.new()
for source in sources:
    evaluated=source.evaluated_get(deps);part=bpy.data.meshes.new_from_object(evaluated,depsgraph=deps)
    part.transform(source.matrix_world);combined.from_mesh(part);bpy.data.meshes.remove(part)
for obj in list(scene.objects):bpy.data.objects.remove(obj,do_unlink=True)
mesh=bpy.data.meshes.new('Print surface');combined.to_mesh(mesh);combined.free()
obj=bpy.data.objects.new('Print copy',mesh);scene.collection.objects.link(obj)
bpy.context.view_layer.objects.active=obj;obj.select_set(True)

def resize_ground():
    low=Vector(tuple(min(v.co[i] for v in mesh.vertices) for i in range(3)))
    high=Vector(tuple(max(v.co[i] for v in mesh.vertices) for i in range(3)))
    if not all(math.isfinite(x) for x in (*low,*high)) or high.z-low.z<=1e-8:raise ValueError('The source has no measurable height.')
    factor=height/(high.z-low.z);origin=Vector(((low.x+high.x)/2,(low.y+high.y)/2,low.z))
    for v in mesh.vertices:v.co=(v.co-origin)*factor
    mesh.update();return (high-low)*factor
size=resize_ground()
requested=options['voxel_mm']/1000
# Bound CPU/memory cost on extremely wide or finely sampled models.
voxel=max(requested,max(size)/384)
modifier=obj.modifiers.new('Print surface reconstruction','REMESH');modifier.mode='VOXEL';modifier.voxel_size=voxel
modifier.adaptivity=0;modifier.use_smooth_shade=False
if hasattr(modifier,'use_remove_disconnected'):modifier.use_remove_disconnected=False
bpy.ops.object.modifier_apply(modifier=modifier.name)
mesh=obj.data
if not mesh.vertices or not mesh.polygons:raise ValueError('Reconstruction produced no surface. Thin/open source geometry needs manual preparation.')
bm=bmesh.new();bm.from_mesh(mesh)
bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=voxel*.0001)
bmesh.ops.dissolve_degenerate(bm,dist=voxel*.00001,edges=list(bm.edges))
loose=[v for v in bm.verts if not v.link_faces]
if loose:bmesh.ops.delete(bm,geom=loose,context='VERTS')
boundary_before=[edge for edge in bm.edges if edge.is_boundary]
patched=bmesh.ops.holes_fill(bm,edges=boundary_before,sides=32).get('faces',[]) if boundary_before else []
small_hole_faces=len(patched)
bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
if bm.calc_volume(signed=True)<0:bmesh.ops.reverse_faces(bm,faces=list(bm.faces))
bm.to_mesh(mesh);bm.free();resize_ground()
bm=bmesh.new();bm.from_mesh(mesh)
boundary=sum(e.is_boundary for e in bm.edges);nonmanifold=sum(not e.is_manifold for e in bm.edges)
bad_vertices=sum(not v.is_manifold for v in bm.verts)
remaining=set(bm.verts);shells=0
while remaining:
    pending=[remaining.pop()];shells+=1
    while pending:
        vertex=pending.pop()
        for edge in vertex.link_edges:
            other=edge.other_vert(vertex)
            if other in remaining:remaining.remove(other);pending.append(other)
volume=abs(bm.calc_volume(signed=True));bm.free()
warnings=['Review wall thickness, overhangs, supports and stability in your slicer. Textures and animations are not part of a single-material print.',
          'Voxel reconstruction can soften detail and merge nearby surfaces; thin parts may disappear.']
if shells>1:warnings.append(f'{shells} disconnected shells remain. Check for floating parts or internal cavities.')
if voxel>requested*1.001:warnings.append('Voxel size was increased to keep the reconstruction within the processing budget.')
report=dict(method='Blender voxel remesh',pose='Rest pose',requested_height_mm=options['height_mm'],
            requested_voxel_mm=options['voxel_mm'],effective_voxel_mm=voxel*1000,boundary_edges=boundary,
            nonmanifold_edges=nonmanifold,nonmanifold_vertices=bad_vertices,connected_shells=shells,
            small_hole_faces_added=small_hole_faces,
            volume_mm3=volume*1e9,source_parent=settings['parent_id'],warnings=warnings,
            checks_not_performed=['minimum wall thickness','self-intersections','printer tolerance','supports','stability'])
material=bpy.data.materials.new('Print preview');material.diffuse_color=(.64,.72,.55,1);material.use_nodes=True
material.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value=material.diffuse_color
material.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value=.62
mesh.materials.clear();mesh.materials.append(material)
for polygon in mesh.polygons:polygon.use_smooth=True
scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1;scene.unit_settings.length_unit='MILLIMETERS'
scene.frame_set(1);scene.render.fps=24
bpy.ops.export_scene.gltf(filepath=str(JOB/'model.glb'),export_format='GLB',use_selection=True,export_animations=False,export_yup=True,export_cameras=False,export_lights=False)
bpy.ops.wm.stl_export(filepath=str(JOB/'model.stl'),export_selected_objects=True,global_scale=1000,use_scene_unit=False,apply_modifiers=True,forward_axis='Y',up_axis='Z')
bpy.context.preferences.filepaths.save_version=0
bpy.ops.wm.save_as_mainfile(filepath=str(JOB/'model.blend'))
mesh.calc_loop_triangles();size=Vector(tuple(max(v.co[i] for v in mesh.vertices)-min(v.co[i] for v in mesh.vertices) for i in range(3)))
result=dict(print_copy=True,vertices=len(mesh.vertices),triangles=len(mesh.loop_triangles),height_m=size.z,dimensions_m=list(size),
            texture_size=0,materials=1,bones=0,animations=0,print_report=report)
(JOB/'print-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
(JOB/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
# Thumbnail only; camera and lights are deliberately absent from the editable model.
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=8
scene.render.resolution_x=400;scene.render.resolution_y=400;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.filepath=str(JOB/'input.png')
scene.world=bpy.data.worlds.new('Print studio');scene.world.use_nodes=True
scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.12,.15,.13,1)
scene.world.node_tree.nodes['Background'].inputs[1].default_value=.65
center=Vector((0,0,size.z/2));extent=max(size)
bpy.ops.object.camera_add(location=center+Vector((.9,-1.5,.7)).normalized()*extent*2.25)
camera=bpy.context.object;camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.type='ORTHO';camera.data.ortho_scale=extent*1.45;scene.camera=camera
for offset,power,scale in [((2,-3,4),140,3),((-3,-1,2),90,2),((0,3,3),180,2)]:
    bpy.ops.object.light_add(type='AREA',location=center+Vector(offset)*extent)
    light=bpy.context.object;light.data.energy=power*extent*extent;light.data.shape='DISK';light.data.size=scale*extent
    light.rotation_euler=(center-light.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.render.render(write_still=True)
