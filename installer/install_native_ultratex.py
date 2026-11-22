"""Install the UltraTex operators and checkpoints used by Studio's native profiles."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

from installer.native_workflow_install_common import (digest, fetch_asset, install_packages, safe_path,
                                                      verify_asset, verify_packages, write_json)
from installer.native_workflow_python import RUNTIME_PATH


RUNTIME = Path('runtime/native-game-texture')
MODELS = Path('models/native-game-texture')
LOCK = Path(__file__).with_name('requirements-native-ultratex.txt')
MANIFEST = Path(__file__).with_name('native-ultratex-manifest.json')

TOKENIZER_NOTICE = (
    'Qwen2.5-VL-7B-Instruct tokenizer vocabulary and BPE merges, Apache-2.0.\n'
    'Source revision cc594898137f460bfe9f0759e9844b3ce807cfb5, https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct.\n'
    'This workflow variant adds the BPE version header and uses adapted token and template settings.\n'
    'It preserves Qwen3 think token IDs. The Qwen license text is included.\n'
)


def manifest():
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def _verify_license(root, data):
    terms = data['license']
    if digest(safe_path(root, terms['path'])) != terms['sha256']:
        raise ValueError('The bundled FLUX.2 license differs from the accepted version.')


def install_sources(root, data):
    def fetch_source(entry):
        return fetch_asset(root, entry, RUNTIME / entry['path'])

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch_source, data['source_files']))
    notice = safe_path(root, RUNTIME / 'tokenizer/NOTICE.txt')
    if not notice.exists():
        notice.write_text(TOKENIZER_NOTICE, encoding='utf-8')


def _source_receipt(data):
    return {'schema': 1, 'workflow': 'PixelArtistry Character 05 UltraTex', 'sources': data['source_revisions'],
            'files': {entry['path']: entry['sha256'] for entry in data['source_files']}}


def _model_receipt(data):
    return [{key: entry[key] for key in ('path', 'bytes', 'sha256')} for entry in data['models']]


def _probe(root, receipt):
    python = safe_path(root, RUNTIME_PATH)
    runtime = safe_path(root, RUNTIME)
    program = '''
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
runtime = Path(sys.argv[2])
sys.path[:0] = [str(root / 'native_ultratex'), str(runtime / 'deps'), str(runtime / 'modules'), str(root)]
import gguf, moderngl, scipy.sparse, torch, triton
from transformers import Qwen2Tokenizer, Qwen3Config, Qwen3Model
from utx_graphics import bake, conditions, fastbake, guidance, render
from run_character05_texture import assert_native_imports, load_texture_recipe, selected_sources
selected_sources(json.loads(sys.argv[3]))
load_texture_recipe()
assert torch.cuda.is_available(), 'UltraTex requires CUDA.'
assert_native_imports()
print('Standalone UltraTex operators, recipe and CUDA verified.')
'''
    subprocess.run([python, '-I', '-B', '-c', program, root, runtime, json.dumps(receipt)], cwd=root, check=True)


def verify(root):
    data = manifest()
    _verify_license(root, data)
    for directory, entries in ((RUNTIME, data['source_files']), (MODELS, data['models'])):
        for entry in entries:
            verify_asset(safe_path(root, directory / entry['path']), entry)
    verify_packages(safe_path(root, RUNTIME / 'deps'), LOCK)
    models = json.loads(safe_path(root, RUNTIME / 'model-manifest.json').read_text(encoding='utf-8'))
    if models != _model_receipt(data):
        raise ValueError('The installed UltraTex checkpoint manifest does not match the Setup version.')
    sources = json.loads(safe_path(root, RUNTIME / 'source-manifest.json').read_text(encoding='utf-8'))
    if sources != _source_receipt(data):
        raise ValueError('The installed UltraTex source manifest does not match the Setup version.')
    _probe(root, sources)


def install(root):
    data = manifest()
    _verify_license(root, data)
    install_packages(root, safe_path(root, RUNTIME_PATH), RUNTIME / 'deps', LOCK)
    install_sources(root, data)
    receipt = _source_receipt(data)
    _probe(root, receipt)
    for entry in data['models']:
        fetch_asset(root, entry, MODELS / entry['path'])
    write_json(root, RUNTIME / 'model-manifest.json', _model_receipt(data))
    write_json(root, RUNTIME / 'source-manifest.json', receipt)
