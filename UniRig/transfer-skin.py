"""Transfer UniRig weights to textured meshes in their world coordinate frame."""
from pathlib import Path
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent / 'source'))
from src.inference import merge


def detach_meshes():
    bpy.context.view_layer.update()
    meshes = [(obj, obj.matrix_world.copy()) for obj in bpy.context.scene.objects if obj.type == 'MESH']
    if not meshes:
        raise ValueError('The target has no mesh to skin.')
    for mesh, world in meshes:
        # UniRig multiplies world matrices of all ancestors and then applies
        # that result again to the weight lookup. Root meshes avoid both.
        mesh.parent = None
        mesh.matrix_world = world
    bpy.context.view_layer.update()
    for mesh, world in meshes:
        if not np.allclose(np.asarray(mesh.matrix_world), np.asarray(world), rtol=1e-5, atol=1e-6):
            # Blender cannot store a sheared world matrix in an unparented
            # object's transform channels. Bake it into its private mesh.
            mesh.data = mesh.data.copy()
            mesh.data.transform(world, shape_keys=True)
            mesh.matrix_world = Matrix.Identity(4)
    bpy.context.view_layer.update()


def transfer(source, target, output):
    merge.clean_bpy()
    armature = merge.load(str(source), return_armature=True)
    if armature is None:
        raise ValueError('The skin prediction has no armature to transfer.')
    vertices, _, weights = merge.process_mesh()
    bones = merge.get_arranged_bones(armature)
    if weights is None:
        weights = merge.get_skin(bones)
    joints, tails, parents, names, _ = merge.process_armature(armature, bones)

    merge.clean_bpy()
    merge.load(str(target))
    detach_meshes()
    np.random.seed(12345)
    merge.make_armature(vertices, np.concatenate((joints, tails), axis=1), parents, names, weights)
    if 'FINISHED' not in bpy.ops.export_scene.gltf(filepath=str(output), export_format='GLB'):
        raise ValueError('Could not export the textured rig.')


if __name__ == '__main__':
    transfer(*(Path(value) for value in sys.argv[1:]))
    # The embedded bpy wheel can fault on interpreter teardown after a glTF
    # export. Only leave this way after the export has completed.
    os._exit(0)
