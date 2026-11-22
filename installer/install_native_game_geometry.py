"""Provision the isolated native Character 05 geometry and mesh-finishing components."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from installer.native_workflow_install_common import (
    extract, fetch_asset, install_packages, safe_path, verify_asset, write_json,
)
from installer.native_workflow_python import RUNTIME_PATH
from native_game_finish import COMFY_OPERATORS, EXPECTED_SOURCE, QUAD_WHEEL, RECIPE_SHA256, TOOLKIT_LICENSES
from native_pixal_prepare import prepare
from wtivo_runtime import PINNED_FILES, _read_runtime


HERE = Path(__file__).resolve().parent
SHAPE = Path('runtime/native-pixal3d')
GEOMETRY = Path('runtime/native-game-geometry')
PACKAGES = SHAPE / 'packages'
DEPS = PACKAGES / 'studio-deps'
BLENDER = Path('.tools/blender-5.2.2')
WTIVO = Path('.tools/wtivo/source')
WEIGHTS = Path('models/native-pixal3d')
CACHE = Path('.tools/native-workflow-downloads/game-geometry')
GEOMETRY_CONFIG = GEOMETRY / 'runtime.json'
SHAPE_CONFIG = Path('config/native-pixal3d.json')
WTIVO_CONFIG = Path('config/wtivo-runtime.json')
SHAPE_RECEIPT = Path('config/native-pixal3d-verified.json')
RECIPE = GEOMETRY / 'character-05-recipe.json'
LOCK = HERE / 'requirements-native-game-geometry.txt'
ENGINES = ('pixal3d', 'trellis2')


def _manifest():
    spec = json.loads((HERE / 'native-game-geometry-manifest.json').read_text(encoding='utf-8'))
    if spec['schema_version'] != 1:
        raise ValueError('Unsupported native Game Asset manifest version.')
    return spec


def _hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _check_files(directory, pins):
    for filename, digest in pins.items():
        path = safe_path(directory, filename)
        if not path.is_file() or _hash(path) != digest:
            raise ValueError(f'Unverified Game Asset component: {path.name}.')


def _copy_file(root, destination, source):
    destination = safe_path(root, destination)
    if destination.exists():
        if destination.is_file() and _hash(source) == _hash(destination):
            return
        raise ValueError(f'An existing Game Asset component differs and was preserved: {destination}.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.game-file-', dir=destination.parent) as work:
        pending = Path(work) / destination.name
        shutil.copyfile(source, pending)
        pending.rename(destination)


def _copy_selection(root, source, destination, names):
    for name in names:
        _copy_file(root, destination / name, safe_path(source, name))


def _cache_asset(root, entry):
    return fetch_asset(root, entry, CACHE / entry['filename'])


def _extract_sources(root, entry, target):
    archive = _cache_asset(root, entry)
    extract(archive, target, strip_root=True)


def _install_operators(root, spec):
    cache = safe_path(root, CACHE)
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.operators-', dir=cache) as work:
        stage = Path(work) / 'selected'
        for entry in spec['operators']:
            fetch_asset(root, entry, stage / entry['path'])
        architecture = safe_path(root, SHAPE / 'native_shape_architecture')
        generated = Path(work) / 'shape'
        prepare(stage, generated)
        _copy_selection(root, generated, architecture, [item.name for item in generated.iterdir()])
        _copy_selection(root, stage, GEOMETRY / 'uv-source', COMFY_OPERATORS)
        for entry in spec['toolkits']:
            _extract_sources(root, entry, stage / entry['filename'])
            _copy_selection(root, stage / entry['filename'], Path(entry['destination']), entry['files'])

    recipe = safe_path(root, RECIPE)
    recipe.parent.mkdir(parents=True, exist_ok=True)
    source = safe_path(root, 'comfy_workflows/game-asset-character-05.json')
    with TemporaryDirectory(prefix='.recipe-', dir=recipe.parent) as work:
        pending = Path(work) / recipe.name
        # The qualified Windows recipe was written with CRLF line endings.
        pending.write_bytes(source.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        _check_files(pending.parent, {pending.name: RECIPE_SHA256})
        _copy_file(root, RECIPE, pending)
    license_name = 'PIXELARTISTRY-MIT-LICENSE.txt'
    _copy_file(root, GEOMETRY / license_name, safe_path(root, Path('comfy_workflows') / license_name))


def _install_vision(root, spec):
    for entry in (*spec['vision_sources'], spec['birefnet_license']):
        fetch_asset(root, entry, entry['destination'])
    with TemporaryDirectory(prefix='.vision-', dir=safe_path(root, CACHE)) as work:
        for entry in spec['vision_archives']:
            source = Path(work) / entry['filename']
            target = Path(entry['destination'])
            _extract_sources(root, entry, source)
            for relative in entry['roots']:
                path = safe_path(source, relative)
                selected = [item.relative_to(source) for item in path.rglob('*') if item.is_file()] if path.is_dir() else [relative]
                _copy_selection(root, source, target, selected)
    for entry in spec['weights']:
        fetch_asset(root, entry, WEIGHTS / entry['path'])


def _blender_status(root, python):
    command = [python, '-I', '-B', '-X', 'utf8', '-c',
               'import sys; sys.path.insert(0, sys.argv[1]); '
               'from native_game_geometry import find_game_blender; print(find_game_blender() or "")', root]
    blender = subprocess.check_output(command, cwd=root, text=True, encoding='utf-8').strip()
    return Path(blender) if blender else None


def _install_blender(root, python, entry):
    if _blender_status(root, python):
        return
    archive = _cache_asset(root, entry)
    target = safe_path(root, BLENDER)
    if not target.exists():
        with TemporaryDirectory(prefix='.blender-', dir=target.parent) as work:
            staging = Path(work) / 'blender'
            extract(archive, staging, strip_root=True)
            staging.rename(target)
    if not _blender_status(root, python):
        raise ValueError('Game Asset requires a working Blender 5.2 or later. Check the configured Blender path.')


def _install_wtivo(root, spec):
    config = safe_path(root, WTIVO_CONFIG)
    settings = {'python': str(safe_path(root, RUNTIME_PATH)), 'source': str(safe_path(root, WTIVO))}
    if config.exists() and json.loads(config.read_text(encoding='utf-8-sig')) != settings:
        _read_runtime(root)
        return
    try:
        _read_runtime(root)
        return
    except ValueError:
        pass
    for entry in spec['wtivo']:
        target = fetch_asset(root, entry, entry['destination'])
        if target.suffix.lower() == '.rar':
            archive = target
    notice = 'build/CELLOCUT-BACKEND-LICENSE-Apache-2.0.txt'
    native = {name: digest for name, digest in PINNED_FILES.items() if name.startswith('build/')}
    native[notice] = '834cded510f4c190fcf171d7f95525ab375822d93bfe09285e0205e355ce9796'
    with TemporaryDirectory(prefix='.wtivo-', dir=safe_path(root, CACHE)) as work:
        tar = shutil.which('tar.exe')
        if not tar:
            raise ValueError('Windows tar.exe is required to extract the pinned WTiVo runtime.')
        members = subprocess.check_output([tar, '-tf', archive], cwd=work, text=True).splitlines()
        if set(members) - {'build', 'build/'} != set(native):
            raise ValueError('Unexpected WTiVo archive members.')
        subprocess.run([tar, '-xf', archive], cwd=work, check=True)
        stage = Path(work)
        _check_files(stage, native)
        _copy_selection(root, stage, WTIVO, native)
    _check_files(safe_path(root, WTIVO), PINNED_FILES)
    if not config.exists():
        write_json(root, WTIVO_CONFIG, settings)


def _install_wheels(root, python, spec):
    dependencies = install_packages(root, python, DEPS, LOCK)
    for entry in spec['wheels'].values():
        wheel = _cache_asset(root, entry)
        extract(wheel, dependencies)
    _check_files(dependencies / 'cumesh', QUAD_WHEEL)
    cache = safe_path(root, SHAPE / 'triton-cache')
    cache.mkdir(parents=True, exist_ok=True)


def _configurations():
    def shape_path(path):
        return '../' + Path(path).as_posix()

    shape = {
        'python': shape_path(RUNTIME_PATH), 'architecture': shape_path(SHAPE),
        'weights': shape_path(WEIGHTS), 'birefnet': shape_path(PACKAGES / 'birefnet_native'),
        'moge': shape_path(PACKAGES / 'moge'),
        'dependencies': [shape_path(path) for path in (DEPS, PACKAGES / 'utils3d_moge')],
        'environment': {
            'CC': shape_path(DEPS / 'triton/runtime/tcc/tcc.exe'),
            'CUDA_PATH': shape_path(DEPS / 'triton/backends/nvidia'),
            'TRITON_CACHE_DIR': shape_path(SHAPE / 'triton-cache'),
        },
    }
    geometry = {
        'schema_version': 1, 'recipe': 'game-asset-character-05', 'studio_python': RUNTIME_PATH,
        'operator_source': 'uv-source', 'toolkit': 'toolkit',
        'dependencies': '../native-pixal3d/packages/studio-deps',
        'cumesh': '../native-pixal3d/packages/studio-deps/cumesh',
        'blender_discovery': 'native_game_geometry.find_game_blender', 'blender_version': '5.2.2',
    }
    return {SHAPE_CONFIG: shape, GEOMETRY_CONFIG: geometry}


PROBE_BASE = """
import importlib.abc
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
blocked = {'comfy', 'comfy_api', 'comfy_extras', 'folder_paths', 'execution'}

class NoHost(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.', 1)[0] in blocked:
            raise ImportError(f'Unexpected UI host import: {fullname}')

sys.meta_path.insert(0, NoHost())
"""


PROBE = PROBE_BASE + """
import os

ENGINES = ('pixal3d', 'trellis2')
from native_pixal3d import CHECKPOINTS, _activate_dependencies, _config
paths = _config('pixal3d')
for backend in ENGINES:
    _config(backend)

# Use precisely the dependency order used by the native worker.
_activate_dependencies(paths)
os.environ.update(paths['environment'])
import numpy
from native_game_asset import BIREFNET_REVISION, MOGE_REVISION, _namespace_module
import torch
import torchvision
import transformers
from safetensors import safe_open
from comfy_kitchen import apply_rope
from native_shape_architecture.model import SLatFlowModel, SparseStructureFlowModel, Trellis2
from native_shape_architecture.vae import ShapeVae
from torchvision.ops import deform_conv2d
import triton

assert torch.__version__ == '2.8.0+cu128' and torch.cuda.is_available()
assert torchvision.__version__ == '0.23.0+cu128'
assert numpy.__version__ == '1.26.4'
assert transformers.DINOv3ViTModel
for backend in ENGINES:
    with safe_open(str(paths['weights'] / CHECKPOINTS[backend]), framework='pt', device='cpu') as weights:
        assert any('qkv' in name for name in weights.keys()), backend

mask = _namespace_module(f'_studio_birefnet_{BIREFNET_REVISION[:7]}', paths['birefnet'], 'birefnet')
camera = _namespace_module(f'_studio_moge_{MOGE_REVISION[:7]}', paths['moge'] / 'moge', 'model.v2')
assert mask.BiRefNet and camera.MoGeModel
image = torch.ones((1, 1, 3, 3), device='cuda')
offset = torch.zeros((1, 2, 3, 3), device='cuda')
kernel = torch.ones((1, 1, 1, 1), device='cuda')
assert deform_conv2d(image, offset, kernel).sum().item() == 9
assert not blocked.intersection(sys.modules)
assert all(not Path(path).is_relative_to(root / 'qa') for path in sys.path)
"""


FINISH_PROBE = PROBE_BASE + """
from native_game_geometry import _runtime

geometry = _runtime()
sys.path[:0] = [str(geometry['dependencies']), str(root)]
import numpy
import pydoc
from scipy.sparse import linalg
import torch
import cumesh
from native_game_finish import _quad_floaters, unwrap_character_uv

assert callable(linalg.spsolve)
assert callable(cumesh.remeshing.reconstruct_mesh_dc_quad)
assert Path(cumesh.__file__).resolve().parent == geometry['cumesh']
assert callable(_quad_floaters(geometry['toolkit'], torch))
vertices = numpy.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=numpy.float32)
faces = numpy.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=numpy.int32)
unwrapped = unwrap_character_uv(geometry['operator_source'], vertices, faces, device='cuda')
assert len(unwrapped['faces']) == len(faces) and numpy.isfinite(unwrapped['uvs']).all()
assert not blocked.intersection(sys.modules)
assert all(not Path(path).is_relative_to(root / 'qa') for path in (*sys.path, pydoc.__file__))
"""


def verify(root):
    """Check both native engines, finishing operators and the external WTiVo backend."""
    root = Path(root).resolve(strict=True)
    python = safe_path(root, RUNTIME_PATH)
    geometry = safe_path(root, GEOMETRY)
    spec = _manifest()
    shape = json.loads(safe_path(root, SHAPE_CONFIG).read_text(encoding='utf-8-sig'))
    weights = (root / 'config' / shape['weights']).resolve(strict=True)
    for entry in spec['weights']:
        verify_asset(safe_path(weights, entry['path']), entry)
    for entry in spec['vision_sources']:
        folder = (root / 'config' / shape['birefnet']).resolve(strict=True)
        verify_asset(safe_path(folder, entry['filename']), entry)
    geometry_settings = json.loads(safe_path(root, GEOMETRY_CONFIG).read_text(encoding='utf-8-sig'))
    source = (geometry / geometry_settings['operator_source']).resolve(strict=True)
    toolkit = (geometry / geometry_settings['toolkit']).resolve(strict=True)
    cumesh = (geometry / geometry_settings['cumesh']).resolve(strict=True)
    _check_files(source, COMFY_OPERATORS)
    _check_files(toolkit, EXPECTED_SOURCE | {f'{name}/LICENSE': digest for name, digest in TOOLKIT_LICENSES.items()})
    _check_files(cumesh, QUAD_WHEEL)
    _check_files(geometry, {RECIPE.name: RECIPE_SHA256})
    if not _blender_status(root, python):
        raise ValueError('Game Asset requires a working Blender 5.2 or later.')
    runtime = _read_runtime(root)
    env = dict(os.environ, HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TRANSFORMERS_OFFLINE='1')
    subprocess.run([python, '-I', '-B', '-c', PROBE, root], cwd=root, env=env, check=True, timeout=180)
    finish_python = (root / geometry_settings['studio_python']).resolve(strict=True)
    subprocess.run([finish_python, '-I', '-B', '-c', FINISH_PROBE, root], cwd=root, env=env, check=True, timeout=120)
    env['WTIVO_SUBPROCESS'] = '1'
    subprocess.run([runtime.python, '-I', '-B', '-c',
                    'import runpy, sys; runpy.run_path(sys.argv[1])', runtime.source / 'wtivo.py'],
                   cwd=root, env=env, check=True, timeout=90)
    return dict.fromkeys(ENGINES, True)


def install(root: Path):
    """Install the selected public sources and validate the two native shape backends."""
    root = Path(root).resolve(strict=True)
    python = safe_path(root, RUNTIME_PATH)
    if not python.is_file():
        raise ValueError('Install the shared native workflow Python runtime first.')
    spec = _manifest()
    settings = _configurations()
    existing = {name: json.loads(safe_path(root, name).read_text(encoding='utf-8-sig'))
                for name in settings if safe_path(root, name).is_file()}
    if existing.keys() == settings.keys():
        try:
            result = verify(root)
        except (OSError, ValueError, subprocess.SubprocessError):
            if existing != settings:
                raise
        else:
            write_json(root, SHAPE_RECEIPT, result)
            return result
    if existing.keys() != settings.keys() or existing == settings:
        _install_operators(root, spec)
        _install_vision(root, spec)
        _install_wheels(root, python, spec)
        for name in settings.keys() - existing.keys():
            write_json(root, name, settings[name])
    _install_blender(root, python, spec['blender'])
    _install_wtivo(root, spec)
    result = verify(root)
    write_json(root, SHAPE_RECEIPT, result)
    return result
