"""Check the standalone texture launch contract without GPU or model files."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_game_profile


class UltraTexCacheTests(unittest.TestCase):
    def test_texture_worker_uses_a_cache_outside_the_server_job(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'ultratex'
            runtime.mkdir()
            runner = runtime / 'runner.py'
            runner.write_text(
                'import json, os, sys\n'
                'from pathlib import Path\n'
                'args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n'
                'spill = Path(args["--work"]) / "spill"\n'
                'spill.mkdir(exist_ok=True)\n'
                'array = spill / ("a" * 64 + ".npy")\n'
                'temporary = array.with_name(array.name + "." + "b" * 32 + ".tmp")\n'
                'temporary.write_bytes(b"fixture")\n'
                'os.replace(temporary, array)\n'
                'assert array.read_bytes() == b"fixture"\n'
                'array.unlink()\n'
                'Path(args["--output"]).touch()\n'
                '(Path(args["--work"]) / "texture-result.json").write_text(json.dumps(\n'
                '    {"cache": os.environ.get("TRITON_CACHE_DIR"), "work": args["--work"]}))\n',
                encoding='utf-8',
            )
            job = root / 'server-data' / 'users' / ('0' * 32) / 'outputs' / ('1' * 32)
            work = job / '.workflow-result-abcdefgh' / '.high-detail-ultratex-ijklmnop' / 'texture'
            work.mkdir(parents=True)
            env = {key: value for key, value in os.environ.items() if key != 'TRITON_CACHE_DIR'}
            with (patch.dict(os.environ, env, clear=True),
                  patch.multiple(native_game_profile, TEXTURE=runtime, TEXTURE_PYTHON=Path(sys.executable),
                                 TEXTURE_RUNNER=runner)):
                arguments = (job / 'surface.glb', job / 'reference.png', job / 'mask.npy', job / 'model.glb', work)
                result = native_game_profile.texture_mesh(*arguments)
                self.assertEqual(result['cache'], str(runtime / 'triton-cache'))
                if os.name == 'nt':
                    self.assertTrue(result['work'].startswith('\\\\?\\'))
                    self.assertGreater(len(str(work)) + len('/spill/') + 105, 259)
                os.environ['TRITON_CACHE_DIR'] = str(root / 'custom-cache')
                self.assertEqual(native_game_profile.texture_mesh(*arguments)['cache'], str(root / 'custom-cache'))


if __name__ == '__main__':
    unittest.main()
