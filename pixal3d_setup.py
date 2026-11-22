"""Install the optional Pixal3D worker without changing Studio's Python environment."""
import argparse
from base64 import urlsafe_b64encode
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from installer.setup import digest, extract, fetch
from pixal3d_runtime import (
    SINGLE_IMAGE_MARKER, load_manifest, runtime_root, runtime_status, single_image_marker, source_path,
)
from studio_workspace import write

ROOT = Path(__file__).resolve().parent
RUNTIME = runtime_root(ROOT)
LOCKFILE = ROOT / 'installer' / 'requirements-pixal3d.txt'


def verified(path, entry):
    return path.is_file() and path.stat().st_size == entry['bytes'] and digest(path) == entry['sha256']


def obtain(entry, destination, local_source=None, allow_download=True):
    if destination.exists():
        if not verified(destination, entry):
            raise RuntimeError(f'Existing file differs from the pinned download; preserved: {destination}')
        print(f'Verified: {destination.name}', flush=True)
    elif local_source and verified(local_source, entry):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pixal3d-copy-', dir=destination.parent) as temp:
            copy = Path(temp) / destination.name
            shutil.copyfile(local_source, copy)
            if not verified(copy, entry):
                raise RuntimeError(f'Asset changed while copying: {local_source}')
            copy.rename(destination)
        print(f'Reused verified file: {destination.name}', flush=True)
    elif allow_download:
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


def matching_dependencies(directory, lockfile):
    """Verify a prior wheel tree whose installation marker uses an older digest."""
    def normalized(name):
        return re.sub(r'[-_.]+', '-', name).lower()

    try:
        root = directory.resolve()
        pins = (line.split('==', 1) for line in lockfile.read_text(encoding='utf-8').splitlines()
                if line.strip() and not line.strip().startswith('#'))
        expected = {normalized(name.strip()): version.strip() for name, version in pins}
        installed = list(distributions(path=[str(root)]))
        actual = {normalized(package.metadata['Name']): package.version for package in installed}
        if len(actual) != len(installed) or actual != expected:
            return False
        for package in installed:
            files = package.files
            if not files:
                return False
            for file in files:
                path = file.locate().resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    return False
                if file.hash is None:
                    if file.name != 'RECORD' or file.parent.suffix != '.dist-info':
                        return False
                    continue
                if file.hash.mode != 'sha256' or path.stat().st_size != file.size:
                    return False
                fingerprint = urlsafe_b64encode(bytes.fromhex(digest(path))).decode('ascii').rstrip('=')
                if fingerprint != file.hash.value:
                    return False
        return True
    except (OSError, TypeError, ValueError, KeyError):
        return False


def install_dependencies(local_assets):
    deps = RUNTIME / 'deps'
    marker = deps / '.studio-pixal3d-deps.json'
    lock = {'sha256': digest(LOCKFILE)}
    try:
        if marker.is_file() and json.loads(marker.read_text(encoding='utf-8')) == lock:
            return
    except (OSError, ValueError):
        pass
    if deps.exists():
        if matching_dependencies(deps, LOCKFILE):
            print('Reused existing Pixal3D dependencies with the pinned versions and verified wheel files.', flush=True)
            return
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


def verify_runtime(single_image=False):
    check = '--check-single-image-runtime' if single_image else '--check-runtime'
    subprocess.run([sys.executable, '-B', '-u', str(ROOT / 'pixal3d_worker.py'), check], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='Show pinned downloads and disk use.')
    parser.add_argument('--verify-only', action='store_true', help='Verify an existing installation.')
    component = parser.add_mutually_exclusive_group()
    component.add_argument('--face-only', action='store_true', help='Install only the small face detector.')
    component.add_argument('--single-image', action='store_true',
                           help='Include the optional single-image Pixal3D and MoGe-2 weights.')
    parser.add_argument('--accept-dinov3-license', action='store_true')
    parser.add_argument('--reuse-assets', type=Path, help='Reuse SHA-256 verified local weights and source archive.')
    args = parser.parse_args()
    manifest = load_manifest(ROOT)
    entries = manifest['weights'] + (manifest['single_image_weights'] if args.single_image else [])
    face = manifest['face_detector']
    face_path = RUNTIME / 'models' / face['path']
    if args.plan:
        print(json.dumps({'runtime': str(RUNTIME), 'comfy': manifest['comfy'], 'weights': entries,
                          'face_detector': face, 'download_bytes': manifest['comfy']['bytes'] +
                          face['bytes'] + sum(entry['bytes'] for entry in entries)},
                         indent=2))
        return
    if os.name != 'nt' or sys.maxsize <= 2**32:
        raise RuntimeError('This Pixal3D installer targets 64-bit Windows.')
    if args.face_only:
        obtain(face, face_path)
        print('Face reprojection detector verified. Refresh TRELLIS Studio.', flush=True)
        return
    if args.verify_only:
        status = runtime_status(ROOT)
        capability = status['single_image'] if args.single_image else status
        if not capability['ready']:
            raise RuntimeError(capability['message'])
        for entry in entries:
            if not verified(RUNTIME / 'models' / entry['path'], entry):
                raise RuntimeError(f"Checksum mismatch: {entry['path']}")
        if face_path.exists() and not verified(face_path, face):
            raise RuntimeError(f"Checksum mismatch: {face['path']}")
        verify_runtime(args.single_image)
        return
    allow_download = args.accept_dinov3_license or args.reuse_assets is None
    if allow_download and not args.accept_dinov3_license:
        print('Pixal3D uses DINOv3. Read licenses/DINOv3-LICENSE.md and its use restrictions.')
        if input('After reading it, type ACCEPT to accept the DINOv3 license: ').strip() != 'ACCEPT':
            raise RuntimeError('Installation skipped.')
    if not (ROOT / 'engine' / 'o-voxel' / 'o_voxel' / 'uvraster.py').is_file():
        raise RuntimeError('Install TRELLIS Studio first. Its patched O-Voxel runtime is required.')
    local_assets = args.reuse_assets.resolve() if args.reuse_assets else None
    if args.single_image:
        size = sum(entry['bytes'] for entry in manifest['single_image_weights']) / 1024**3
        print(f'Single-image Pixal3D adds {size:.2f} GiB of pinned model weights.', flush=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    install_source(manifest, local_assets, allow_download)
    for entry in entries:
        obtain(entry, RUNTIME / 'models' / entry['path'],
               local_assets / 'models' / entry['path'] if local_assets else None, allow_download)
    local_face = local_assets / 'models' / face['path'] if local_assets else None
    if allow_download or face_path.is_file() or (local_face and local_face.is_file()):
        obtain(face, face_path, local_face, allow_download)
    install_dependencies(local_assets)
    verify_runtime(args.single_image)
    write(RUNTIME / 'verified.json', {
        'schema': 1,
        'source_sha256': manifest['comfy']['sha256'],
        'model_revision': manifest['weights_revision'],
    })
    if args.single_image:
        write(RUNTIME / SINGLE_IMAGE_MARKER, single_image_marker(manifest))
    print('Pixal3D is ready. Reload TRELLIS Studio to see the available generation methods.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (EOFError, RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print('PIXAL3D SETUP STOPPED:', error, file=sys.stderr)
        sys.exit(1)
