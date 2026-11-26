"""Compose the native Character 05 generation stages for Studio."""

from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import json
import os
import subprocess

from native_game_geometry import finish_geometry, runtime_status as finishing_status
from native_pixal3d import generate_pixal3d_geometry, runtime_status as geometry_status
from studio_model_assets import model_asset_path


ROOT = Path(__file__).resolve().parent
TEXTURE = ROOT / 'runtime/native-game-texture'
TEXTURE_MODELS = ROOT / 'models/native-game-texture'
VERIFIED = ROOT / 'config/native-game-asset-verified.json'
TEXTURE_RUNNER = ROOT / 'native_ultratex/run_character05_texture.py'
TEXTURE_PYTHON = ROOT / '.tools/wtivo/runtime/Scripts/python.exe'


def texture_runtime_status():
    if not all(path.is_file() for path in (TEXTURE_RUNNER, TEXTURE_PYTHON, TEXTURE / 'source-manifest.json')):
        return {'available': False, 'reason': 'The standalone UltraTex runtime is still being prepared.'}
    try:
        models = json.loads((TEXTURE / 'model-manifest.json').read_text(encoding='utf-8'))
        if len(models) != 6 or not all(
            model_asset_path(ROOT, 'native-ultratex', entry['path']).stat().st_size == entry['bytes'] for entry in models
        ):
            raise ValueError
    except (OSError, KeyError, ValueError, TypeError):
        return {'available': False, 'reason': 'Install the verified local UltraTex model files.'}
    return {'available': True, 'reason': ''}


def runtime_status(backend='pixal3d'):
    texture = texture_runtime_status()
    if not texture['available']:
        return texture
    for check in (geometry_status(backend), finishing_status()):
        if not check['available']:
            return check
    try:
        results = json.loads(VERIFIED.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        results = {}
    if not isinstance(results, dict) or results.get(backend) is not True:
        return {'available': False, 'reason': 'This standalone Game Asset combination is still being compared.'}
    return {'available': True, 'reason': ''}


def texture_mesh(source, image, mask, output, work, progress=None, *, options=None):
    """Generate the configured UltraTex albedo and metal/roughness views and bake them."""
    work = Path(work).resolve()
    # UltraTex's atomic cache writes append a hash and a UUID to nested job paths.
    # Use the same private directory with Win32 long-path semantics, including on shares.
    if os.name == 'nt' and not str(work).startswith('\\\\?\\'):
        path = str(work)
        work = Path('\\\\?\\UNC\\' + path[2:] if path.startswith('\\\\') else '\\\\?\\' + path)
    command = [str(TEXTURE_PYTHON), '-I', '-B', '-u', str(TEXTURE_RUNNER), '--source-glb', str(source),
               '--reference', str(image), '--foreground-mask', str(mask), '--output', str(output),
               '--work', str(work), '--models', str(TEXTURE_MODELS)]
    if options is not None:
        work.mkdir(parents=True, exist_ok=True)
        recipe = work / 'studio-texture-options.json'
        recipe.write_text(json.dumps(options), encoding='utf-8')
        command.extend(('--options', str(recipe)))
    # Keep Triton's hashed compilation paths out of the deeply nested Server job directory.
    env = os.environ.copy()
    env.setdefault('TRITON_CACHE_DIR', str(TEXTURE / 'triton-cache'))
    if progress is not None:
        progress('Starting the UltraTex runtime')
    with subprocess.Popen(command, cwd=TEXTURE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        try:
            for line in process.stdout:
                if line.startswith('STAGE ') and progress is not None:
                    progress(line[6:].strip())
                else:
                    print(line, end='', flush=True)
            receipt = work / 'texture-result.json'
            if process.wait() or not output.is_file() or not receipt.is_file():
                raise RuntimeError('Standalone UltraTex failed. See the generation log.')
        finally:
            if process.poll() is None:
                process.terminate()
    return json.loads(receipt.read_text(encoding='utf-8'))


def run_profile(image_path, output_path, job_dir, options=None, progress=None):
    from native_workflow_options import geometry_backend, settings
    import numpy as np

    reference, output, work = (Path(value).resolve() for value in (image_path, output_path, job_dir))
    if not output.is_relative_to(work) or output.suffix.lower() != '.glb' or reference == output:
        raise ValueError('Write the Game Asset GLB inside the job directory.')
    options = settings('game_asset_native', options)
    backend = geometry_backend('game_asset_native', options)
    started = perf_counter()
    progress = progress or (lambda _label, _percent: None)
    with TemporaryDirectory(prefix='.native-game-', dir=work) as directory:
        scratch = Path(directory)
        mask = scratch / 'foreground.npy'
        vertices, faces = generate_pixal3d_geometry(
            reference, options, lambda label, percent: progress(label, int(percent * 0.68)),
            backend=backend, job_dir=scratch, texture_mask=mask,
        )
        geometry_triangles = len(faces)
        raw_mesh = scratch / 'decoded.npz'
        np.savez(raw_mesh, vertices=vertices, faces=faces)
        del vertices, faces
        quad_percent = 55
        wtivo_percent = 61
        milestones = iter((quad_percent, wtivo_percent, 67, 70, 74, 76, 78, 81))
        normal_mesh, finish = finish_geometry(raw_mesh, scratch / 'finishing',
                                              lambda label: progress(label, next(milestones, 81)), options=options,
                                              quad_progress=lambda label: progress(label, quad_percent),
                                              wtivo_progress=lambda label: progress(label, wtivo_percent))
        texture = texture_mesh(normal_mesh, reference, mask, output, scratch / 'texture',
                               lambda label: progress(label, 84), options=options)
    name = 'PiXal3D' if backend == 'pixal3d' else 'TRELLIS.2'
    return dict(engine=name, geometry_backend=backend, texture_method='ultratex',
                decoded_triangles=geometry_triangles, finishing=finish, texture=texture,
                elapsed_seconds=round(perf_counter() - started, 2))
