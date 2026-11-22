"""Provision and verify the native Windows generation profiles managed by Setup."""

import argparse
import os
from pathlib import Path
import subprocess
import sys


SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from gpu_runtime import select_gpu
from installer.native_workflow_install_common import write_json
from installer.native_workflow_python import install_runtime, verify_runtime


GAME_RECEIPT = 'config/native-game-asset-verified.json'
BACKENDS = ('pixal3d', 'trellis2')


def _verify_profile_availability():
    from native_game_profile import runtime_status as game_status
    from native_high_detail import runtime_status as high_detail_status, ultratex_status

    availability = [
        ('Game Asset / PiXal3D', game_status('pixal3d')),
        ('Game Asset / TRELLIS.2', game_status('trellis2')),
        ('High Detail / PiXal3D', high_detail_status(geometry_backend='pixal3d')),
        ('High Detail / TRELLIS.2 GGUF', high_detail_status()),
        ('High Detail / UltraTex', ultratex_status()),
    ]
    for name, status in availability:
        if not status['available']:
            raise RuntimeError(f'{name}: {status["reason"]}')


def run(root, *, verify_only=False, accept_ultratex=False, accept_meshlib=False):
    if os.name != 'nt':
        raise RuntimeError('Native workflow Setup requires Windows x64.')
    if not verify_only:
        if not accept_ultratex:
            raise RuntimeError('Accept the FLUX.2 license before installing the UltraTex checkpoints.')
        if not accept_meshlib:
            raise RuntimeError('Accept the MeshLib terms before installing the High Detail runtime.')
    select_gpu(root)
    os.environ['UV_LINK_MODE'] = 'copy'
    os.environ['UV_NO_CACHE'] = '1'
    from installer import install_native_game_geometry, install_native_high_detail, install_native_ultratex

    if verify_only:
        verify_runtime(root)
        install_native_game_geometry.verify(root)
        install_native_ultratex.verify(root)
        install_native_high_detail.verify(root)
    else:
        install_runtime(root)
        verified = install_native_game_geometry.install(root)
        if not all(verified.get(backend) is True for backend in BACKENDS):
            raise RuntimeError('Both Game Asset geometry engines must pass verification.')
        install_native_ultratex.install(root)
        write_json(root, GAME_RECEIPT, verified)
        install_native_high_detail.install(root)
    _verify_profile_availability()
    print('Native workflow Setup verification succeeded.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=SOURCE)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--accept-ultratex-license', action='store_true')
    parser.add_argument('--accept-meshlib-license', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if root != SOURCE:
        parser.error('Run the copy of this script in the installation being configured.')
    try:
        run(root, verify_only=args.verify_only, accept_ultratex=args.accept_ultratex_license,
            accept_meshlib=args.accept_meshlib_license)
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'NATIVE WORKFLOWS SETUP STOPPED: {error}\n')


if __name__ == '__main__':
    main()
