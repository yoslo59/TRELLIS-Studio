"""Portable geometry stages of Character 05. This module does not load ComfyUI."""

import ast
from contextlib import contextmanager
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import struct
import sys
import time
from types import ModuleType, SimpleNamespace
from uuid import uuid4


RECIPE = Path(__file__).resolve().parent / 'runtime/native-game-geometry/character-05-recipe.json'
RECIPE_SHA256 = 'f0a041581429eb673b3decd57183c87b3444e71231041ca23d6ed5829d6cfa9d'
UV_PATH = Path('comfy_extras/mesh3d/uv_unwrap')
MESH_POSTPROCESS = Path('comfy_extras/nodes_mesh_postprocess.py')
MESH_SERIALIZER = Path('comfy_extras/nodes_save_3d.py')
# The numerical code stays in the separately provisioned GPL source installation.
COMFY_OPERATORS = {
    Path('LICENSE'): '3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986',
    MESH_POSTPROCESS: '215ed682a2a4554de99d6d676236723e9cfc7e037b0cde77747ca802f8ddd005',
    MESH_SERIALIZER: 'b67519d34648d9d61f9663188b5b11d0727693c0e89e527724310b99de847d47',
    UV_PATH / 'mesh.py': 'b73b1509db7a9a5b5f2d0f3316de292a768616707dab355766aa9a428732d051',
    UV_PATH / 'segment.py': 'c53a3a2b7af31a3eb6034eb7e910bad1c67464613900c260d8b714d0d5e5d685',
    UV_PATH / 'parameterize.py': '1594225fc3740fc72d91dc536c3a3957cb141fce90e308a108bb7859d1cfa778',
    UV_PATH / 'pack.py': 'fc39f8f583a14c516d1c3be4acd79b04ce661cce89dc56c70bb4e5ed13e614ad',
}
UV_RUNTIME_CALLS = {
    'comfy.model_management.get_torch_device': '_uv_device',
    'comfy.model_management.get_free_memory': '_uv_free_memory',
    'comfy.utils.ProgressBar': '_uv_progress',
}
LOD_SCRIPT = 'LODTailor-The-Mesh-Trimmer-ComfyuiNode/decimate_only.py'
BAKE_SOURCE = 'LODTailor-Bake-Forger/__init__.py'
QUAD_SOURCE = 'ComfyUI-Mesh-Quad-Reconstruct/nodes.py'
MERGE_TOOL = 'WTiVo-FastMergeByDistance'
TOOLKIT_LICENSES = {
    'LODTailor-The-Mesh-Trimmer-ComfyuiNode': '3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986',
    'LODTailor-Bake-Forger': '3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986',
    'ComfyUI-Mesh-Quad-Reconstruct': '3b64bf740a5cac9e22542f3c95e09ecd61f60c8c9e799d83050304285f303c91',
    MERGE_TOOL: '413e5441523e34c08835367060101d4659ad6b5368c56e5291655375c793917a',
}
EXPECTED_SOURCE = {
    LOD_SCRIPT: '85957ba32a20d007ba2468bed25ce2bd32bf13766a2880f3bd15a1ed4d69fb0e',
    BAKE_SOURCE: '8abae94bbe4f1672ebaf2e305d21de7a6ba0d3105cc274877cf7741fc62af1a0',
    QUAD_SOURCE: 'd4ea0405bd623127e29b36e615d71d9b132c64ac387bf2b445af1a09f786933d',
    f'{MERGE_TOOL}/node.py': '5c1f7b182ba9a4d64583dacbc12a6d7b1910b00db1dbd462f219c68cbd7a7209',
    f'{MERGE_TOOL}/native.py': '5201347933c4caee8a4b7ac8347d6759d8eb74b4132abc7e3b59986e0e51c9da',
    f'{MERGE_TOOL}/python_weld.py': '2381f7f80284c285368489bffab3147826ca4d1700616d977c9a9bfa30cd223f',
    f'{MERGE_TOOL}/native/wtivo_fast_weld_mt.dll': '55f16afa679cbc8cb3dfd15e05c1cd89eb35f8e5b763f98e7dabe32c5251adc0',
}
QUAD_WHEEL = {
    '__init__.py': 'b0391f73f6db9fa214e4a7bf689a9b33879d773c1d273c1f2ef6fdd32bb5b4df',
    'remeshing.py': 'b38a22123c381c99afff23613d188475b26af24bec04a6774100f66be76b029d',
    'bvh.py': '2a6116850dbccb52da11c1585a3196f0f81f48df94f4fd761e30701582552551',
    'cumesh.py': 'eabb42a0e1e4a06e994103fa21ff22c07cf9b467b4110e7445971a3aad31dac9',
    'xatlas.py': '19ca10dd0880ce8999f46e9db40146bc3c863ac641189309ec9f5f015ad58083',
    '_C.cp312-win_amd64.pyd': 'd5dbafac1384015c99fcf801c1c2f43c666ff720d2d96387d77208a63bea9b3d',
    '_cubvh.cp312-win_amd64.pyd': '40a4f55495172c16ccecf5d7e76b2f6289d7883c29493993edf0bcbd872b696e',
    '_xatlas.cp312-win_amd64.pyd': '12d3501747913f99162f3025b6a8f63dae7db52922360b5beb081fb22a135be3',
}


BAKE_MAP_OPTIONS = ('bake_normal_detail', 'copy_base_color_texture', 'bake_roughness_texture',
                    'bake_metallic_texture', 'bake_emission_texture', 'bake_ao_texture')


def _step(node, expected_class, options=None):
    prompt = json.loads(_verified_path(RECIPE, RECIPE_SHA256).read_text(encoding='utf-8'))['payload']['prompt']
    step = prompt[node]
    if step['class_type'] != expected_class:
        raise ValueError(f'Unexpected Character 05 node type at {node}.')
    inputs = step['inputs']
    overrides = (options or {}).get(node, {})
    if any(key not in inputs or isinstance(inputs[key], list) for key in overrides):
        raise ValueError(f'Invalid Character 05 controls at {node}.')
    return {**inputs, **overrides}


def _verified_path(path, expected):
    with path.open('rb') as source:
        actual = hashlib.file_digest(source, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError(f'Unverified Character 05 component: {path.name}.')
    return path


def _pinned_path(toolkit, relative_path):
    toolkit = Path(toolkit).resolve(strict=True)
    folder = relative_path.split('/', 1)[0]
    _verified_path(toolkit / folder / 'LICENSE', TOOLKIT_LICENSES[folder])
    return _verified_path(toolkit / relative_path, EXPECTED_SOURCE[relative_path])


def _run_blender(blender, work, arguments, timeout):
    executable = Path(blender).resolve(strict=True)
    if not executable.is_file():
        raise ValueError('The Character 05 Blender executable is missing.')
    environment = dict(os.environ, TEMP=str(work), TMP=str(work))
    with (work / 'blender.log').open('wb') as log:
        try:
            run = subprocess.run([str(executable), *arguments], cwd=work, env=environment,
                                 stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as error:
            raise RuntimeError('Character 05 Blender stage timed out. See blender.log.') from error
    if run.returncode:
        raise RuntimeError('Character 05 Blender stage failed. See blender.log.')


def _quad_floaters(toolkit, torch):
    """Load only the two MIT CUDA geometry functions, omitting the original node."""
    path = _pinned_path(toolkit, QUAD_SOURCE)
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    names = {'_visualbruno_keep_mask', '_remove_floaters_cuda'}
    operators = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name in names]
    if {item.name for item in operators} != names:
        raise ValueError('Unable to locate the verified CUDA floater operations.')
    constants = {'FLOATER_FACE_RATIO', 'CUDA_FILTER_CHUNK_FACES'}
    assignments = [item for item in tree.body if isinstance(item, ast.Assign) and len(item.targets) == 1
                   and isinstance(item.targets[0], ast.Name) and item.targets[0].id in constants]
    if {item.targets[0].id for item in assignments} != constants:
        raise ValueError('Unable to locate the verified CUDA floater settings.')
    namespace = {'torch': torch}
    exec(compile(ast.Module(body=assignments + operators, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_remove_floaters_cuda']


def _quad_reconstruction_with_progress(remeshing, source, progress):
    """Instrument only the signed-distance loop in the verified CuMesh source."""
    tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    reconstruct, = (node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'reconstruct_mesh_dc_quad')
    inner_filter, = (node for node in reconstruct.body if isinstance(node, ast.FunctionDef)
                     and node.name == 'chunked_sdf_raystab')
    loop, = (node for node in inner_filter.body if isinstance(node, ast.For))
    loop.iter = ast.parse('_studio_quad_batches(N, chunk_size, device)', mode='eval').body

    def batches(total, chunk_size, device):
        label = 'Filtering inner faces with QuadDC'
        total_batches = (total + chunk_size - 1) // chunk_size
        progress(f'{label} (0/{total_batches} batches complete)')
        synchronize = remeshing.torch.cuda.synchronize
        synchronize(device)
        started = time.perf_counter()
        for batch, start in enumerate(range(0, total, chunk_size), 1):
            yield start
            # The native ray queries queue CUDA work. Count a batch after it finishes.
            synchronize(device)
            completed = min(start + chunk_size, total)
            detail = f'{batch}/{total_batches} batches complete'
            if completed < total:
                remaining = math.ceil((time.perf_counter() - started) * (total - completed) / completed)
                minutes, seconds = divmod(remaining, 60)
                duration = f'{minutes} min {seconds:02d} s' if minutes else f'{seconds} s'
                detail += f', about {duration} remaining'
            progress(f'{label} ({detail})')

    namespace = {**vars(remeshing), '_studio_quad_batches': batches}
    instrumented = ast.Module(body=[reconstruct], type_ignores=[])
    exec(compile(ast.fix_missing_locations(instrumented), str(source), 'exec'), namespace)
    return namespace[reconstruct.name]


def reconstruct_quad(toolkit, cumesh_directory, vertices, faces, *, options=None, quad_progress=None):
    """Rebuild with the pinned VisualBruno CuMesh and its CUDA floater rule."""
    from importlib.util import find_spec
    import torch

    recipe = _step('325', 'MeshReconstructWithQuad', options)
    cumesh_directory = Path(cumesh_directory).resolve(strict=True)
    package = find_spec('cumesh')
    if not package or not package.origin or Path(package.origin).resolve() != cumesh_directory / '__init__.py':
        raise ValueError('Use the separately installed Character 05 CuMesh package for QuadDC.')
    for component, expected in QUAD_WHEEL.items():
        _verified_path(cumesh_directory / component, expected)
    import cumesh

    if not torch.cuda.is_available():
        raise ValueError('The Character 05 QuadDC stage requires CUDA.')
    position = torch.as_tensor(vertices, dtype=torch.float32, device='cuda').contiguous()
    indices = torch.as_tensor(faces, dtype=torch.int32, device='cuda').contiguous()
    if (position.ndim != 2 or position.shape[1] != 3 or not len(position) or not torch.isfinite(position).all()
            or indices.ndim != 2 or indices.shape[1] != 3 or not len(indices)
            or indices.min() < 0 or indices.max() >= len(position)):
        raise ValueError('QuadDC expects finite indexed triangles.')
    reconstruct = cumesh.remeshing.reconstruct_mesh_dc_quad
    if recipe['remove_inner_faces'] and quad_progress is not None:
        reconstruct = _quad_reconstruction_with_progress(cumesh.remeshing, cumesh_directory / 'remeshing.py',
                                                         quad_progress)
    with torch.inference_mode():
        position, indices = reconstruct(
            position, indices, recipe['resolution'], band=recipe['remesh_band'],
            verbose=True, remove_inner_faces=recipe['remove_inner_faces'])
        if recipe['remove_floaters']:
            position, indices = _quad_floaters(toolkit, torch)(cumesh, position, indices)
    if not len(position) or not len(indices):
        raise ValueError('QuadDC returned an empty surface.')
    position = position.detach().to(device='cpu', dtype=torch.float32).contiguous()
    indices = indices.detach().to(device='cpu', dtype=torch.int64).contiguous()
    return position.numpy(), indices.numpy()


def rebuild_watertight(root, work, vertices, faces, *, options=None, progress=None):
    """Run the pinned WTiVo executable with Character 05's grid and component mode."""
    from wtivo_runtime import reconstruct

    recipe = _step('327', 'WTiVoNativeMeshToMesh', options)
    return reconstruct(root, work, vertices, faces, options=recipe, progress=progress)


def _surface_arrays(vertices, faces):
    import numpy as np

    vertices, faces = np.asarray(vertices), np.asarray(faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices) or vertices.dtype.kind != 'f'
            or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces) or faces.dtype.kind not in 'iu'
            or faces.min() < 0 or faces.max() >= len(vertices) or not np.isfinite(vertices).all()):
        raise ValueError('Character 05 requires finite vertices and indexed triangular faces.')
    return np.ascontiguousarray(vertices, dtype=np.float32), np.ascontiguousarray(faces, dtype=np.int64)


def _vertex_colors(colors, count):
    import numpy as np

    colors = np.asarray(colors, dtype=np.float32)
    if colors.shape not in ((count, 3), (count, 4)) or not np.isfinite(colors).all():
        raise ValueError('Character 05 vertex colors must match the input surface.')
    return colors


def trim_surface(toolkit, blender, work, vertices, faces, *, game_mesh, vertex_colors=None,
                 with_attributes=False, options=None):
    """LOD Tailor donor or game reduction from untextured geometry in decoded units."""
    import numpy as np
    import trimesh

    if trimesh.__version__ != '5.2.2':
        raise ValueError('The Character 05 LOD Tailor bridge requires trimesh 5.2.2.')
    script = _pinned_path(toolkit, LOD_SCRIPT)
    recipe = _step('335' if game_mesh else '328', 'LODTailorTheMeshTrimmer', options)
    work = Path(work).resolve()
    work.mkdir(exist_ok=False)
    source = work / 'mesh_input.glb'
    output = work / 'lodsmith_decimated.glb'
    vertices, faces = _surface_arrays(vertices, faces)
    mesh = trimesh.Trimesh(vertices, faces, process=False)
    channels = 3
    if vertex_colors is not None:
        colors = _vertex_colors(vertex_colors, len(vertices))
        channels = colors.shape[1]
        rgba = (np.clip(colors, 0, 1) * 255).astype(np.uint8)
        if channels == 3:
            rgba = np.column_stack((rgba, np.full(len(vertices), 255, dtype=np.uint8)))
        mesh.visual.vertex_colors = rgba
    mesh.export(str(source))
    args = ['--background', '--python', str(script), '--', '--input', str(source), '--output', str(output)]
    numbers = {'target_tris': '--tris', 'passes': '--passes', 'tolerance': '--tolerance',
               'intermediate_ratio': '--intermediate-ratio', 'last_ratio': '--last-ratio',
               'final_ratio': '--final-ratio', 'relative_voxel_size': '--relative-voxel-size',
               'minimum_voxel_size': '--minimum-voxel-size', 'seal_distance': '--seal-distance',
               'seal_max_steps': '--seal-max-steps'}
    flags = {'triangulate': '--triangulate', 'symmetry': '--symmetry', 'voxel_rebuild': '--voxel',
             'seal_keep_trying': '--seal-keep-trying'}
    for key, switch in numbers.items():
        args.extend((switch, str(recipe[key])))
    args.extend(switch for key, switch in flags.items() if recipe[key])
    _run_blender(blender, work, args, recipe['timeout_seconds'] or None)
    mesh = trimesh.load(str(output), force='mesh')
    mesh.merge_vertices(merge_tex=True, merge_norm=True)
    vertices, faces = _surface_arrays(mesh.vertices, mesh.faces)
    if not with_attributes:
        return vertices, faces
    if mesh.visual.kind == 'vertex':
        colors = np.asarray(mesh.visual.vertex_colors)
        colors = colors.astype(np.float32) / 255.0 if colors.dtype == np.uint8 else colors.astype(np.float32)
        colors = colors[:, :channels]
    else:
        colors = np.ones((len(vertices), channels), dtype=np.float32)
    return dict(vertices=vertices, faces=faces, vertex_colors=colors)


def _operator_source(source):
    source = Path(source).resolve(strict=True)
    _verified_path(source / 'LICENSE', COMFY_OPERATORS[Path('LICENSE')])
    return source


def _operator_tree(source, relative):
    path = _verified_path(source / relative, COMFY_OPERATORS[relative])
    return ast.parse(path.read_bytes(), filename=str(path))


def _select_functions(tree, names):
    operators = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in operators} != names:
        raise ValueError('The pinned Character 05 operator is missing.')
    return ast.Module(body=operators, type_ignores=[])


def _original_function(source, relative, name, bindings):
    operator = _select_functions(_operator_tree(source, relative), {name})
    exec(compile(operator, str(source / relative), 'exec'), bindings)
    return bindings[name]


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + '.' + node.attr
    return ''


class _DeviceBoundary(ast.NodeTransformer):
    def __init__(self):
        self.calls = []

    def visit_Import(self, node):
        if [(alias.name, alias.asname) for alias in node.names] == [('comfy.model_management', None)]:
            return None
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        name = _dotted(node.func)
        if name in UV_RUNTIME_CALLS:
            self.calls.append(name)
            node.func = ast.copy_location(ast.Name(id=UV_RUNTIME_CALLS[name], ctx=ast.Load()), node.func)
        return node


def _isolate_uv_device(tree, expected):
    patch = _DeviceBoundary()
    transformed = patch.visit(tree)
    if sorted(patch.calls) != sorted(expected):
        raise ValueError('The pinned PEC device boundary is different from the expected interface.')
    dependencies = ('comfy', 'comfy_api', 'comfy_extras')
    for node in ast.walk(transformed):
        if isinstance(node, ast.Name) and node.id in dependencies:
            raise ValueError('A PEC operator still requires ComfyUI.')
        imports = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        if any(name.split('.')[0] in dependencies for name in imports):
            raise ValueError('A PEC operator still requires ComfyUI.')
    return ast.fix_missing_locations(transformed)


def _load_module(name, path, tree, bindings):
    module = ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = name.rpartition('.')[0]
    module.__dict__.update(bindings)
    sys.modules[name] = module
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


@contextmanager
def _uv_operators(source, device, progress):
    import numpy as np
    import psutil
    import torch
    from tqdm import tqdm

    device = torch.device(device)
    modules = {relative: _operator_tree(source, relative)
               for relative in (*[UV_PATH / f'{name}.py' for name in ('mesh', 'segment', 'parameterize', 'pack')],
                                MESH_POSTPROCESS)}
    package = ModuleType(f'_character_pec_{uuid4().hex}')
    package.__path__ = []
    sys.modules[package.__name__] = package

    def free_memory(selected):
        if selected.type == 'cpu':
            return psutil.virtual_memory().available
        stats = torch.cuda.memory_stats(selected)
        free, _ = torch.cuda.mem_get_info(selected)
        return free + stats['reserved_bytes.all.current'] - stats['active_bytes.all.current']

    def update(done, total):
        if progress is not None:
            progress(done, total)

    bindings = {'_uv_device': lambda: device, '_uv_free_memory': free_memory}
    try:
        operators = {}
        for name in ('mesh', 'segment', 'parameterize', 'pack'):
            relative = UV_PATH / f'{name}.py'
            expected = (['comfy.model_management.get_torch_device', 'comfy.model_management.get_free_memory']
                        if name == 'pack' else [])
            tree = _isolate_uv_device(modules[relative], expected)
            operators['_uv_' + {'segment': 'seg', 'parameterize': 'param'}.get(name, name)] = _load_module(
                f'{package.__name__}.{name}', source / relative, tree, bindings)
        if operators['_uv_pack']._HAVE_NUMBA_PACK:
            raise ValueError('The Character 05 witness used the PyTorch packer. Use its pinned environment.')
        tree = _isolate_uv_device(
            _select_functions(modules[MESH_POSTPROCESS], {'_uv_weld_vertices', '_uv_unwrap'}),
            ['comfy.utils.ProgressBar', 'comfy.model_management.get_torch_device'])
        assembly = _load_module(
            f'{package.__name__}.assemble', source / MESH_POSTPROCESS, tree,
            {**operators, **bindings, '_uv_progress': lambda _: SimpleNamespace(update_absolute=update),
             'np': np, 'torch': torch, 'math': math, 'time': time, 'logging': logging, 'tqdm': tqdm})
        yield assembly._uv_unwrap
    finally:
        for name in tuple(sys.modules):
            if name == package.__name__ or name.startswith(package.__name__ + '.'):
                del sys.modules[name]


def unwrap_character_uv(source, vertices, faces, *, colors=None, device='cuda', progress=None, options=None):
    """Return the node 337 seam-split buffers, before the separate smoothing node."""
    import numpy as np
    import torch

    settings = _step('337', 'UnwrapMesh', options)
    source = _operator_source(source)
    vertices, faces = _surface_arrays(vertices, faces)
    if colors is not None:
        colors = _vertex_colors(colors, len(vertices))
    device = torch.device('cpu' if settings['segmenter'] == 'adaptive' else device)
    if device.type not in ('cpu', 'cuda'):
        raise ValueError('Choose a CPU or CUDA device for the pinned PEC implementation.')
    extent = float(np.linalg.norm(vertices.max(0) - vertices.min(0)))
    with _uv_operators(source, device, progress) as operation, torch.inference_mode():
        mapping, indices, uvs = operation(torch.from_numpy(vertices).to(device), torch.from_numpy(faces).to(device),
                                          settings['segmenter'], settings['resolution'], settings['padding'],
                                          settings['weld_distance'] * extent)
    if not len(mapping) or not len(indices) or not np.isfinite(uvs).all():
        raise ValueError('PEC returned an empty or invalid UV mesh.')
    uvs = uvs.copy()
    uvs[:, 1] = 1. - uvs[:, 1]  # glTF V orientation used by the pinned unwrap node
    result = dict(vertices=vertices[mapping], faces=indices.astype(np.int32),
                  uvs=uvs.astype(np.float32), vertex_mapping=mapping)
    if colors is not None:
        result['vertex_colors'] = np.ascontiguousarray(colors[mapping])
    return result



def _bake_body(source):
    """Read the standalone Blender code without importing the original node module."""
    tree = ast.parse(source.read_text(encoding='utf-8-sig'), filename=str(source))
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == 'BLENDER_BAKE_SCRIPT' for target in statement.targets):
            body = ast.literal_eval(statement.value)
            if isinstance(body, str):
                return body
    raise ValueError('The separately installed Bake Forger script is missing.')


def bake_normal_atlas(toolkit, blender, work, high_glb, low_glb, *, cpu_for_qa=False, options=None):
    """Run the standalone Blender body for the requested surface maps."""
    script_source = _pinned_path(toolkit, BAKE_SOURCE)
    settings = _step('341', 'LODTailorBakeForger', options)
    for key in ('high_poly', 'low_poly', 'blender_path', 'load_output_images'):
        settings.pop(key)
    for key in ('neutral_normal_color', 'material_base_color'):
        channels = [float(channel) for channel in settings[key].replace(';', ',').split(',')]
        if len(channels) not in (3, 4) or not all(0 <= channel <= 1 for channel in channels):
            raise ValueError('Bake colors must contain three or four channels between zero and one.')
        settings[key] = channels + [1.0] if len(channels) == 3 else channels
    if cpu_for_qa:
        settings.update(prefer_gpu_baking=False, hybrid_cpu_gpu_baking=False)
    high_glb = Path(high_glb).resolve(strict=True)
    low_glb = Path(low_glb).resolve(strict=True)
    work = Path(work).resolve()
    work.mkdir(exist_ok=False)
    script, config = work / 'bake_script.py', work / 'config.json'
    report_path, output = work / 'result.json', work / 'baked_low.glb'
    script.write_text(_bake_body(script_source), encoding='utf-8')
    config.write_text(json.dumps(dict(high_poly=str(high_glb), low_poly=str(low_glb), out_dir=str(work),
                                      glb_out=str(output), result_json=str(report_path), settings=settings)),
                      encoding='utf-8')
    flags = ['--factory-startup', '-noaudio', '-b']
    if cpu_for_qa:
        flags.extend(('-t', '4'))
    _run_blender(blender, work, [*flags, '-P', str(script), '--', str(config)], 1800)
    if not report_path.is_file() or not output.is_file():
        raise RuntimeError('Bake Forger did not write an output GLB. See blender.log.')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('status') != 'FINISHED' or Path(report['glb']).resolve() != output:
        raise RuntimeError('Bake Forger did not finish the normal map. See blender.log.')
    return output, report


class _GLBBytes:
    """Carry the original MIT tool's bytes without its ComfyUI File3D class."""

    def __init__(self, source, file_format):
        if file_format != 'glb':
            raise ValueError('Fast Merge requires a GLB.')
        self.source = source

    def get_bytes(self):
        return self.source.getvalue()


def _reject_python_weld(*args, **kwargs):
    raise RuntimeError('Character 05 requires the verified native Fast Merge operation.')


def texture_safe_merge(toolkit, source, output, *, options=None):
    """Reuse the original MIT GLB weld with its native DLL and exact retry settings."""
    original = _pinned_path(toolkit, f'{MERGE_TOOL}/node.py')
    for file in ('native.py', 'python_weld.py', 'native/wtivo_fast_weld_mt.dll'):
        _pinned_path(toolkit, f'{MERGE_TOOL}/{file}')
    tree = ast.parse(original.read_text(encoding='utf-8-sig'), filename=str(original))
    carrier, = [statement for statement in tree.body
                if isinstance(statement, ast.ImportFrom) and statement.module == 'comfy_api.latest']
    tree.body.remove(carrier)
    package = ModuleType(f'_character_fast_merge_{uuid4().hex}')
    package.__path__ = [str(original.parent)]
    node = ModuleType(f'{package.__name__}.node')
    node.__package__ = package.__name__
    node.__file__ = str(original)
    node.Types = SimpleNamespace(File3D=_GLBBytes)
    sys.modules[package.__name__] = package
    sys.modules[node.__name__] = node
    try:
        exec(compile(tree, str(original), 'exec'), node.__dict__)
        if not node.native_available():
            raise RuntimeError('The verified Character 05 Fast Merge DLL did not load.')
        node.python_weld = _reject_python_weld
        recipe = _step('342', 'WTiVoFastMergeByDistance', options)
        recipe.pop('model')
        data = Path(source).read_bytes()
        merged, = node.WTiVoFastMergeByDistance().execute(_GLBBytes(io.BytesIO(data), 'glb'), **recipe)
        with Path(output).open('xb') as stream:
            stream.write(merged.get_bytes())
    finally:
        for name in tuple(sys.modules):
            if name == package.__name__ or name.startswith(package.__name__ + '.'):
                del sys.modules[name]


def write_character_glb(source, output, surface, *, smooth=False, options=None):
    """Serialize the donor or low surface using the pinned mesh export operator."""
    import numpy as np
    import torch

    _step('339' if smooth else '333', 'MeshToFile3D')
    source = _operator_source(source)
    vertices, faces = _surface_arrays(surface['vertices'], surface['faces'])
    colors = surface.get('vertex_colors')
    if colors is not None:
        colors = _vertex_colors(colors, len(vertices))
    uvs = surface.get('uvs')
    if uvs is not None:
        uvs = np.asarray(uvs, dtype=np.float32)
        if uvs.shape != (len(vertices), 2) or not np.isfinite(uvs).all():
            raise ValueError('Character 05 UV coordinates must match the low surface.')
    normals = None
    if smooth:
        crease = _step('338', 'MeshSmoothNormals', options)['crease_angle']
        if crease == 180:
            operation = _original_function(source, MESH_POSTPROCESS, '_smooth_vertex_normals', {'np': np})
            normals = operation(vertices, faces)
        else:
            operation = _original_function(source, MESH_POSTPROCESS, '_compute_vertex_face_normals',
                                           {'np': np, 'math': math})
            vertices, faces, normals, remap = operation(vertices, faces, crease)
            uvs = uvs[remap] if uvs is not None else None
            colors = colors[remap] if colors is not None else None
    export = _original_function(source, MESH_SERIALIZER, 'save_glb',
                                dict(np=np, BytesIO=io.BytesIO, json=json, struct=struct, logging=logging))
    attributes = {'vertex_colors': colors, 'uvs': uvs, 'normals': normals}
    tensors = {key: torch.from_numpy(np.ascontiguousarray(value)) for key, value in attributes.items()
               if value is not None}
    data = export(torch.from_numpy(vertices), torch.from_numpy(faces), **tensors)
    with Path(output).open('xb') as stream:
        stream.write(data)


def _normal_ready_model(model, resolution):
    import numpy as np
    from glb_data import accessor, image, read

    data, binary = read(model)
    primitive, = [primitive for mesh in data['meshes'] for primitive in mesh['primitives']]
    if not {'POSITION', 'NORMAL', 'TEXCOORD_0'} <= primitive['attributes'].keys():
        raise ValueError('Character 05 bake did not retain positions, normals and UVs.')
    material = data['materials'][primitive['material']]
    texture = material.get('normalTexture')
    if texture is None and resolution is not None:
        raise ValueError('Character 05 bake did not write a tangent-space normal texture.')
    if texture is not None:
        source = data['textures'][texture['index']]['source']
        size = image(data, binary, source).size
        if size != (resolution, resolution):
            raise ValueError('Character 05 bake did not write the expected normal texture resolution.')
    vertices = accessor(data, binary, primitive['attributes']['POSITION'])
    faces = accessor(data, binary, primitive['indices']).reshape(-1, 3)
    if len(faces):
        vertices, faces = _surface_arrays(vertices, faces)
    corners = vertices[faces].astype(np.float64)
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    surface_triangles = int(np.count_nonzero(np.any(cross != 0, axis=1)))
    return dict(triangles=len(faces), surface_triangles=surface_triangles, normal_size=resolution)


def finish_reduced_character(toolkit, blender, comfy_source, work, *, donor, low, device='cuda',
                             cpu_bake_for_qa=False, progress=None, options=None):
    """Run nodes 333/337/338/339/341/342 on the post-LOD donor and low mesh buffers."""
    work = Path(work).resolve()
    work.mkdir(exist_ok=False)
    progress = progress or (lambda label: None)
    started = time.perf_counter()
    progress('Preparing the UV layout')
    high_glb, low_glb = work / 'input_high.glb', work / 'input_low.glb'
    bake_settings = _step('341', 'LODTailorBakeForger', options)
    bake_enabled = any(bake_settings[name] for name in BAKE_MAP_OPTIONS)
    if bake_enabled:
        write_character_glb(comfy_source, high_glb, donor)
    unwrapped = unwrap_character_uv(comfy_source, low['vertices'], low['faces'],
                                    colors=low.get('vertex_colors'), device=device, options=options)
    progress('Smoothing the unwrapped mesh normals')
    write_character_glb(comfy_source, low_glb, unwrapped, smooth=True, options=options)
    unwrap_export_seconds = time.perf_counter() - started
    if str(device).split(':', 1)[0] == 'cuda':
        import torch
        torch.cuda.empty_cache()
    started = time.perf_counter()
    if bake_enabled:
        progress('Baking the normal map' if bake_settings['bake_normal_detail']
                 else 'Baking the surface maps')
        baked, bake_report = bake_normal_atlas(toolkit, blender, work / 'normal-bake', high_glb,
                                             low_glb, cpu_for_qa=cpu_bake_for_qa, options=options)
    else:
        progress('Skipping surface map baking')
        baked, bake_report = low_glb, {}
    bake_seconds = time.perf_counter() - started
    normal_size = bake_settings['normal_map_resolution'] if bake_settings['bake_normal_detail'] else None
    baked_report = _normal_ready_model(baked, normal_size)
    if not baked_report['surface_triangles']:
        raise ValueError('The Game Asset mesh has no usable triangles after surface map baking.')
    progress('Merging vertices on the textured mesh')
    started = time.perf_counter()
    model = work / 'normal-ready.glb'
    texture_safe_merge(toolkit, baked, model, options=options)
    welded_report = _normal_ready_model(model, normal_size)
    warnings = list(bake_report.get('warnings', []))
    print(f'Game Asset triangles: {len(low["faces"]):,} reduced, {len(unwrapped["faces"]):,} unwrapped, '
          f'{baked_report["triangles"]:,} baked, {welded_report["triangles"]:,} after merge.', flush=True)
    model_report = welded_report
    if welded_report['surface_triangles'] < baked_report['surface_triangles']:
        warning = (f'Vertex merge reduced the usable triangle count from {baked_report["surface_triangles"]:,} '
                   f'to {welded_report["surface_triangles"]:,}. Keeping the baked mesh for UltraTex.')
        warnings.append(warning)
        print(warning, flush=True)
        baked.replace(model)
        model_report = baked_report
    uv_device = 'cpu' if _step('337', 'UnwrapMesh', options)['segmenter'] == 'adaptive' else str(device)
    return model, dict(donor_triangles=len(donor['faces']), reduced_triangles=len(low['faces']),
                       unwrapped_triangles=len(unwrapped['faces']), **model_report,
                       baked_triangles=baked_report['triangles'], welded_triangles=welded_report['triangles'],
                       unwrap_export_seconds=round(unwrap_export_seconds, 3),
                       bake_seconds=round(bake_seconds, 3), merge_seconds=round(time.perf_counter() - started, 3),
                       warnings=warnings, uv_device=uv_device, cpu_bake=cpu_bake_for_qa)


def finish_character_mesh(root, toolkit, cumesh_directory, blender, comfy_source, work, vertices, faces,
                          *, input_stage='decoded', device='cuda', progress=None, options=None,
                          quad_progress=None, wtivo_progress=None):
    """Build the normal-ready Character 05 low-poly surface in a new working directory."""
    if input_stage not in ('decoded', 'watertight'):
        raise ValueError('Provide the decoded shape or an explicitly archived WTiVo surface.')
    work = Path(work).resolve()
    work.mkdir(exist_ok=False)
    progress = progress or (lambda label: None)
    before = {}
    if input_stage == 'decoded':
        import torch
        progress('Reconstructing the mesh with QuadDC')
        started = time.perf_counter()
        vertices, faces = reconstruct_quad(toolkit, cumesh_directory, vertices, faces, options=options,
                                           quad_progress=quad_progress)
        before.update(quad_triangles=len(faces), quad_seconds=round(time.perf_counter() - started, 3))
        torch.cuda.empty_cache()
        progress('Closing the mesh with WTiVo')
        vertices, faces, before['watertight'] = rebuild_watertight(
            root, work, vertices, faces, options=options, progress=wtivo_progress)
    progress('Reducing the dense donor with LODTailor')
    started = time.perf_counter()
    donor = trim_surface(toolkit, blender, work / 'lod-donor', vertices, faces, game_mesh=False,
                         with_attributes=True, options=options)
    progress('Reducing the game mesh with LODTailor')
    low = trim_surface(toolkit, blender, work / 'lod-game', donor['vertices'], donor['faces'], game_mesh=True,
                       vertex_colors=donor['vertex_colors'], with_attributes=True, options=options)
    before['lod_seconds'] = round(time.perf_counter() - started, 3)
    result, report = finish_reduced_character(toolkit, blender, comfy_source, work / 'uv-bake',
                                              donor=donor, low=low, device=device, progress=progress, options=options)
    return result, dict(input_stage=input_stage, **before, **report)
