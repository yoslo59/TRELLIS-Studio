"""Project a decoded master onto the final triangle mesh in Blender/Cycles."""
from pathlib import Path
import sys

import bpy
import numpy as np


def to_blender(vectors):
    return vectors[:, [0, 2, 1]] * np.array([1, -1, 1], dtype=np.float32)


def build_object(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set("co", np.ascontiguousarray(vertices, dtype=np.float32).ravel())
    mesh.loops.add(faces.size)
    mesh.polygons.add(len(faces))
    mesh.loops.foreach_set("vertex_index", np.ascontiguousarray(faces, dtype=np.int32).ravel())
    mesh.polygons.foreach_set("loop_start", np.arange(0, faces.size, 3, dtype=np.int32))
    mesh.polygons.foreach_set("use_smooth", np.ones(len(faces), dtype=bool))
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def bake(directory, size):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4

    with np.load(directory / "bake-input.npz", allow_pickle=False) as inputs:
        faces = inputs["faces"]
        low = build_object("Game mesh", to_blender(inputs["positions"]), faces)
        mesh = low.data
        mesh.normals_split_custom_set_from_vertices(to_blender(inputs["normals"]))
        uv = inputs["uv"]
        uv[:, 1] = 1 - uv[:, 1]
        layer = mesh.uv_layers.new(name="UVMap")
        layer.uv.foreach_set("vector", uv[faces].ravel())
        mesh.calc_tangents(uvmap=layer.name)
        tangents = np.empty((faces.size, 3), dtype=np.float32)
        signs = np.empty(faces.size, dtype=np.float32)
        mesh.loops.foreach_get("tangent", tangents.ravel())
        mesh.loops.foreach_get("bitangent_sign", signs)
        # Match Blender's glTF exporter: rotate XYZ and keep the Mikk sign.
        gltf_tangents = np.column_stack([tangents[:, 0], tangents[:, 2], -tangents[:, 1], signs])
        np.save(directory / "tangents.npy", gltf_tangents, allow_pickle=False)

        vertices = np.load(directory / "master-positions.npy", allow_pickle=False)
        if bool(inputs["master_y_up"]):
            vertices = to_blender(vertices)
        vertices *= float(inputs["scale"])
        vertices += to_blender(inputs["translation"].reshape(1, 3))[0]
        master_faces = np.load(directory / "master-faces.npy", mmap_mode="r", allow_pickle=False)
        master = build_object("Decoded master", vertices, master_faces)
        voxel = float(inputs["voxel_m"])
        del vertices, master_faces

    material = bpy.data.materials.new("Bake target")
    material.use_nodes = True
    low.data.materials.append(material)
    image = bpy.data.images.new("Tangent-space detail", width=size, height=size, alpha=True)
    image.colorspace_settings.name = "Non-Color"
    image.generated_color = (.5, .5, 1., 1.)
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    material.node_tree.nodes.active = node
    low.select_set(True)
    master.select_set(True)
    bpy.context.view_layer.objects.active = low
    bpy.context.view_layer.update()
    bpy.ops.object.bake(
        type="NORMAL", use_selected_to_active=True, normal_space="TANGENT",
        normal_r="POS_X", normal_g="POS_Y", normal_b="POS_Z", use_clear=False,
        cage_extrusion=3 * voxel, max_ray_distance=8 * voxel, margin=8, margin_type="EXTEND",
    )
    image.file_format = "PNG"
    image.filepath_raw = str(directory / "unfiltered.png")
    image.save()


if __name__ == "__main__":
    path, resolution = sys.argv[sys.argv.index("--") + 1:]
    bake(Path(path), int(resolution))
