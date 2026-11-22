"""Prepare the standalone Character 05 shape architecture from the pinned reference."""

from argparse import ArgumentParser
import ast
from hashlib import sha256
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory


SOURCES = {
    'model.py': ('comfy/ldm/trellis2/model.py', 'baa8b081de2eada84754b867282bd21d498233816326f8842943857fe1d07e0c'),
    'vae.py': ('comfy/ldm/trellis2/vae.py', '77d55274133aeb103a97979bb89e16b90f87ecf4018b8a8c0e89c87f45555124'),
    'flexgemm.py': ('comfy/ldm/trellis2/flexgemm.py', '457e6a06149ca440fb56cd29f1194a6d4ba1ede92991d7e438836a631525811b'),
    'timestep.py': ('comfy/ldm/genmo/joint_model/layers.py', 'bb0ae6cc4dee093547688f667e307bc37d83697d7342dc944f20499682321c95'),
}
IMPORTS = {
    'import comfy.model_management\n': '',
    'import comfy.ops\n': 'import native_pixal_layers as tensor_ops\n',
    'from comfy.ldm.trellis2.vae import ': 'from .vae import ',
    'from comfy.ldm.trellis2.flexgemm import ': 'from .flexgemm import ',
    'from comfy.ldm.modules.attention import ': 'from native_pixal_layers import ',
    'from comfy.ldm.genmo.joint_model.layers import ': 'from .timestep import ',
    'from comfy.ldm.flux.math import ': 'from comfy_kitchen import ',
    'comfy.model_management.get_free_memory(device)': 'free_memory(device)',
    'comfy.model_management.cast_to(phases, device=q.device)': 'phases.to(q.device)',
    'comfy.ops.': 'tensor_ops.',
    'ComfyAttention': 'AttentionMetadata',
    'self.comfy_attention': 'self.attention_metadata',
}


def prepare(source, destination):
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError('Choose a new native shape runtime directory.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix='native-shape-') as directory:
        package = Path(directory) / 'native_shape_architecture'
        package.mkdir()
        for output, (relative, expected) in SOURCES.items():
            path = source / relative
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != expected:
                raise ValueError(f'Unexpected source revision for {relative}.')
            text = raw.decode('utf-8')
            if output == 'timestep.py':
                tree = ast.parse(text)
                member = next(node for node in tree.body if getattr(node, 'name', None) == 'TimestepEmbedder')
                text = 'import math\nfrom typing import Optional\nimport torch\nfrom torch import nn\n\n' + ast.get_source_segment(text, member)
            if output == 'flexgemm.py':
                text = 'from native_pixal_layers import free_memory\n' + text
            for old, new in IMPORTS.items():
                text = text.replace(old, new)
            syntax = ast.parse(text)
            imports = [name.name for node in ast.walk(syntax) if isinstance(node, ast.Import) for name in node.names]
            imports.extend(node.module for node in ast.walk(syntax) if isinstance(node, ast.ImportFrom) and not node.level)
            if any(name.partition('.')[0] in {'comfy', 'comfy_api', 'comfy_extras', 'folder_paths'} for name in imports):
                raise ValueError(f'Unexpected graph runtime import in {relative}.')
            notice = '# Adapted from ComfyUI 0.39.2 (GPL-3.0). See LICENSE.\n'
            (package / output).write_text(notice + text, encoding='utf-8')
        (package / '__init__.py').write_text('# Standalone shape architecture.\n', encoding='utf-8')
        shutil.copyfile(source / 'LICENSE', package / 'LICENSE')
        package.rename(destination)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    arguments = parser.parse_args()
    prepare(arguments.source, arguments.destination)
