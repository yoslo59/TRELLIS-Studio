"""Publish the predicted skeleton for the studio while skinning continues."""
from pathlib import Path
import bpy,json,sys,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent/'source'))
from src.inference.merge import denormalize_vertices,get_correct_orientation_kdtree
job=Path(sys.argv[1]);bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=str(Path(sys.argv[2]) if len(sys.argv)>2 else job/'rig-work'/'skeleton.fbx'),use_image_search=False)
arm=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
names=[dict(name=b.name,parent=b.parent.name if b.parent else None) for b in arm.data.bones]
bones=np.array([list(arm.matrix_world@b.head_local)+list(arm.matrix_world@b.tail_local) for b in arm.data.bones])
def mesh_points():return np.array([list(o.matrix_world@v.co) for o in bpy.context.scene.objects if o.type=='MESH' for v in o.data.vertices])
vertices=mesh_points()
bpy.ops.wm.read_factory_settings(use_empty=True)
source=job/'rig-work'/'input.glb'
if not source.is_file():source=job/'source.glb'
bpy.ops.import_scene.gltf(filepath=str(source));target=mesh_points()
# Use the same scale, translation and orientation recovery as UniRig's merge.
vertices,bones=denormalize_vertices(target,vertices,bones)
np.random.seed(12345);vertices,bones=get_correct_orientation_kdtree(vertices,target,bones)
def gltf(vector):return [float(vector[0]),float(vector[2]),float(-vector[1])]
data=dict(predicted=True,aligned_to_model=True,landmarks_applied=(job/'rig-work'/'landmarks-report.json').is_file(),bones=[dict(**name,head=gltf(bone[:3]),tail=gltf(bone[3:])) for name,bone in zip(names,bones)])
path=job/'rig-preview.tmp';path.write_text(json.dumps(data),encoding='utf-8');path.replace(job/'rig-preview.json')
