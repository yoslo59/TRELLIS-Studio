"""Provision the Windows High Detail GGUF workflow without a ComfyUI host."""

import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory

from installer.native_workflow_install_common import (
    fetch_asset, install_packages, safe_path, verify_asset, verify_packages,
)
from installer.setup import digest, extract
from native_high_detail import WEIGHT_REVISION, _runtime
from native_high_detail_portable import MANIFEST, PIPELINE_REVISION, QUANT_REVISION, prepare_portable_package


INSTALLER = Path(__file__).resolve().parent
MANIFEST_PATH = INSTALLER / 'native-high-detail-manifest.json'
LOCK_PATH = INSTALLER / 'requirements-native-high-detail.txt'
RUNTIME = Path('runtime/native-high-detail')
DOWNLOADS = Path('.downloads/native-high-detail')
WEIGHTS = Path('models/trellis2-gguf/weights')
DINO = Path('engine/MODELS/dinov3')
CONFIG = Path('config/native-high-detail.json')
PYTHON = Path('.venv/Scripts/python.exe')
TRITON = Path('.venv/Lib/site-packages/triton')
PACKAGE = RUNTIME / 'library'
TENSOR_PACKAGES = RUNTIME / 'workflow-deps-v1'
CUDA_PACKAGES = RUNTIME / 'cuda-deps-v1'

EXPECTED_DISTRIBUTIONS = {
    'cumesh': '1.0',
    'custom-rasterizer': '0.1',
    'flex-gemm': '0.0.1',
    'nvdiffrast': '0.4.0',
    'nvdiffrec-render': '0.0.0',
    'o-voxel': '0.0.1',
}


def _manifest():
    spec = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    if (spec['schema'], spec['weights_revision'], spec['pipeline']['revision'], spec['quantization']['revision']) != (
            1, WEIGHT_REVISION, PIPELINE_REVISION, QUANT_REVISION):
        raise ValueError('Unsupported High Detail provisioning manifest.')
    return spec


def _config():
    def relative(path):
        return '../' + path.as_posix()

    return {
        'schema': 1,
        'package': relative(PACKAGE),
        'models': relative(WEIGHTS),
        'dinov3': relative(DINO),
        'u2net': '../models/rembg',
        'python_paths': [relative(TENSOR_PACKAGES), relative(CUDA_PACKAGES)],
        'environment': {
            'CC': relative(TRITON / 'runtime/tcc/tcc.exe'),
            'CUDA_PATH': relative(TRITON / 'backends/nvidia'),
            'TRITON_CACHE_DIR': relative(RUNTIME / 'triton-cache'),
        },
    }


def _toolchain(root):
    python = safe_path(root, PYTHON)
    compiler = safe_path(root, TRITON / 'runtime/tcc/tcc.exe')
    cuda = safe_path(root, TRITON / 'backends/nvidia')
    if not (python.is_file() and compiler.is_file() and cuda.is_dir()):
        raise ValueError('Install the Studio Windows CUDA foundation before High Detail.')
    return python


def _package_receipt(package):
    receipt = json.loads(safe_path(package, MANIFEST).read_text(encoding='utf-8'))
    if (receipt.get('schema'), receipt.get('trellis_gguf_revision'), receipt.get('quant_revision')) != (
            1, PIPELINE_REVISION, QUANT_REVISION):
        raise ValueError(f'Existing High Detail source was preserved: {package}')
    sources = receipt['sources']
    required = {'model_manager.py', 'native_high_detail_dequant.py', 'trellis2_gguf/pipelines/base.py',
                'trellis2_gguf/pipelines/trellis2_image_to_3d.py', 'trellis2_gguf/utils/gguf_utils.py'}
    if not required.issubset(sources) or any(digest(safe_path(package, name)) != expected
                                            for name, expected in sources.items()):
        raise ValueError(f'Existing High Detail source differs from its receipt and was preserved: {package}')
    for name in ('TRELLIS-LICENSE.txt', 'GGUF-LICENSE.txt'):
        if not safe_path(package, name).is_file():
            raise ValueError(f'High Detail source license is missing: {name}')


def _verify_cuda_packages(folder, patches):
    variants = {item.metadata.get('Name', '').lower().replace('_', '-'): item.version
                for item in importlib.metadata.distributions(path=[str(folder)])}
    if variants != EXPECTED_DISTRIBUTIONS:
        raise ValueError(f'Existing High Detail CUDA packages differ and were preserved: {folder}')
    for patch in patches:
        if digest(safe_path(folder, patch['destination'])) != patch['sha256']:
            raise ValueError(f'Existing High Detail CUDA patch differs and was preserved: {patch["destination"]}')


def _install_cuda_packages(root, python, spec, pipeline_source):
    target = safe_path(root, CUDA_PACKAGES)
    if target.exists():
        _verify_cuda_packages(target, spec['patches'])
        return
    wheels = [fetch_asset(root, entry, DOWNLOADS / entry['name'])
              for entry in spec['windows_wheels']]
    uv = safe_path(root, '.tools/uv/uv.exe')
    with TemporaryDirectory(prefix='.hd-cuda-', dir=target.parent) as temporary:
        stage = Path(temporary) / 'cuda'
        subprocess.run([uv, 'pip', 'install', '--no-config', '--offline', '--no-index', '--no-deps',
                        '--python', python, '--target', stage, *wheels], cwd=root, check=True)
        for patch in spec['patches']:
            source = safe_path(pipeline_source, patch['source'])
            destination = safe_path(stage, patch['destination'])
            if digest(source) != patch['sha256'] or not destination.is_file():
                raise ValueError(f'The pinned High Detail CUDA patch could not be applied: {patch["source"]}')
            shutil.copyfile(source, destination)
        _verify_cuda_packages(stage, spec['patches'])
        stage.rename(target)


def _install_sources(root, python, spec):
    package = safe_path(root, PACKAGE)
    cuda = safe_path(root, CUDA_PACKAGES)
    if package.exists():
        _package_receipt(package)
    if package.exists() and cuda.exists():
        _verify_cuda_packages(cuda, spec['patches'])
        return

    archive = fetch_asset(root, spec['pipeline'], DOWNLOADS / 'pipeline.zip')
    package.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.hd-sources-', dir=package.parent) as temporary:
        stage = Path(temporary)
        pipeline_source = stage / 'pipeline'
        extract(archive, pipeline_source, strip_root=True)
        _install_cuda_packages(root, python, spec, pipeline_source)
        if not package.exists():
            quant_archive = fetch_asset(root, spec['quantization'], DOWNLOADS / 'quantization.zip')
            quant = stage / 'quantization'
            extract(quant_archive, quant, strip_root=True)
            prepare_portable_package(pipeline_source, quant, package)
            _package_receipt(package)


def _foundation_weights():
    foundation = json.loads((INSTALLER / 'manifest.json').read_text(encoding='utf-8'))
    mask = next(entry for entry in foundation['assets'] if entry['path'] == 'models/rembg/u2net.onnx')
    dino = [entry for entry in foundation['extracted_files']
            if Path(entry['path']).parent == DINO]
    return mask, dino


def _install_weights(root, spec):
    mask, dino = _foundation_weights()
    fetch_asset(root, mask, mask['path'])

    # The High Detail engine reads Foundation's exact checkpoint in place.
    for entry in dino:
        verify_asset(safe_path(root, entry['path']), entry)

    for entry in spec['assets']:
        fetch_asset(root, entry, entry['path'])


def _smoke(root, python, config):
    _runtime(config)
    script = '''
import importlib.abc
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
blocked = {'comfy', 'comfy_api', 'comfy_extras', 'folder_paths', 'gguf_native'}
class NoHost(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.', 1)[0] in blocked:
            raise ImportError(f'Unexpected UI host import: {fullname}')
sys.meta_path.insert(0, NoHost())

from native_high_detail import _bind_dino_checkpoint, _configure, _runtime
runtime = _runtime(sys.argv[2])
with tempfile.TemporaryDirectory(prefix='studio-hd-smoke-') as temporary:
    _configure(runtime, 'xformers', Path(temporary))
    import torch
    import _nvdiffrast_c
    import cumesh
    import gguf
    import pymeshlab
    from meshlib import mrmeshnumpy, mrmeshpy
    from rembg import remove
    from transformers import DINOv3ViTConfig
    from trellis2_gguf.pipelines.trellis2_image_to_3d import Trellis2ImageTo3DPipeline
    from xformers import _cpp_lib

    assert torch.__version__ == '2.8.0+cu128' and torch.version.cuda == '12.8'
    assert _cpp_lib._cpp_library_load_exception is None, _cpp_lib._cpp_library_load_exception
    assert callable(cumesh.remeshing.reconstruct_mesh_dc_quad)
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(
        str(runtime['models'] / 'Trellis2'), keep_models_loaded=False,
        enable_gguf=True, gguf_quant='Q4_K_M', isPixal3D=False,
    )
    _bind_dino_checkpoint(pipeline, runtime)
    model = Path(pipeline._pretrained_args['image_cond_model']['args']['model_name'])
    assert model == runtime['dinov3']
    DINOv3ViTConfig.from_json_file(model / 'config.json')
    assert not blocked.intersection(sys.modules)
'''
    subprocess.run([python, '-I', '-B', '-c', script, INSTALLER.parent, config], cwd=root, check=True)


def verify(root: Path):
    """Check the configured High Detail source, locked assets, and native imports."""
    root = Path(root).resolve(strict=True)
    python = _toolchain(root)
    config = safe_path(root, CONFIG)
    runtime = _runtime(config)
    spec = _manifest()
    _package_receipt(runtime['package'])
    if json.loads(config.read_text(encoding='utf-8-sig')) == _config():
        _verify_cuda_packages(safe_path(root, CUDA_PACKAGES), spec['patches'])
        verify_packages(safe_path(root, TENSOR_PACKAGES), LOCK_PATH)

    mask, dino = _foundation_weights()
    verify_asset(safe_path(runtime['u2net'], 'u2net.onnx'), mask)
    models = runtime['models']
    for entry in dino:
        verify_asset(safe_path(runtime['dinov3'], Path(entry['path']).name), entry)
    for entry in spec['assets']:
        verify_asset(safe_path(models, Path(entry['path']).relative_to(WEIGHTS)), entry)
    _smoke(root, python, config)
    return config


def install(root: Path):
    """Install and exercise the native GGUF runtime before making it available."""
    root = Path(root).resolve(strict=True)
    python = _toolchain(root)
    config = safe_path(root, CONFIG)
    selected = _config()
    if config.exists() and json.loads(config.read_text(encoding='utf-8-sig')) != selected:
        # An existing administrator configuration owns its paths and dependencies.
        return verify(root)

    spec = _manifest()
    _install_sources(root, python, spec)
    install_packages(root, python, TENSOR_PACKAGES, LOCK_PATH)
    _install_weights(root, spec)

    config.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=config.parent, prefix='.native-hd-',
                            suffix='.json', delete=False) as stream:
        pending = Path(stream.name)
        json.dump(selected, stream, indent=2)
        stream.write('\n')
    try:
        _smoke(root, python, pending)
        if not config.exists():
            pending.rename(config)
    finally:
        pending.unlink(missing_ok=True)
    return config
