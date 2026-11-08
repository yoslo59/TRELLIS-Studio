"""Apply studio orientation/size to a private copy, using glTF Y-up controls."""
from pathlib import Path
import bpy,json,math,sys
from mathutils import Vector,Matrix
job=Path(sys.argv[sys.argv.index('--')+1]);options=json.loads((job/'settings.json').read_text(encoding='utf-8'))['rig_options']
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(job/'source.glb'))
roots=[o for o in bpy.context.scene.objects if not o.parent]
group=bpy.data.objects.new('Studio orientation',None);bpy.context.collection.objects.link(group)
for obj in roots:obj.parent=group
conversion=Matrix.Rotation(math.pi/2,4,'X')
x,y,z=[math.radians(v) for v in options['rotation']]
rotation=Matrix.Rotation(x,4,'X')@Matrix.Rotation(y,4,'Y')@Matrix.Rotation(z,4,'Z')
group.matrix_world=conversion@rotation@conversion.inverted()
def bounds():
    # Match Three.js Box3(..., precise=True) in rig preparation, including
    # arbitrary rotations. Rotated local box corners overestimate the mesh.
    bpy.context.view_layer.update();points=[o.matrix_world@v.co for o in bpy.context.scene.objects if o.type=='MESH' for v in o.data.vertices]
    if not points:raise ValueError('The character contains no mesh vertices.')
    low=Vector(tuple(min(p[i] for p in points) for i in range(3)));high=Vector(tuple(max(p[i] for p in points) for i in range(3)));return low,high
low,high=bounds();group.scale*=options['height_m']/max(high.z-low.z,.00001)
low,high=bounds();center=(low+high)/2;group.location+=Vector((-center.x,-center.y,-low.z+options['offset_m']))
bpy.ops.export_scene.gltf(filepath=str(job/'rig-work'/'input.glb'),export_format='GLB',export_animations=True,export_cameras=False,export_lights=False)
