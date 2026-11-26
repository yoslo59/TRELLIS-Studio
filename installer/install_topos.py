"""Provision the optional Windows TOPOS geometry runtime in an existing Studio installation."""
import argparse
from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from installer.native_workflow_install_common import fetch_asset, safe_path, verify_asset, verify_packages
from installer.kimodo_install_common import lock
from installer.setup import extract
from gpu_runtime import select_gpu
from pixal3d_runtime import load_manifest, runtime_root

TOOL = ROOT / '.tools/topos'
ENV = TOOL / 'venv'
CHILD = ENV / 'Scripts/python.exe'
LOCK = ROOT / 'installer/requirements-topos.txt'
SOURCES = (
    ('source', 'ymxbj/TOPOS', '581ad9e218cc7559072960ea44464014c808cef3', 4359603,
     '9e107f0ca5d823ef17c708fc279b416fba7a4b95150424ef7a4a6634c3c66ea4',
     '90716930aa68754ab7c968bb8ac648172c2ec424f9a6afddb4badf3ce06de61a'),
    ('dinov2', 'facebookresearch/dinov2', '7764ea0f912e53c92e82eb78a2a1631e92725fc8', 3001681,
     '04276715cddb29d45d05bff3a6fc132224dc27749b279ac98ad2ce4620e20d48',
     '0d97708926d39f88716ec91cd8d938459d0d7b59a2da3a027937a696c5ff21b1'),
)
DRIVE = 'https://drive.usercontent.google.com/download?export=download&confirm=t&id='
WEIGHTS = (
    ('TOPOS-VAE.model.pth', DRIVE + '1yEQIHQAXomgNVJ8chkuNc34RgF5rotoJ', 40033015,
     '6459f9704e1f93d50aebbc22221bfbbefa80d51b46531afc4315455118c77248'),
    ('TOPOS-DiT.model.pth', DRIVE + '1VwiVdPYXJM4gnPbwo8SGCI4og2G1zeul', 2228761001,
     '10143fd11c43651ecd3c8109256f47b9703b5eaf9b5ae50ee28545132424730d'),
    ('dinov2_vitl14_reg4_pretrain.pth',
     'https://dl.fbaipublicfiles.com/dinov2/dinov2_vitl14/dinov2_vitl14_reg4_pretrain.pth', 1217607321,
     '36e4deffbaef061a2576705b0c36f93621e2ae20bf6274694821b0b492551b51'),
)


def verify_source(folder, expected):
    """Accept the same source tree from a pinned archive or a Windows Git checkout."""
    digest = hashlib.sha256()
    for path in sorted(folder.rglob('*'), key=lambda p: p.relative_to(folder).as_posix()):
        if '.git' in path.parts or not path.is_file():
            continue
        if path.suffix not in ('.py', '.json', '.npy', '.npz', '.obj', '.yaml') and path.name != 'LICENSE':
            continue
        content = path.read_bytes()
        if path.suffix not in ('.npz', '.npy'):
            content = content.replace(b'\r\n', b'\n')
        digest.update(path.relative_to(folder).as_posix().encode() + b'\0' + hashlib.sha256(content).digest())
    if digest.hexdigest() != expected:
        raise ValueError(f'Missing or modified TOPOS component: {folder}')


def sources(verify_only):
    for name, repo, revision, size, archive_sha, tree_sha in SOURCES:
        folder = safe_path(ROOT, TOOL / name)
        if not folder.exists() and not verify_only:
            entry = {'url': f'https://codeload.github.com/{repo}/zip/{revision}', 'bytes': size, 'sha256': archive_sha}
            with TemporaryDirectory(prefix='source-', dir=TOOL) as work:
                archive = fetch_asset(ROOT, entry, Path(work) / 'source.zip')
                stage = Path(work) / 'unpacked'
                extract(archive, stage, strip_root=True)
                verify_source(stage, tree_sha)
                stage.rename(folder)
        else:
            verify_source(folder, tree_sha)


def weights(verify_only):
    detector = load_manifest(ROOT)['face_detector']
    detector_root = runtime_root(ROOT).resolve()
    entries = [(detector_root, detector_root / 'models' / detector['path'], detector)]
    for name, url, size, sha in WEIGHTS:
        entries.append((ROOT, ROOT / 'models/topos' / name, {'url': url, 'bytes': size, 'sha256': sha}))
    for base, target, entry in entries:
        target = safe_path(base, target)
        if verify_only:
            verify_asset(target, entry)
        else:
            fetch_asset(base, entry, target)


def python_environment(verify_only):
    studio = safe_path(ROOT, '.venv/Scripts/python.exe')
    site = studio.parents[1] / 'Lib/site-packages'
    target = safe_path(ROOT, ENV / 'Lib/site-packages')
    link = safe_path(ROOT, target / 'trellis_runtime.pth')
    if not verify_only:
        if not CHILD.is_file():
            if ENV.exists() and any(ENV.iterdir()) and not (ENV / 'pyvenv.cfg').is_file():
                raise ValueError(f'Existing unrecognized environment was preserved: {ENV}')
            subprocess.run([studio, '-m', 'venv', '--copies', '--without-pip', ENV], check=True)
        if not link.exists():
            link.write_text(site.as_posix() + '\n', encoding='utf-8')
    if not link.is_file() or Path(link.read_text(encoding='utf-8').strip()).resolve() != site:
        raise ValueError('The TOPOS environment must reference the current Studio CUDA packages.')
    try:
        verify_packages(target, LOCK)
    except ValueError:
        if verify_only:
            raise
        uv = safe_path(ROOT, '.tools/uv/uv.exe')
        subprocess.run([uv, 'pip', 'install', '--python', CHILD, '--no-config', '--no-cache',
                        '--link-mode', 'copy', '--only-binary', ':all:', '--no-deps',
                        '--target', target, '-r', LOCK], check=True)
        verify_packages(target, LOCK)
    # The worker uses this interpreter, the Studio CUDA build and TOPOS's SDPA fallback.
    check = ("import sys,torch,cv2,numba,scipy,torchvision; from pathlib import Path; "
             "sys.path.insert(0,sys.argv[1]); from train_dit import TOPOSDiTSolver; "
             "assert Path(torch.__file__).resolve().is_relative_to(Path(sys.argv[2])); "
             "assert torch.cuda.is_available(), 'CUDA is unavailable'")
    subprocess.run([CHILD, '-I', '-B', '-c', check, TOOL / 'source', site], cwd=ROOT,
                   env={**os.environ, 'ATTN_BACKEND': 'sdpa'}, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true', help='Check the installed component offline.')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This provisioner targets Windows x64.')
    if Path(sys.executable).resolve() != safe_path(ROOT, '.venv/Scripts/python.exe'):
        parser.error(r"Run this command with Studio's .\.venv\Scripts\python.exe.")
    select_gpu(ROOT)
    with nullcontext() if args.verify_only else lock(safe_path(ROOT, TOOL / '.installation.lock')):
        sources(args.verify_only)
        python_environment(args.verify_only)
        weights(args.verify_only)
    print('TOPOS geometry is ready for Prepare > Refine face.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(f'TOPOS setup stopped: {error}')
