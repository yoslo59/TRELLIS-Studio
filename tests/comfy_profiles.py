"""Exercise native runner orchestration without importing GPU code or downloading models."""

import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import comfy_profiles


NATIVE_MAIN = """
import hashlib
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import comfy

comfy.model_management = SimpleNamespace(get_torch_device=lambda: SimpleNamespace(type='cuda'))

def validate_prompt(_prompt_id, prompt, _partial):
    async def checked():
        if '122' in prompt:
            assert prompt['122']['inputs']['image'] == 'reference.png'
            assert Path(prompt['324']['inputs']['value']).is_file()
            assert prompt['358']['inputs']['view_count'] == 12
            assert prompt['364']['inputs']['view_count'] == 6
            return (True, None, ['386'], {})
        assert prompt['13']['inputs']['image'] == 'reference.png'
        assert prompt['35']['inputs']['value'] == 'StudioHighDetail'
        assert prompt['17']['inputs']['shape_steps'] == 50
        return (True, None, ['26'], {})
    return checked()

execution = SimpleNamespace(validate_prompt=validate_prompt)

def start_comfyui():
    import asyncio
    server = SimpleNamespace(asset_manager=SimpleNamespace(shutdown=lambda: None))
    history = {}
    class PromptQueue:
        def put(self, item):
            stage = '327' if '122' in item[2] else '41'
            server.send_sync('executing', {'node': stage})
            assert Path('input/reference.png').read_bytes() == b'studio test reference'
            output_prefix = 'PixelArtistry_char_final' if '122' in item[2] else 'StudioHighDetail_Textured'
            data = b'{"asset":{"version":"2.0"}} '
            header = struct.pack('<4sIIII', b'glTF', 2, 20+len(data), len(data), 0x4E4F534A)
            Path('output', output_prefix + '_00001.glb').write_bytes(header + data)
            history[item[1]] = {'status': {'status_str': 'success'}}

        def get_history(self, *, prompt_id):
            return {prompt_id: history[prompt_id]}

    server.prompt_queue = PromptQueue()
    def listener():
        raise AssertionError('Native profile tried to open an HTTP listener')
    return asyncio.new_event_loop(), server, listener
"""


class ComfyProfileRunnerTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory(prefix='studio-comfy-contract-')
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        fake = self.root / 'native'
        (fake / 'comfy').mkdir(parents=True)
        (fake / 'comfy' / '__init__.py').touch()
        (fake / 'comfy' / 'options.py').write_text(
            'def enable_args_parsing(): pass', encoding='utf-8')
        (fake / 'main.py').write_text(NATIVE_MAIN, encoding='utf-8')
        (fake / 'comfyui_version.py').write_text("__version__ = '0.39.2'", encoding='utf-8')
        (fake / 'cuda_malloc.py').touch()
        (fake / 'models').mkdir()
        (fake / 'nodes').mkdir()
        blender = fake / 'blender.exe'
        blender.touch()
        test_weight = b'test U2NET'
        (fake / 'u2net.onnx').write_bytes(test_weight)
        pinned = patch.object(comfy_profiles, 'U2NET_WEIGHT', (len(test_weight), hashlib.sha256(test_weight).hexdigest()))
        pinned.start()
        self.addCleanup(pinned.stop)
        runtime = {
            'python': sys.executable, 'comfy': str(fake), 'models': str(fake / 'models'),
            'custom_nodes': str(fake / 'nodes'), 'blender': str(blender),
            'environment': {'U2NET_HOME': str(fake)},
        }
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({
            'schema': 1, 'profiles': {profile: runtime for profile in comfy_profiles.PROFILES},
        }), encoding='utf-8')
        self.input = self.root / 'stored-source.PNG'
        self.input.write_bytes(b'studio test reference')
        self.output = self.root / 'result.glb'

    def test_both_pinned_profiles_publish_and_remove_temporary_files(self):
        for profile in comfy_profiles.PROFILES:
            with self.subTest(profile=profile):
                stages = []
                try:
                    receipt = comfy_profiles.run_profile(
                        profile, self.input, self.output, self.root,
                        lambda label, percentage: stages.append((label, percentage)), config_path=self.config,
                    )
                except RuntimeError:
                    self.fail((self.root / 'comfy-profile.log').read_text(encoding='utf-8'))
                self.assertEqual(receipt['profile'], profile)
                self.assertEqual(receipt['comfy_version'], '0.39.2')
                self.assertGreaterEqual(receipt['elapsed_seconds'], 0)
                self.assertEqual(self.output.read_bytes()[:4], b'glTF')
                self.assertGreater(len(stages), 1)
                self.assertTrue((self.root / 'comfy-profile.log').is_file())
                self.assertFalse(list(self.root.glob('comfy-*' + '/')))

    def test_character_runtime_ignores_studio_engine_environment(self):
        inherited = {
            'PYTORCH_CUDA_ALLOC_CONF': 'garbage_collection_threshold:0.65', 'TORCHDYNAMO_DISABLE': '1',
            'ATTN_BACKEND': 'sdpa', 'SPARSE_ATTN_BACKEND': 'xformers',
        }
        with patch.dict(os.environ, inherited):
            runtime = {'environment': {}}
            character = comfy_profiles._environment('game_asset_comfy', runtime, self.root)
            high = comfy_profiles._environment('high_detail_comfy', runtime, self.root)
            configured = comfy_profiles._environment(
                'game_asset_comfy', {'environment': {'ATTN_BACKEND': 'flash_attn'}}, self.root)
        self.assertTrue(inherited.keys().isdisjoint(character))
        self.assertEqual({key: high.get(key) for key in inherited}, inherited)
        self.assertEqual(configured['ATTN_BACKEND'], 'flash_attn')

    def test_cancelled_request_preserves_existing_output(self):
        self.output.write_bytes(b'existing')
        with self.assertRaises(InterruptedError):
            comfy_profiles.run_profile('game_asset_comfy', self.input, self.output, self.root,
                                       config_path=self.config, cancelled=lambda: True)
        self.assertEqual(self.output.read_bytes(), b'existing')

    def test_corrupt_local_background_weight_cannot_trigger_a_download(self):
        weight = self.root / 'native' / 'u2net.onnx'
        weight.write_bytes(b'x' * weight.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'does not match the pinned model'):
            comfy_profiles.run_profile('high_detail_comfy', self.input, self.output, self.root,
                                       config_path=self.config)
        self.assertFalse((self.root / 'comfy-profile.log').exists())
        self.assertFalse(self.output.exists())

    def test_no_runtime_shows_a_path_free_status(self):
        status = comfy_profiles.runtime_status(
            'high_detail_comfy', config_path=self.root / 'private' / 'missing.json')
        self.assertFalse(status['available'])
        self.assertNotIn(str(self.root), status['reason'])


if __name__ == '__main__':
    unittest.main()
