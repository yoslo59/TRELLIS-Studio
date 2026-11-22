"""Prepare the pinned portable GGUF source used by the native High Detail worker."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil


PIPELINE_REVISION = 'b3b9e951874c5fb5c936566c93b908299be08b2c'
QUANT_REVISION = '6ea2651e7df66d7585f6ffee804b20e92fb38b8a'
MANIFEST = 'native-high-detail-source.json'
SOURCE_HASHES = {
    'model_manager.py': '40002857ed67dd82933356f359025d1c329e44412b4a95e826a09c5263035db0',
    'trellis2_gguf/pipelines/base.py': '49e70867a319ef649da1467ca50098c7139328bcc56a8dd902a33a4baecab87a',
    'trellis2_gguf/pipelines/trellis2_image_to_3d.py':
        '9be0e4ec4634cf394ea2b13eb5d373eb8372d236fd84c123072f2ffd0fd0df70',
    'trellis2_gguf/utils/gguf_utils.py': '4267a207d38da5742484e4c0d64fe2026c7e9a5a0dddd3fcaffc192a37469d83',
}
DEQUANT_HASH = '215369438d96c372c09eb8701eec3c3b0348084c96dd3ea83872601ebed42017'
_models = None


def configure_models(path):
    global _models
    _models = Path(path).resolve(strict=True)


def models_directory():
    if _models is None:
        raise RuntimeError('The native High Detail model directory was not configured.')
    return str(_models)


class Progress:
    def __init__(self, total, callback=None):
        self.total = total
        self.callback = callback
        self.current = 0

    def update(self, amount):
        self.current = min(self.current + amount, self.total)
        if self.callback:
            self.callback(self.current, self.total)


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _set_models_source(source):
    return source.replace(
        'import folder_paths', 'from native_high_detail_portable import models_directory',
    ).replace('folder_paths.models_dir', 'models_directory()')


def _set_portable_quant(source):
    setup = source.index('def _setup_native_gguf():')
    fallback = source.index('_native_dequantize_tensor = None\n\n#', setup)
    source = source[:setup] + source[fallback:]
    initialisation = source.index('# Initialise native support')
    remapping = source.index('def remap_gguf_state_dict', initialisation)
    loader = (
        'from native_high_detail_quant import (GGMLTensor, GGMLLayer, GGMLOps,\n'
        '                                     get_orig_shape, is_quantized)\n'
        'from native_high_detail_dequant import dequantize_tensor as _native_dequantize_tensor\n\n'
        'HAS_GGUF_OPS = True\n\n\n'
    )
    return (source[:initialisation] + loader + source[remapping:]).replace(
        'from gguf_native.ops import GGMLOps', 'from native_high_detail_quant import GGMLOps',
    ).replace('forward_comfy_cast_weights', 'forward_cast_weights')


def prepare_portable_package(pipeline_source, quant_source, installation):
    """Create a standalone source installation from the two pinned local archives."""
    pipeline_source = Path(pipeline_source).resolve(strict=True)
    quant_source = Path(quant_source).resolve(strict=True)
    installation = Path(installation).resolve()
    if installation.exists():
        raise FileExistsError(f'The portable High Detail package already exists at {installation}.')
    if any(_hash(pipeline_source / filename) != expected for filename, expected in SOURCE_HASHES.items()):
        raise ValueError('The GGUF pipeline source does not match the tested revision.')
    if _hash(quant_source / 'dequant.py') != DEQUANT_HASH:
        raise ValueError('The GGUF dequantisation source does not match the tested revision.')

    installation.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='high-detail-', dir=installation.parent) as staging:
        package = Path(staging) / 'package'
        sources = [entry for entry in (pipeline_source / 'trellis2_gguf').rglob('*.py')]
        sources.append(pipeline_source / 'model_manager.py')
        manifest = {}
        for entry in sources:
            relative = entry.relative_to(pipeline_source)
            target = package / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = entry.read_text(encoding='utf-8')
            if relative.as_posix() in SOURCE_HASHES:
                raw = _set_models_source(raw)
                raw = raw.replace('from comfy.utils import ProgressBar',
                                  'from native_high_detail_portable import Progress as ProgressBar')
            if relative.as_posix() == 'trellis2_gguf/utils/gguf_utils.py':
                raw = _set_portable_quant(raw)
            syntax = ast.parse(raw, filename=str(relative))
            for statement in ast.walk(syntax):
                if isinstance(statement, ast.Import):
                    names = [name.name.split('.', 1)[0] for name in statement.names]
                elif isinstance(statement, ast.ImportFrom) and not statement.level:
                    names = [statement.module.split('.', 1)[0]]
                else:
                    continue
                if any(name in {'comfy', 'comfy_api', 'comfy_extras', 'folder_paths', 'gguf_native'}
                       for name in names):
                    raise ValueError(f'Unexpected UI runtime dependency in {relative}.')
            target.write_text(raw, encoding='utf-8')
            manifest[relative.as_posix()] = _hash(target)

        shutil.copyfile(quant_source / 'dequant.py', package / 'native_high_detail_dequant.py')
        shutil.copyfile(pipeline_source / 'LICENSE', package / 'TRELLIS-LICENSE.txt')
        shutil.copyfile(quant_source / 'LICENSE', package / 'GGUF-LICENSE.txt')
        manifest['native_high_detail_dequant.py'] = DEQUANT_HASH
        (package / MANIFEST).write_text(json.dumps({
            'schema': 1,
            'trellis_gguf_revision': PIPELINE_REVISION,
            'quant_revision': QUANT_REVISION,
            'sources': manifest,
        }, indent=2), encoding='utf-8')
        package.replace(installation)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pipeline_source', type=Path)
    parser.add_argument('quant_source', type=Path)
    parser.add_argument('installation', type=Path)
    arguments = parser.parse_args()
    prepare_portable_package(arguments.pipeline_source, arguments.quant_source, arguments.installation)
