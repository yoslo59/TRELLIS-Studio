"""Install the optional Pixal3D worker without changing Studio's Python environment."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from installer.setup import digest, extract, fetch
from pixal3d_runtime import load_manifest, runtime_root, runtime_status, source_path
from studio_workspace import write

ROOT = Path(__file__).resolve().parent
RUNTIME = runtime_root(ROOT)
LOCKFILE = ROOT / 'installer' / 'requirements-pixal3d.txt'


def verified(path, entry):
    return path.is_file() and path.stat().st_size == entry['bytes'] and digest(path) == entry['sha256']


def obtain(entry, destination, local_source=None, allow_download=True):
    if local_source and not destination.exists() and verified(local_source, entry):
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(local_source, destination)
        except OSError:
            with tempfile.TemporaryDirectory(prefix='pixal3d-copy-', dir=destination.parent) as temp:
                copy = Path(temp) / destination.name
                shutil.copyfile(local_source, copy)
                if not verified(copy, entry):
                    raise RuntimeError(f'Asset changed while copying: {local_source}')
                copy.rename(destination)
        print(f'Reused verified file: {destination.name}', flush=True)
    elif allow_download or verified(destination, entry):
        fetch(entry, destination)
    else:
        raise RuntimeError(f'No verified local copy of {destination.name}; use the installer to download it.')


def install_source(manifest, local_assets, allow_download):
    entry = manifest['comfy']
    archive = RUNTIME / 'comfy-source.zip'
    obtain(entry, archive, local_assets / archive.name if local_assets else None, allow_download)
    source = source_path(RUNTIME, manifest)
    marker = source / '.studio-pixal3d-source.json'
    if marker.is_file() and json.loads(marker.read_text(encoding='utf-8')) == entry:
        return
    extract(archive, source, strip_root=True)
    write(marker, entry)


def install_dependencies(local_assets):
    deps = RUNTIME / 'deps'
    marker = deps / '.studio-pixal3d-deps.json'
    lock = {'sha256': digest(LOCKFILE)}
    if marker.is_file() and json.loads(marker.read_text(encoding='utf-8')) == lock:
        return
    if deps.exists():
        raise RuntimeError(f'Unrecognised dependency directory preserved: {deps}')
    uv = ROOT / '.tools' / 'uv' / 'uv.exe'
    if not uv.is_file():
        raise RuntimeError('Install TRELLIS Studio first to provision uv and its CUDA environment.')
    env = os.environ.copy()
    prior_cache = local_assets / 'uv-cache' if local_assets else None
    env['UV_CACHE_DIR'] = str(prior_cache if prior_cache and prior_cache.is_dir() else RUNTIME / 'uv-cache')
    with tempfile.TemporaryDirectory(prefix='pixal3d-deps-', dir=RUNTIME) as work:
        target = Path(work) / 'deps'
        subprocess.run([
            str(uv), 'pip', 'install', '--python', sys.executable, '--target', str(target),
            '--no-deps', '--only-binary', ':all:', '-r', str(LOCKFILE),
        ], env=env, check=True)
        write(target / marker.name, lock)
        target.rename(deps)


def verify_runtime():
    subprocess.run([sys.executable, '-u', str(ROOT / 'pixal3d_worker.py'), '--check-runtime'],
                   cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='Show pinned downloads and disk use.')
    parser.add_argument('--verify-only', action='store_true', help='Verify an existing installation.')
    parser.add_argument('--accept-dinov3-license', action='store_true')
    parser.add_argument('--reuse-assets', type=Path, help='Reuse SHA-256 verified local weights and source archive.')
    args = parser.parse_args()
    manifest = load_manifest(ROOT)
    entries = manifest['weights']
    if args.plan:
        print(json.dumps({'runtime': str(RUNTIME), 'comfy': manifest['comfy'], 'weights': entries,
                          'download_bytes': manifest['comfy']['bytes'] + sum(entry['bytes'] for entry in entries)},
                         indent=2))
        return
    if os.name != 'nt' or sys.maxsize <= 2**32:
        raise RuntimeError('This Pixal3D installer targets 64-bit Windows.')
    if args.verify_only:
        status = runtime_status(ROOT)
        if not status['ready']:
            raise RuntimeError(status['message'])
        for entry in entries:
            if not verified(RUNTIME / 'models' / entry['path'], entry):
                raise RuntimeError(f"Checksum mismatch: {entry['path']}")
        verify_runtime()
        return
    allow_download = args.accept_dinov3_license or args.reuse_assets is None
    if allow_download and not args.accept_dinov3_license:
        print('Pixal3D uses DINOv3. Read licenses/DINOv3-LICENSE.md and its use restrictions.')
        if input('After reading it, type ACCEPT to accept the DINOv3 license: ').strip() != 'ACCEPT':
            raise RuntimeError('Installation skipped.')
    if not (ROOT / 'engine' / 'o-voxel' / 'o_voxel' / 'uvraster.py').is_file():
        raise RuntimeError('Install TRELLIS Studio first. Its patched O-Voxel runtime is required.')
    local_assets = args.reuse_assets.resolve() if args.reuse_assets else None
    RUNTIME.mkdir(parents=True, exist_ok=True)
    install_source(manifest, local_assets, allow_download)
    for entry in entries:
        obtain(entry, RUNTIME / 'models' / entry['path'],
               local_assets / 'models' / entry['path'] if local_assets else None, allow_download)
    install_dependencies(local_assets)
    verify_runtime()
    write(RUNTIME / 'verified.json', {
        'schema': 1,
        'source_sha256': manifest['comfy']['sha256'],
        'model_revision': manifest['weights_revision'],
    })
    print('Pixal3D is ready. Reload TRELLIS Studio to see the optional character mode.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (EOFError, RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print('PIXAL3D SETUP STOPPED:', error, file=sys.stderr)
        sys.exit(1)
