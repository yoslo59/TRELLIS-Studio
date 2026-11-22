"""Optional geometry-only bridge to an operator-provisioned WTiVo runtime."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


WTIVO_RESOLUTION = 1024
WTIVO_REVISION = 'fb9e9ea6a7deec965d3a4bf43ba7a4a9ff3856f3'
WTIVO_PROXY_POINTS = 12_000_000
WTIVO_OUTPUT_BYTES = 768 * 1024**2
RUNTIME_HINT = 'Configure the optional external WTiVo runtime in config/wtivo-runtime.json.'
PINNED_FILES = {
    'wtivo.py': 'bb3b2ffd450a643e981271bba88d66cb735ec1e766fbe27df350faa2c618d55d',
    'build/cellocut_gpupr_fast.pyd': 'ffa5a1e166048548ee9ec70a19a350376528c5d25f3541c2bce3e3c2db8a0251',
    'build/cellocut_vdb_watertight.pyd': '9d3414e637e40fee1b76a874229e04376908edcebb74ce5c14db831f3fc9c4ee',
    'build/cppmodules.cp312-win_amd64.pyd': '047db2173c8d141d320089689b2ac88feda80adb5dec44d8b15b1650a2ed5b47',
    'build/libgmp-10.dll': '2e95a24e93a069c9c7e1801f42cb47d93a7b58886f1c91366205383ef41d7a0c',
    'build/tbb12.dll': 'e8aff3cfbcaa6dea3e0727ea0673715873273c6b0920b9eaf48a6faae0ff1f8f',
    'build/tbbmalloc.dll': '75ff31b8548d5c6a70d33e6cd59099c743ad890a8806f957a9e19140c5cfb80d',
}
AUDIT = re.compile(r'^\[FINAL\] watertight=(True|False) \| bad_edge_groups=(\d+) '
                   r'\| native_edge_watertight=(True|False) \| trimesh_closed=(True|False)$', re.MULTILINE)


@dataclass(frozen=True)
class Runtime:
    python: Path
    source: Path


class WTiVoTimeout(ValueError):
    """The WTiVo subprocess exhausted its finishing time limit."""


def _print_runtime_log(output, *directories):
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    if not output:
        return
    for path in directories:
        output = output.replace(str(path), path.name).replace(path.as_posix(), path.name)
    print(output, end='' if output.endswith('\n') else '\n', flush=True)


def validate_wtivo_recipe(enabled, resolution, *, refinish=True, hd_reconstruction=False,
                          repair_surface=False, adaptive=False, budget_preset=None, inline_master=False):
    if type(enabled) is not bool:
        raise ValueError('WTiVo Reconstruction must be true or false.')
    if not enabled:
        return
    if resolution < WTIVO_RESOLUTION or not (refinish or inline_master):
        raise ValueError('WTiVo Reconstruction requires a saved decoded source at 1024 or higher.')
    if hd_reconstruction or repair_surface or adaptive or budget_preset:
        raise ValueError('WTiVo Reconstruction uses a fixed grid and a manual triangle budget. Choose one reconstruction method.')


def _absolute_path(text):
    if type(text) is not str or not Path(text).is_absolute():
        raise ValueError('WTiVo runtime paths must be absolute.')
    return Path(text).resolve(strict=True)


def _read_runtime(root):
    if sys.platform != 'win32':
        raise ValueError('The verified WTiVo runtime is available on Windows x64.')
    config = Path(root) / 'config' / 'wtivo-runtime.json'
    try:
        with config.open('rb') as stream:
            data = json.loads(stream.read(16 * 1024 + 1).decode('utf-8-sig'))
        if not isinstance(data, dict) or set(data) != {'python', 'source'}:
            raise ValueError('Expected python and source paths.')
        python = _absolute_path(data['python'])
        source = _absolute_path(data['source'])
    except (OSError, ValueError) as error:
        raise ValueError(RUNTIME_HINT) from error
    environment = python.parent.parent
    try:
        version = (environment / 'pyvenv.cfg').read_text(encoding='utf-8')
        torch_metadata = environment / 'Lib/site-packages/torch-2.8.0+cu128.dist-info/METADATA'
        if (not python.is_file() or python.name.lower() != 'python.exe'
                or not re.search(r'^\s*(?:version_info|version)\s*=\s*3\.12(?:\.|\s|$)', version, re.MULTILINE)
                or not torch_metadata.is_file()):
            raise ValueError('Unqualified WTiVo environment.')
    except (OSError, ValueError) as error:
        raise ValueError('WTiVo requires its separate Python 3.12 environment with PyTorch 2.8.0+cu128.') from error
    try:
        expected = {Path(name).name for name in PINNED_FILES if name.startswith('build/')}
        found = {path.name for path in (source / 'build').iterdir() if path.suffix.lower() in ('.pyd', '.dll')}
        if expected != found:
            raise ValueError('Unexpected WTiVo native build.')
        for name, expected_hash in PINNED_FILES.items():
            path = source / name
            if path.resolve(strict=True) != path or not path.is_file():
                raise ValueError('Missing WTiVo component.')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_hash:
                    raise ValueError('WTiVo component fingerprint mismatch.')
    except (OSError, ValueError) as error:
        raise ValueError('The configured WTiVo files do not match the verified Windows x64 build.') from error
    return Runtime(python, source)


def wtivo_capability(root, resolution):
    if resolution < WTIVO_RESOLUTION:
        return False, 'WTiVo Reconstruction requires a saved decoded source at 1024 or higher.'
    try:
        _read_runtime(root)
    except ValueError as error:
        return False, str(error)
    return True, ''


def _check_mesh(vertices, faces):
    import numpy as np

    if (vertices.ndim != 2 or vertices.shape[1] != 3 or vertices.dtype.kind != 'f'
            or faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in 'iu'
            or not len(vertices) or not len(faces) or not np.isfinite(vertices).all()
            or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError('WTiVo requires finite 3D vertices and valid indexed triangle faces.')


def reconstruct(root, job, vertices, faces, *, grid=WTIVO_RESOLUTION, component_mode='keep_all',
                options=None):
    """Run pinned WTiVo on copies of geometry in the original decoded coordinates."""
    import numpy as np

    if options is None:
        if (grid, component_mode) not in ((WTIVO_RESOLUTION, 'keep_all'), (1536, 'largest')):
            raise ValueError('Unsupported WTiVo recipe.')
        options = dict(input_res=grid, final_res=grid, proxy_points=WTIVO_PROXY_POINTS, proxy_eps_scale=1.0,
                       proxy_feature_weight=1.5, lambda_fill=20.0, threads=16, thin_iso_vox=0.0,
                       faithc_component_mode=component_mode)
    grid, final_grid = options['input_res'], options['final_res']
    component_mode = options['faithc_component_mode']
    if (grid < 2 or final_grid < 2 or options['proxy_points'] < 4 or options['threads'] < 1
            or not 1.0e-4 < options['proxy_eps_scale'] / grid < 0.5):
        raise ValueError('WTiVo grid, proxy points, proxy epsilon, or thread count is outside the runtime range.')
    runtime = _read_runtime(root)
    _check_mesh(vertices, faces)
    input_bytes = (vertices.size * 8) + (faces.size * 4)
    if shutil.disk_usage(job).free < input_bytes + WTIVO_OUTPUT_BYTES:
        raise ValueError('Free disk space for the WTiVo working mesh or choose Studio 768.')
    started = perf_counter()
    with TemporaryDirectory(prefix='.wtivo-', dir=job) as work:
        work = Path(work)
        v_in, f_in = work / 'vertices.npy', work / 'faces.npy'
        v_out, f_out = work / 'rebuilt-vertices.npy', work / 'rebuilt-faces.npy'
        np.save(v_in, np.ascontiguousarray(vertices, dtype=np.float64), allow_pickle=False)
        np.save(f_in, np.ascontiguousarray(faces, dtype=np.int32), allow_pickle=False)
        command = [str(runtime.python), '-I', '-B', '-u', '-X', 'utf8', str(runtime.source / 'wtivo.py'),
                   '--input-vertices-npy', str(v_in), '--input-faces-npy', str(f_in),
                   '--output-vertices-npy', str(v_out), '--output-faces-npy', str(f_out),
                   '--input-res', str(grid), '--final-res', str(final_grid),
                   '--proxy_points', str(options['proxy_points']), '--proxy_eps_scale', str(float(options['proxy_eps_scale'])),
                   '--proxy_feature_weight', str(float(options['proxy_feature_weight'])),
                   '--lambda_fill', str(float(options['lambda_fill'])), '--threads', str(options['threads']),
                   '--thick_band_voxels', '3.0', '--thin_band_voxels', '3.0',
                   '--thin_iso_vox', str(float(options['thin_iso_vox'])),
                   '--faithc_component_mode', component_mode, '--faithc_tri_mode', 'auto',
                   '--faithc_clamp_anchors', '1', '--faithc_lambda_n', '1.0', '--faithc_lambda_d', '0.1']
        # Use only the host's native runtime and this job's temporary directory.
        env = {**os.environ, 'WTIVO_SUBPROCESS': '1', 'TEMP': str(work), 'TMP': str(work),
               'CUDA_CACHE_PATH': str(work / 'cuda')}
        try:
            process = subprocess.run(command, cwd=work, env=env, timeout=1800, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as error:
            _print_runtime_log(error.stdout, runtime.source, runtime.python.parent.parent, work)
            raise WTiVoTimeout(f'WTiVo exceeded 30 minutes at {grid} → {final_grid}. '
                               'Try smaller WTiVo grids or fewer proxy points.') from None
        except OSError as error:
            raise ValueError('Unable to start the configured WTiVo runtime.') from error
        _print_runtime_log(process.stdout, runtime.source, runtime.python.parent.parent, work)
        audit = AUDIT.search(process.stdout)
        if process.returncode != 0 or not audit:
            raise ValueError('WTiVo did not return a verified mesh. See the generation log or choose Studio 768.')
        try:
            if sum(path.stat().st_size for path in (v_out, f_out)) > WTIVO_OUTPUT_BYTES:
                raise ValueError('WTiVo returned an oversized reconstructed mesh.')
            rebuilt_vertices = np.load(v_out, allow_pickle=False)
            rebuilt_faces = np.load(f_out, allow_pickle=False)
            _check_mesh(rebuilt_vertices, rebuilt_faces)
        except (OSError, ValueError, EOFError) as error:
            raise ValueError('WTiVo returned an invalid model. The original model is intact.') from error
    margin = (4 + abs(options['thin_iso_vox'])) / min(grid, final_grid)
    if (np.any(rebuilt_vertices.min(axis=0) < vertices.min(axis=0) - margin)
            or np.any(rebuilt_vertices.max(axis=0) > vertices.max(axis=0) + margin)):
        raise ValueError('WTiVo reconstruction exceeded the decoded source bounds. The original model is intact.')
    report = dict(engine='WTiVo', revision=WTIVO_REVISION, grid=grid, component_mode=component_mode,
                  proxy_points=options['proxy_points'], reconstructed_triangles=len(rebuilt_faces),
                  watertight_before_reduction=audit[1] == 'True', bad_edge_groups_before_reduction=int(audit[2]),
                  native_edge_watertight_before_reduction=audit[3] == 'True', seconds=round(perf_counter() - started, 3))
    if final_grid != grid:
        report['final_grid'] = final_grid
    return np.ascontiguousarray(rebuilt_vertices, dtype=np.float32), np.ascontiguousarray(rebuilt_faces, dtype=np.int32), report
