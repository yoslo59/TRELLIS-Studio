"""Provision the isolated Windows Python used by WTiVo, native shape and UltraTex."""

from pathlib import Path
import subprocess

from installer.native_workflow_install_common import safe_path, verify_packages


PYTHON_VERSION = '3.12.15'
RUNTIME_PATH = '.tools/wtivo/runtime/Scripts/python.exe'
MANAGED_PATH = '.tools/wtivo/python'


def verify_runtime(root):
    python = safe_path(root, RUNTIME_PATH)
    subprocess.run([python, '-I', '-B', '-c',
                    'import pydoc, sys, torch, scipy.sparse, trimesh; from pathlib import Path; '
                    'assert sys.version_info[:2] == (3, 12); '
                    'assert Path(sys.base_prefix).resolve().is_relative_to(Path(sys.argv[1]).resolve()); '
                    'assert Path(pydoc.__file__).resolve().is_relative_to(Path(sys.base_prefix).resolve()); '
                    'assert torch.__version__ == "2.8.0+cu128"; '
                    'assert torch.cuda.is_available(), "CUDA is unavailable for the native workflows"; '
                    'assert torch.ones(1, device="cuda").item() == 1',
                    safe_path(root, MANAGED_PATH)], cwd=root, check=True)
    return python


def install_runtime(root):
    root = Path(root).resolve()
    python = safe_path(root, RUNTIME_PATH)
    uv = safe_path(root, '.tools/uv/uv.exe')
    managed = safe_path(root, MANAGED_PATH)
    venv = python.parent.parent
    if python.is_file():
        # Accept an existing installation with a healthy, self-contained base.
        try:
            return verify_runtime(root)
        except subprocess.CalledProcessError:
            pass
    portable = managed / f'cpython-{PYTHON_VERSION}-windows-x86_64-none/python.exe'
    if not portable.is_file():
        subprocess.run([uv, 'python', 'install', PYTHON_VERSION, '--install-dir', managed,
                        '--no-bin', '--no-registry', '--no-config'], cwd=root, check=True)
    site = safe_path(root, venv / 'Lib/site-packages')
    # The stock launcher reads pyvenv.cfg. It keeps the base under this install
    # directory even if a temporary Python used during development was removed.
    subprocess.run([portable, '-I', '-B', '-m', 'venv', '--without-pip', venv], cwd=root, check=True)
    lock = Path(__file__).with_name('requirements-native-workflow-python.txt')
    try:
        verify_packages(site, lock)
    except ValueError:
        subprocess.run([uv, 'pip', 'install', '--no-config', '--no-cache', '--link-mode', 'copy',
                        '--python', python, '--no-deps', '--only-binary', ':all:', '--require-hashes',
                        '-r', lock], cwd=root, check=True)
    verify_packages(site, lock)
    return verify_runtime(root)
