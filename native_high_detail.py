"""Run the two-pass TRELLIS.2 GGUF High Detail pipeline directly.

The TRELLIS.2 processing sequence follows the MIT-licensed VisualBruno and
Aero-Ex adaptations of Microsoft TRELLIS.2.
"""

from __future__ import annotations

import gc
from importlib.util import find_spec
import json
import math
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from native_high_detail_portable import MANIFEST, PIPELINE_REVISION, QUANT_REVISION, Progress, configure_models


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config' / 'native-high-detail.json'
WEIGHT_REVISION = 'edf81aef93af0823ac8721da3c9cd3c52d539f87'
WORKFLOW_SHA256 = '979137c6a5d8a0e510a638f5ce25879aae3e75a8000d87d6712559b9c5c01575'
TRELLIS_GEOMETRY = 'trellis2_gguf'
PIXAL_GEOMETRY = 'pixal3d'
DINO_DIRECTORY = Path('facebook/dinov3-vitl16-pretrain-lvd1689m')
ENVIRONMENT_KEYS = {'CC', 'CUDA_PATH', 'TRITON_CACHE_DIR'}
MODEL_FILES = (
    'pipeline.json',
    'decoders/Stage1/ss_dec_conv3d_16l8_fp16.safetensors',
    'decoders/Stage2/shape_dec_next_dc_f16c32_fp16.safetensors',
    'decoders/Stage2/tex_dec_next_dc_f16c32_fp16.safetensors',
    'encoders/shape_enc_next_dc_f16c32_fp16.safetensors',
    'refiner/ss_flow_img_dit_1_3B_64_bf16_Q4_K_M.gguf',
    'shape/slat_flow_img2shape_dit_1_3B_512_bf16_Q4_K_M.gguf',
    'shape/slat_flow_img2shape_dit_1_3B_1024_bf16_Q4_K_M.gguf',
    'texture/slat_flow_imgshape2tex_dit_1_3B_1024_bf16_Q4_K_M.gguf',
)
DIFFUSION_SETTINGS = dict(
    seed=12345, max_views=4, sampler='euler', verbose=False,
    dino_lock=0.0, dino_substeps=4, dino_foundation_cap=1.0,
)
SHAPE_SETTINGS = dict(
    shape_guidance_strength=6.5, shape_guidance_rescale=0.2, shape_rescale_t=4,
    shape_guidance_interval_start=0.1, shape_guidance_interval_end=1.0,
)
MATERIAL_SETTINGS = dict(
    texture_guidance_strength=3.0, texture_guidance_rescale=0.2, texture_rescale_t=3,
    texture_guidance_interval_start=0.0, texture_guidance_interval_end=0.9,
)
GEOMETRY_SETTINGS = DIFFUSION_SETTINGS | SHAPE_SETTINGS | MATERIAL_SETTINGS | dict(
    max_num_tokens=999999, use_tiled_decoder=True, generate_texture_slat=False,
)
QUAD_SETTINGS = dict(resolution=1024, remove_floaters=True, remove_inner_faces=True, remesh_band=1)
REDUCTION_SETTINGS = dict(target_face_num=2000000, method='Cumesh')
ORIENTATION_SETTINGS = dict(reorient_vertices='90 degrees')
DEFAULTS = {
    '44': dict(geometry_backend=TRELLIS_GEOMETRY, modelname='TRELLIS.2-4B',
               model_format='GGUF Q4_K_M', backend='xformers',
               device='cuda', low_vram=True, keep_models_loaded=False),
    '12': dict(padding=25, remove_background=True, max_size=2048),
    '17': GEOMETRY_SETTINGS | dict(
        pipeline_type='1024_cascade', sparse_structure_steps=50, sparse_structure_guidance_strength=6.5,
        sparse_structure_guidance_rescale=0.2, sparse_structure_rescale_t=4,
        sparse_structure_guidance_interval_start=0.1, sparse_structure_guidance_interval_end=1.0,
        sparse_structure_resolution=32, shape_steps=50, texture_steps=12,
        sparse_structure_postprocessing=False, sparse_hole_size=3,
        fill_holes=True, hole_iterations=1, hole_fill_algorithm='flood_fill', keep_only_shell=True,
    ),
    '41': QUAD_SETTINGS, '11': REDUCTION_SETTINGS, '14': ORIENTATION_SETTINGS,
    '20': GEOMETRY_SETTINGS | dict(resolution=1024, shape_steps=25, texture_steps=25, downsampling=16),
    '42': QUAD_SETTINGS, '22': REDUCTION_SETTINGS, '23': ORIENTATION_SETTINGS,
    '25': DIFFUSION_SETTINGS | MATERIAL_SETTINGS | dict(
        texture_method='trellis', ultratex_size=2048, texture_steps=25, resolution=1024,
        texture_size=4096, texture_alpha_mode='OPAQUE',
        double_side_material=False, bake_on_vertices=False, use_custom_normals=False,
        mesh_cluster_threshold_cone_half_angle_rad=40, inpainting='telea',
    ),
}


def _runtime(config_path):
    config = Path(config_path or CONFIG).resolve()
    try:
        entry = json.loads(config.read_text(encoding='utf-8-sig'))
        if entry['schema'] != 1:
            raise ValueError
        paths = {key: (config.parent / entry[key]).resolve() for key in ('package', 'models', 'u2net')}
        dino = (config.parent / entry['dinov3']).resolve() if 'dinov3' in entry else None
        paths['python_paths'] = [(config.parent / item).resolve() for item in entry.get('python_paths', ())]
        paths['environment'] = {
            key: str((config.parent / value).resolve())
            for key, value in entry.get('environment', {}).items() if key in ENVIRONMENT_KEYS
        }
    except (OSError, TypeError, KeyError, ValueError, AttributeError) as exc:
        raise ValueError('Configure the optional native High Detail runtime.') from exc
    source = paths['package']
    models = paths['models']
    try:
        provenance = json.loads((source / MANIFEST).read_text(encoding='utf-8'))
        if (provenance['schema'], provenance['trellis_gguf_revision'], provenance['quant_revision']) != (
                1, PIPELINE_REVISION, QUANT_REVISION):
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError('The portable High Detail pipeline source is missing or unsupported.') from exc
    dino_paths = (dino,) if dino is not None else (
        models / 'Trellis2/dinov3' / DINO_DIRECTORY, models / DINO_DIRECTORY,
        models / 'Aero-Ex/Dinov3' / DINO_DIRECTORY,
    )
    paths['dinov3'] = next((path for path in dino_paths if (path / 'model.safetensors').is_file()
                           and (path / 'config.json').is_file()), None)
    if paths['dinov3'] is None or not all((models / 'Trellis2' / name).is_file() for name in MODEL_FILES):
        raise ValueError('The native High Detail Q4 weights or local DINOv3 checkpoint are missing.')
    if not (paths['u2net'] / 'u2net.onnx').is_file():
        raise ValueError('The native High Detail background-removal weights are missing.')
    if any(not path.is_dir() for path in paths['python_paths']):
        raise ValueError('A native High Detail dependency folder is missing.')
    return paths


def _check_geometry_backend(backend):
    if backend == TRELLIS_GEOMETRY:
        return
    if backend != PIXAL_GEOMETRY:
        raise ValueError('Choose a supported High Detail geometry generator.')
    try:
        from native_pixal3d import runtime_status as pixal_status
    except ImportError as exc:
        raise ValueError('The optional standalone Pixal3D geometry runtime could not be imported.') from exc
    status = pixal_status(backend=PIXAL_GEOMETRY)
    if not status['available']:
        raise ValueError(status['reason'] or 'The standalone Pixal3D geometry runtime is not ready.')


def runtime_status(*, config_path=None, geometry_backend=TRELLIS_GEOMETRY):
    try:
        _runtime(config_path)
        _check_geometry_backend(geometry_backend)
    except ValueError as exc:
        return {'available': False, 'reason': str(exc)}
    return {'available': True, 'reason': ''}


def _configure(runtime, backend, work):
    sys.path[:0] = [str(runtime['package']), *(str(path) for path in runtime['python_paths'])]
    configure_models(runtime['models'])
    sparse_backend = 'xformers' if backend == 'sdpa' else backend
    required = {'xformers': 'xformers', 'flash_attn': 'flash_attn', 'flash_attn_3': 'flash_attn_interface'}
    if sparse_backend not in required:
        raise ValueError('Choose a supported High Detail attention backend.')
    if find_spec(required[sparse_backend]) is None:
        raise ValueError(f'The {sparse_backend} attention backend is not installed.')
    os.environ.update({
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'U2NET_HOME': str(runtime['u2net']),
        'ATTN_BACKEND': backend, 'SPARSE_ATTN_BACKEND': sparse_backend,
        'PYTORCH_CUDA_ALLOC_CONF': 'expandable_segments:True',
        'OPENCV_IO_ENABLE_OPENEXR': '1',
        'FLEX_GEMM_AUTOTUNE_CACHE_PATH': str(work / 'flex-gemm-cache.json'),
    })
    os.environ.update(runtime['environment'])


def _prepare_image(path, *, padding, remove_background, max_size):
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert('RGBA')
    if remove_background:
        from rembg import remove

        image = remove(image)
    scale = min(1, max_size / max(image.size))
    if scale < 1:
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
    alpha = np.asarray(image)[:, :, 3]
    rows, columns = np.where(alpha > 0.8 * 255)
    if not len(rows):
        raise ValueError('No foreground remains in the High Detail source image.')
    x = (int(columns.min()) + int(columns.max())) / 2
    y = (int(rows.min()) + int(rows.max())) / 2
    half = max(int(columns.max()) - int(columns.min()), int(rows.max()) - int(rows.min())) // 2
    if half == 0:
        raise ValueError('The High Detail foreground is too small.')
    cropped = np.asarray(image.crop((x - half, y - half, x + half, y + half))).astype(np.float32) / 255
    rgb = Image.fromarray((cropped[:, :, :3] * cropped[:, :, 3:4] * 255).astype(np.uint8))
    return ImageOps.expand(rgb, border=int(padding), fill=(0, 0, 0))


def _reset_cuda(torch):
    torch.cuda.synchronize()
    gc.collect()
    torch.cuda.empty_cache()


def _repair_occupancy(occupied, settings):
    import numpy as np
    from scipy.ndimage import binary_closing, binary_erosion, binary_fill_holes, label

    if settings['fill_holes']:
        method = settings['hole_fill_algorithm']
        width = settings['sparse_hole_size']
        if method == 'remove_small_holes':
            occupied = occupied.copy()
            for plane in occupied:
                components, count = label(~plane)
                sizes = np.bincount(components.ravel(), minlength=count + 1)
                sizes[0] = width ** 2
                plane |= sizes[components] < width ** 2
        elif method in ('morphological_closing', 'flood_fill'):
            occupied = binary_closing(occupied, structure=np.ones((width,) * 3, dtype=bool),
                                      iterations=settings['hole_iterations'], border_value=0)
            if method == 'flood_fill':
                occupied = binary_fill_holes(occupied)
                components, count = label(occupied)
                if count:
                    sizes = np.bincount(components.ravel())
                    sizes[0] = 0
                    occupied = components == sizes.argmax()
        else:
            raise ValueError('Choose a supported sparse hole filling algorithm.')
    if settings['keep_only_shell']:
        interior = binary_erosion(occupied, structure=np.ones((3, 3, 3), dtype=bool), iterations=2)
        occupied = occupied & ~interior
    return occupied


def _repair_structure(coords, resolution, settings):
    import numpy as np

    if settings['sparse_hole_size'] not in (3, 5, 7) or not 1 <= settings['hole_iterations'] <= 9:
        raise ValueError('Choose a supported sparse repair kernel and iteration count.')
    points = coords.detach().cpu().numpy()
    if not len(points):
        raise ValueError('Sparse structure repair requires occupied cells.')
    # A non-divisor target may use the closest coarser decoded grid in the pinned sampler.
    resolution = max(resolution, int(points[:, 1:].max()) + 1)
    repaired = []
    for batch in np.unique(points[:, 0]):
        positions = points[points[:, 0] == batch, 1:]
        # Pad by the maximum closing radius so occupied boundary cells are preserved.
        padding = (settings['sparse_hole_size'] // 2) * settings['hole_iterations']
        shape = (resolution + 2 * padding,) * 3
        occupied = np.zeros(shape, dtype=bool)
        occupied[tuple((positions + padding).T)] = True
        occupied = _repair_occupancy(occupied, settings)[padding:-padding, padding:-padding, padding:-padding]
        positions = np.argwhere(occupied)
        if not len(positions):
            raise ValueError('Sparse structure repair left no occupied cells. Reduce the repair settings.')
        repaired.append(np.column_stack((np.full(len(positions), batch), positions)))
    result = np.concatenate(repaired)
    if settings['verbose']:
        print(f'High Detail sparse structure repair: {len(points)} to {len(result)} cells.', flush=True)
    return coords.new_tensor(result)


class _DinoLockedSampler:
    """The optional VisualBruno DINO velocity schedule for the pinned GGUF sampler."""

    def __init__(self, sampler, settings):
        self.sampler = sampler
        self.lock = settings['dino_lock']
        self.substeps = settings['dino_substeps']
        self.cap = settings['dino_foundation_cap']
        self.verbose = settings['verbose']

    def _strength(self, index, steps):
        foundation_end, ramp_end = int(steps * .4), int(steps * .7)
        if index < foundation_end:
            return self.cap
        if index >= ramp_end:
            return self.lock
        fraction = (index - foundation_end) / max(1, ramp_end - foundation_end)
        return self.cap + (self.lock - self.cap) * .5 * (1 - math.cos(math.pi * fraction))

    def sample(self, model, noise, cond=None, steps=50, rescale_t=1., verbose=True,
               tqdm_desc='Sampling', **kwargs):
        from tqdm import tqdm

        if rescale_t <= 0:
            raise ValueError('DINO Lock requires a positive timestep rescale.')
        if cond is None:
            return self.sampler.sample(model, noise, cond, steps=steps, rescale_t=rescale_t,
                                       verbose=verbose, tqdm_desc=tqdm_desc, **kwargs)
        predict = self.sampler._inference_model
        positive_only = kwargs | {'guidance_strength': 1.0}
        times = [1. - index / steps for index in range(steps + 1)]
        times = [rescale_t * time / (1 + (rescale_t - 1) * time) for time in times]
        sample, previous_velocity = noise, None
        for index in tqdm(range(steps), desc=tqdm_desc, disable=not verbose):
            time, end = times[index:index + 2]
            strength = self._strength(index, steps)
            guided = predict(model, sample, time, cond, **kwargs)
            positive = predict(model, sample, time, cond, **positive_only)
            velocity = (1 - strength) * guided + strength * positive
            if previous_velocity is not None:
                velocity = .85 * velocity + .15 * previous_velocity
            previous_velocity = velocity
            if strength >= .9 and self.substeps > 1:
                step = (time - end) / self.substeps
                for substep in range(self.substeps):
                    sample = sample - step * predict(model, sample, time - substep * step, cond, **kwargs)
            else:
                sample = sample - (time - end) * velocity
        if self.verbose:
            print(f'High Detail DINO Lock: foundation {self.cap:.2f}, guardrail {self.lock:.2f}, '
                  f'{self.substeps} foundation substeps.', flush=True)
        return SimpleNamespace(samples=sample)


def _bind_dino_checkpoint(pipeline, runtime):
    # The pinned upstream loader assumes its own directory layout. Studio's
    # setup can select the same local checkpoint from the shared foundation.
    pipeline._pretrained_args['image_cond_model']['args']['model_name'] = str(runtime['dinov3'])


def _load_model(torch, runtime, settings):
    from trellis2_gguf.pipelines.trellis2_image_to_3d import Trellis2ImageTo3DPipeline

    class NativePipeline(Trellis2ImageTo3DPipeline):
        stage_settings = None

        def sample_sparse_structure(self, cond, resolution, *args, **kwargs):
            coordinates = super().sample_sparse_structure(cond, resolution, *args, **kwargs)
            if self.stage_settings and self.stage_settings.get('sparse_structure_postprocessing'):
                return _repair_structure(coordinates, resolution, self.stage_settings)
            return coordinates

        def _get_sampler(self, sampler_name, default_sampler, **kwargs):
            sampler = super()._get_sampler(sampler_name, default_sampler, **kwargs)
            if self.stage_settings and self.stage_settings['dino_lock'] > 0:
                return _DinoLockedSampler(sampler, self.stage_settings)
            return sampler

    if (settings['modelname'], settings['model_format'], settings['device']) != (
            'TRELLIS.2-4B', 'GGUF Q4_K_M', 'cuda'):
        raise ValueError('High Detail supports the installed TRELLIS.2-4B Q4_K_M CUDA checkpoint.')
    _reset_cuda(torch)
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    torch.set_float32_matmul_precision('high')
    pipeline = NativePipeline.from_pretrained(
        str(runtime['models'] / 'Trellis2'), keep_models_loaded=settings['keep_models_loaded'],
        enable_gguf=True, gguf_quant='Q4_K_M', precision=None, enable_sdnq=False,
        sdnq_use_quantized_matmul=True, sdnq_torch_compile=False, sdnq_svd_rank=32, isPixal3D=False,
    )
    _bind_dino_checkpoint(pipeline, runtime)
    pipeline.low_vram = settings['low_vram']
    if pipeline.low_vram:
        pipeline.cuda()
    else:
        pipeline.to('cuda')
    return pipeline


def _sampler(settings, stage):
    return dict(
        steps=settings[stage + '_steps'], guidance_strength=settings[stage + '_guidance_strength'],
        guidance_rescale=settings[stage + '_guidance_rescale'], rescale_t=settings[stage + '_rescale_t'],
        guidance_interval=[settings[stage + '_guidance_interval_start'],
                           settings[stage + '_guidance_interval_end']],
    )


def _arguments(settings, *names):
    return {key: settings[key] for key in names}


def _common(settings):
    return _arguments(settings, 'seed', 'max_views', 'sampler', 'verbose',
                      'dino_lock', 'dino_substeps', 'dino_foundation_cap')


def _stage(pipeline, settings, name, *sampler_stages):
    pipeline.stage_settings = settings
    if settings['verbose']:
        samplers = ', '.join(f'{stage}: {settings[stage + "_steps"]} steps' for stage in sampler_stages)
        print(f'High Detail {name}: {settings["sampler"]}, seed {settings["seed"]}, {samplers}.', flush=True)


def _generate(torch, pipeline, reference, settings, progress):
    _reset_cuda(torch)
    _stage(pipeline, settings, 'first pass', 'sparse_structure', 'shape')
    progress('Generating dense TRELLIS.2 geometry', 18)
    return pipeline.run(
        image=reference, sparse_structure_sampler_params=_sampler(settings, 'sparse_structure'),
        shape_slat_sampler_params=_sampler(settings, 'shape'), tex_slat_sampler_params=_sampler(settings, 'texture'),
        pbar=Progress(5 if settings['generate_texture_slat'] else 4,
                      lambda current, total: progress('Generating dense TRELLIS.2 geometry', 18 + 20 * current // total)),
        use_tiled=settings['use_tiled_decoder'], **_common(settings),
        **_arguments(settings, 'pipeline_type', 'max_num_tokens', 'sparse_structure_resolution',
                     'generate_texture_slat', 'fill_holes', 'hole_iterations', 'hole_fill_algorithm', 'keep_only_shell'),
    )[0]


def _generate_pixal_reference(ref, work, progress):
    from native_pixal3d import generate_pixal3d_geometry

    progress('Preparing the Pixal3D reference', 8)
    return generate_pixal3d_geometry(
        ref, progress=lambda label, percent: progress(label, 8 + 33 * int(percent) // 100),
        backend=PIXAL_GEOMETRY, job_dir=work,
    )


def _pixal_mesh_in_trellis_frame(torch, result):
    import numpy as np
    from trellis2_gguf.representations import Mesh

    if not isinstance(result, tuple) or len(result) != 2:
        raise ValueError('Pixal3D did not return an indexed mesh.')
    vertices, faces = (np.asarray(array) for array in result)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or vertices.size == 0
            or not np.issubdtype(vertices.dtype, np.floating) or not np.isfinite(vertices).all()):
        raise ValueError('Pixal3D returned invalid geometry positions.')
    if (faces.ndim != 2 or faces.shape[1] != 3 or faces.size == 0
            or not np.issubdtype(faces.dtype, np.integer) or faces.min() < 0
            or faces.max() >= len(vertices)):
        raise ValueError('Pixal3D returned invalid triangle indices.')
    # The Pixal3D result is Y-up. Undo the same +90-degree export conversion
    # that the first High Detail repair applies before the common refiner.
    positions = np.ascontiguousarray(vertices[:, (0, 2, 1)], dtype=np.float32)
    positions[:, 1] *= -1
    return Mesh(torch.from_numpy(positions), torch.from_numpy(np.ascontiguousarray(faces, dtype=np.int32)))


def _repair(torch, mesh, quad, reduction, orientation, progress, *, start, pass_name='first pass'):
    import cumesh
    import pymeshlab
    import trimesh
    from meshlib import mrmeshnumpy, mrmeshpy

    _reset_cuda(torch)
    progress(f'Reconstructing the surface with QuadDC · {pass_name}', start)
    vertices, faces = cumesh.remeshing.reconstruct_mesh_dc_quad(
        mesh.vertices.cuda(), mesh.faces.cuda(), quad['resolution'], verbose=True,
        band=quad['remesh_band'], remove_inner_faces=quad['remove_inner_faces'],
    )
    if quad['remove_floaters']:
        mesh_set = pymeshlab.MeshSet()
        mesh_set.add_mesh(pymeshlab.Mesh(vertex_matrix=vertices.cpu().numpy(), face_matrix=faces.cpu().numpy()))
        mesh_set.apply_filter('compute_selection_by_small_disconnected_components_per_face', nbfaceratio=0.005)
        mesh_set.apply_filter('compute_selection_transfer_face_to_vertex', inclusive=False)
        mesh_set.apply_filter('meshing_remove_selected_vertices_and_faces')
        filtered = mesh_set.current_mesh()
        vertices = torch.from_numpy(filtered.vertex_matrix().copy()).contiguous().float()
        faces = torch.from_numpy(filtered.face_matrix().copy()).contiguous().int()
    mesh.vertices = vertices.to(mesh.device)
    mesh.faces = faces.to(mesh.device)
    del vertices, faces
    progress(f'Reducing triangles and repairing holes · {pass_name}', start + 5)
    if reduction['method'] == 'Cumesh':
        mesh.simplify_with_cumesh(target=reduction['target_face_num'])
    elif reduction['method'] == 'Meshlib':
        mesh.simplify_with_meshlib(target=reduction['target_face_num'])
    else:
        raise ValueError('Unknown High Detail simplification method.')

    surface = mrmeshnumpy.meshFromFacesVerts(mesh.faces.detach().cpu().numpy(), mesh.vertices.detach().cpu().numpy())
    holes = list(surface.topology.findHoleRepresentiveEdges())
    for edge in holes:
        fill = mrmeshpy.FillHoleParams()
        fill.metric = mrmeshpy.getUniversalMetric(surface)
        mrmeshpy.fillHole(surface, edge, fill)
    vertices = torch.from_numpy(mrmeshnumpy.getNumpyVerts(surface)).float().numpy()
    faces = torch.from_numpy(mrmeshnumpy.getNumpyFaces(surface.topology)).int().numpy()
    del surface
    progress(f'Orienting the mesh · {pass_name}', start + 8)
    direction = orientation['reorient_vertices']
    if direction == '90 degrees':
        vertices[:, 1], vertices[:, 2] = vertices[:, 2].copy(), -vertices[:, 1]
    elif direction == '-90 degrees':
        vertices[:, 1], vertices[:, 2] = -vertices[:, 2], vertices[:, 1].copy()
    print(f'High Detail MeshLib repaired {len(holes)} boundary loop(s).', flush=True)
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def _refine(torch, pipeline, reference, mesh, settings):
    _reset_cuda(torch)
    _stage(pipeline, settings, 'geometry refinement', 'shape')
    return pipeline.refine_mesh(
        mesh=mesh, image=reference, use_tiled=settings['use_tiled_decoder'],
        shape_slat_sampler_params=_sampler(settings, 'shape'),
        tex_slat_sampler_params=_sampler(settings, 'texture'),
        **_common(settings), **_arguments(settings, 'resolution', 'max_num_tokens',
                                          'generate_texture_slat', 'downsampling'),
    )[0]


def _texture(pipeline, reference, mesh, settings):
    _stage(pipeline, settings, 'texture generation', 'texture')
    return pipeline.texture_mesh(
        mesh=mesh, image=reference, tex_slat_sampler_params=_sampler(settings, 'texture'),
        **_common(settings),
        **_arguments(settings, 'resolution', 'texture_size', 'texture_alpha_mode',
                     'bake_on_vertices', 'use_custom_normals', 'mesh_cluster_threshold_cone_half_angle_rad', 'inpainting'),
    )[0]


def _material_sides(tree, double_sided):
    materials = tree.setdefault('materials', [])
    for primitive in (primitive for mesh in tree['meshes'] for primitive in mesh['primitives']):
        if 'material' not in primitive:
            primitive['material'] = len(materials)
            materials.append({})
        materials[primitive['material']]['doubleSided'] = double_sided


def ultratex_status():
    from native_game_profile import texture_runtime_status
    from native_pixal3d import texture_mask_status

    texture = texture_runtime_status()
    return texture_mask_status() if texture['available'] else texture


def _retexture_ultratex(staging, reference, destination, work, atlas_size, progress):
    from native_game_profile import texture_mesh
    from native_pixal3d import prepare_texture_mask
    from native_workflow_options import settings

    with TemporaryDirectory(prefix='.high-detail-ultratex-', dir=work) as directory:
        scratch = Path(directory)
        progress('Preparing the original reference for UltraTex', 94)
        mask = prepare_texture_mask(reference, scratch / 'foreground.npy')
        recipe = settings('game_asset_native', {'377': {'texture_size': atlas_size}})
        candidate = scratch / 'model.glb'
        result = texture_mesh(staging, reference, mask, candidate, scratch / 'texture',
                              lambda label: progress(label, 96), options=recipe)
        os.replace(candidate, destination)
    return result


def run_profile(image_path, output_path, job_dir, options=None, progress=None, *, config_path=None):
    """Generate the native High Detail GLB inside the current job directory."""
    started = perf_counter()
    ref, destination, work = (Path(path).resolve() for path in (image_path, output_path, job_dir))
    if not destination.is_relative_to(work) or destination.suffix.lower() != '.glb' or ref == destination:
        raise ValueError('High Detail needs an output GLB within the current job folder.')
    overrides = options or {}
    if not isinstance(overrides, dict) or overrides.keys() - DEFAULTS.keys():
        raise ValueError('Unknown native High Detail stage.')
    for node, values in overrides.items():
        if not isinstance(values, dict) or values.keys() - DEFAULTS[node].keys():
            raise ValueError(f'Unknown native High Detail setting in stage {node}.')
    stages = {node: defaults | overrides.get(node, {}) for node, defaults in DEFAULTS.items()}
    material = stages['25']
    use_ultratex = material['texture_method'] == 'ultratex'
    if use_ultratex:
        if material['bake_on_vertices']:
            raise ValueError('UltraTex needs a UV atlas. Turn off Bake on vertices.')
        capability = ultratex_status()
        if not capability['available']:
            raise ValueError(capability['reason'])
    for node in ('41', '42'):
        if not 0.5 <= stages[node]['remesh_band'] <= 4:
            raise ValueError('The QuadDC remesh band must be between 0.5 and 4.')
    progress = progress or (lambda _label, _percent: None)
    geometry = stages['44']['geometry_backend']
    runtime = _runtime(config_path)
    _check_geometry_backend(geometry)
    use_pixal = geometry == PIXAL_GEOMETRY
    pixal_result = None
    if use_pixal:
        if stages['17'] != DEFAULTS['17']:
            raise ValueError('The first TRELLIS.2 pass settings apply only to TRELLIS.2 GGUF geometry.')
        # Pixal3D owns its environment and unloads it before loading this worker's
        # TRELLIS.2 dependencies. It conditions on the original reference.
        pixal_result = _generate_pixal_reference(ref, work, progress)

    _configure(runtime, stages['44']['backend'], work)

    import torch
    import trimesh

    if not torch.cuda.is_available():
        raise RuntimeError('The native High Detail profile requires CUDA.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    progress('Preparing the reference image', 42 if use_pixal else 8)
    reference = _prepare_image(ref, **stages['12'])
    progress('Loading TRELLIS.2 GGUF model weights', 42 if use_pixal else 13)
    with torch.inference_mode():
        pipeline = _load_model(torch, runtime, stages['44'])
        if use_pixal:
            raw = _pixal_mesh_in_trellis_frame(torch, pixal_result)
            del pixal_result
        else:
            raw = _generate(torch, pipeline, reference, stages['17'], progress)
        first = _repair(torch, raw, stages['41'], stages['11'], stages['14'], progress, start=43, pass_name='first pass')
        del raw
        progress('Refining the TRELLIS.2 mesh', 56)
        refined = _refine(torch, pipeline, reference, first, stages['20'])
        del first
        surface = _repair(torch, refined, stages['42'], stages['22'], stages['23'], progress, start=72, pass_name='second pass')
        del refined
        progress('Generating TRELLIS.2 materials', 87)
        textured = _texture(pipeline, reference, surface, stages['25'])
        del surface, pipeline

    progress('Packaging the mesh and its UV atlas', 92 if use_ultratex else 97)
    if not stages['25']['use_custom_normals']:
        textured.vertex_normals = trimesh.smoothing.get_vertices_normals(textured)
    material_sides = stages['25']['double_side_material']
    export_options = {}
    if material_sides is not False:
        export_options['tree_postprocessor'] = lambda tree: _material_sides(tree, material_sides is True)
    staging = destination.with_name(f'.{destination.stem}-{uuid4().hex}.glb')
    ultratex = None
    try:
        textured.export(staging, file_type='glb', **export_options)
        if use_ultratex:
            _reset_cuda(torch)
            ultratex = _retexture_ultratex(staging, ref, destination, work, material['ultratex_size'], progress)
        else:
            os.replace(staging, destination)
    finally:
        staging.unlink(missing_ok=True)
    provenance = (
        {'engine': 'TRELLIS.2 GGUF', 'workflow_sha256': WORKFLOW_SHA256,
         'weights_revision': WEIGHT_REVISION, 'seed': stages['17']['seed']}
        if geometry == TRELLIS_GEOMETRY else
        {'engine': 'Pixal3D + TRELLIS.2 GGUF', 'finishing_workflow_sha256': WORKFLOW_SHA256,
         'finishing_weights_revision': WEIGHT_REVISION, 'refinement_seed': stages['20']['seed'],
         'material_seed': stages['25']['seed']}
    )
    output_details = provenance | {
        'geometry_backend': geometry,
        'elapsed_seconds': round(perf_counter() - started, 2), 'resolution': stages['25']['resolution'],
        'texture_method': material['texture_method'],
        'texture_size': material['ultratex_size'] if use_ultratex else 0 if material['bake_on_vertices'] else material['texture_size'],
        'triangles': len(textured.faces), 'vertices': len(textured.vertices),
    }
    if ultratex is not None:
        output_details['texture'] = ultratex
    return output_details



if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--options', type=Path, help='JSON settings keyed by stage ID')
    args = parser.parse_args()
    settings = json.loads(args.options.read_text(encoding='utf-8-sig')) if args.options else {}
    receipt = run_profile(args.reference, args.output, args.output.parent, settings,
                          lambda stage, percent: print(f'{percent}% {stage}', flush=True),
                          config_path=args.config)
    print(json.dumps(receipt, indent=2))
