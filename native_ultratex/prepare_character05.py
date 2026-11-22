"""Prepare shared geometry, per-branch camera rigs, and the aligned reference."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parent.parent
NATIVE_RUNTIME = ROOT / 'runtime/native-game-texture'
native_deps = NATIVE_RUNTIME / 'deps'
if not native_deps.is_dir():
    raise RuntimeError('The standalone UltraTex dependencies are missing.')
sys.path[:0] = [str(ROOT), str(native_deps)]

import numpy as np
from PIL import Image, ImageOps
import torch

from glb_data import accessor, read
from native_ultratex_recipe import fitted_cameras
from native_ultratex.options import EDITABLE_TEXTURE_FIELDS


def load_static_mesh(path):
    document, binary = read(path)
    meshes = document['meshes']
    if (len(meshes) != 1 or len(meshes[0]['primitives']) != 1 or document.get('skins')
            or any({'matrix', 'translation', 'rotation', 'scale'} & node.keys() for node in document['nodes'])):
        raise ValueError('Expected one static UV mesh with applied transforms.')
    primitive = meshes[0]['primitives'][0]
    if primitive.get('mode', 4) != 4 or primitive.get('targets'):
        raise ValueError('Expected indexed triangles without morph targets.')
    attributes = primitive['attributes']

    def tensor(name):
        values = accessor(document, binary, attributes[name]).astype(np.float32)
        return torch.from_numpy(values).unsqueeze(0)

    faces = accessor(document, binary, primitive['indices']).astype(np.int32).reshape(1, -1, 3)
    return SimpleNamespace(vertices=tensor('POSITION'), faces=torch.from_numpy(faces),
                           uvs=tensor('TEXCOORD_0'), normals=tensor('NORMAL') if 'NORMAL' in attributes else None)


def prepare_textures(source_glb, source_image, foreground_mask, work_dir, prompt):
    """Share geometry and render each distinct normal rig only once."""
    from utx_graphics.conditions import prepare_reference
    from utx_graphics.contracts import RuntimePolicy, TextureSetup
    from utx_graphics.geometry import plan_views, prepare_geometry
    from utx_graphics.render import render_gbuffers
    from utx_graphics.runtime import Store, fingerprint

    align = prompt['375']['inputs']
    memory = prompt['387']['inputs']
    policy = RuntimePolicy(str(work_dir / 'spill'), **{key: memory[key] for key in EDITABLE_TEXTURE_FIELDS['387']})
    context, _ = prepare_geometry(load_static_mesh(source_glb), policy=policy, normal_mode=align['normal_mode'])
    store = Store(policy)
    normal_resolution = int(align['normal_view_resolution'])
    fixed_cameras = align['camera_fit'] == 'original'
    buffer_cache, reference_cache = {}, {}

    def rig(yaw, render_size, sample_size):
        key = (yaw, render_size)
        if key not in buffer_cache:
            base = plan_views(context, render_size, render_size, yaw)
            fill = 0.94
            if align['camera_fit'] == 'custom':
                margin = align['image_margin_percent'] / 100.0 + align['image_padding_pixels'] / render_size
                fill = min(0.98, max(0.50, 1.0 - 2.0 * margin))
            matrices, _ = fitted_cameras(context.vertices, yaw, target_fill=fill, fit=not fixed_cameras)
            matrices = tuple(tuple(tuple(float(value) for value in row) for row in matrix) for matrix in matrices)
            plan = replace(base, matrices=matrices,
                           identity=fingerprint('views-v2-framing', context.geometry_id, matrices, render_size, render_size))
            buffer_cache[key], _ = render_gbuffers(context, plan, policy)
        result = buffer_cache[key]
        if result.plan.sample_size != sample_size:
            plan = replace(result.plan, sample_size=sample_size,
                           identity=fingerprint('utx1-sample-v1', result.plan.identity, sample_size))
            result = replace(result, plan=plan)
        return result

    with Image.open(source_image) as opened:
        image = np.asarray(ImageOps.exif_transpose(opened).convert('RGB'), np.float32)[None] / 255.0
    mask = np.asarray(foreground_mask, np.float32)[None]

    def aligned_reference(first):
        key = first.plan.identity
        if key not in reference_cache:
            reference = prepare_reference(image, mask, first, policy, polarity=align['mask_polarity'],
                                          alignment=align['alignment'])
            zoom = align['reference_zoom']
            if abs(zoom - 1.0) >= 1e-6:
                pixels = Image.fromarray(reference.rgba.read())
                width, height = pixels.size
                scaled = (max(1, round(width * zoom)), max(1, round(height * zoom)))
                canvas = Image.new('RGBA', pixels.size)
                canvas.paste(pixels.resize(scaled, Image.Resampling.LANCZOS),
                             ((width - scaled[0]) // 2, (height - scaled[1]) // 2))
                identity = fingerprint('utx1-reference-zoom-v1', reference.identity, zoom, width, height)
                reference = replace(reference, rgba=store.put(np.asarray(canvas), identity), identity=identity,
                                    transform={**reference.transform, 'utx1_zoom': zoom})
            reference_cache[key] = reference
        return reference_cache[key]

    def branch(config):
        size = config['view_resolution']
        first = rig(0.0, normal_resolution if normal_resolution >= size else max(2048, size), size)
        views = [first]
        if config['view_count'] != 6:
            second = rig(45.0, max(normal_resolution, size), size)
            if config['view_count'] == 10:
                active = (0, 1, 2, 3)
                plan = replace(second.plan, active_indices=active,
                               identity=fingerprint('utx-10v-four-diagonal-v1', second.plan.identity, active))
                empty = store.put(np.zeros((plan.render_size, plan.render_size), dtype=np.bool_))
                second = replace(second, plan=plan, masks=second.masks[:4] + (empty, empty))
            views.append(second)
        return TextureSetup(context, tuple(views), aligned_reference(first), policy)

    return {task: branch(prompt[node]['inputs']) for task, node in (('albedo', '358'), ('mr', '364'))}
