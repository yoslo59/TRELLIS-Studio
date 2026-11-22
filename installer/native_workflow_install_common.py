"""Shared provisioning helpers for Studio's standalone generation workflows."""

import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from installer.setup import digest, extract, fetch


def safe_path(root, path):
    root = Path(root).resolve()
    path = Path(path)
    if '..' in path.parts:
        raise ValueError(f'Unsafe install path: {path}')
    target = Path(os.path.abspath(root / path))
    relative = target.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f'Install path uses a symlink or junction: {current}')
    return target


def verify_asset(path, entry):
    if not path.is_file() or path.stat().st_size != entry['bytes']:
        raise ValueError(f'Missing or incomplete pinned file: {path}')
    if digest(path) != entry['sha256']:
        raise ValueError(f'Pinned file has an unexpected SHA-256 digest: {path}')
    return path


def fetch_asset(root, entry, destination):
    target = safe_path(root, destination)
    if target.exists():
        return verify_asset(target, entry)
    safe_path(root, target.with_name(target.name + '.part'))
    fetch(entry, target, root=Path(root).resolve())
    return target


def write_json(root, path, payload):
    path = safe_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = safe_path(root, path.with_name(path.name + '.tmp'))
    pending.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    pending.replace(path)


def _normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def verify_packages(target, lock):
    text = Path(lock).read_text(encoding='utf-8')
    expected = dict((_normalized(name), version) for name, version in
                    re.findall(r'^([\w.-]+)==([^\s;]+)', text, flags=re.MULTILINE))
    if not expected:
        raise ValueError(f'No pinned packages in {lock.name}.')
    actual = {_normalized(package.metadata['Name']): package.version
              for package in importlib.metadata.distributions(path=[str(target)]) if package.metadata['Name']}
    missing = [f'{name}=={version}' for name, version in expected.items() if actual.get(name) != version]
    if missing:
        raise ValueError(f'Missing or mismatched packages in {target}: {", ".join(missing)}')


def install_packages(root, python, target, lock: Path):
    root = Path(root).resolve()
    target = safe_path(root, target)
    if target.exists():
        verify_packages(target, lock)
        return target
    uv = safe_path(root, '.tools/uv/uv.exe')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.packages-', dir=target.parent) as temporary:
        stage = Path(temporary) / 'deps'
        command = [uv, 'pip', 'install', '--no-config', '--no-cache', '--link-mode', 'copy',
                   '--python', python, '--target', stage, '--only-binary', ':all:', '--no-deps']
        if '--hash=' in lock.read_text(encoding='utf-8'):
            command.append('--require-hashes')
        subprocess.run([*command, '-r', lock], cwd=root, check=True)
        verify_packages(stage, lock)
        stage.rename(target)
    return target
