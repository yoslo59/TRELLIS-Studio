"""WTiVo admission, geometry-only exchange, and isolation without CUDA."""
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wtivo_runtime as wtivo


class WTiVoTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='trellis-wtivo-contract-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'external' / 'source'
        (self.source / 'build').mkdir(parents=True)
        self.native = self.source / 'build' / 'example.pyd'
        self.script = self.source / 'wtivo.py'
        pins = {}
        for path in (self.script, self.native):
            path.write_bytes(b'Pinned external test fixture')
            pins[path.relative_to(self.source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        for name, value in [('PINNED_FILES', pins), ('WTIVO_OUTPUT_BYTES', 1024 ** 2)]:
            patcher = patch.object(wtivo, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        platform = patch.object(wtivo.sys, 'platform', 'win32')
        platform.start()
        self.addCleanup(platform.stop)
        self.env = self.root / 'external' / 'venv'
        self.python = self.env / 'Scripts' / 'python.exe'
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        (self.env / 'pyvenv.cfg').write_text('version_info = 3.12\n', encoding='utf-8')
        metadata = self.env / 'Lib/site-packages/torch-2.8.0+cu128.dist-info/METADATA'
        metadata.parent.mkdir(parents=True)
        metadata.touch()
        self.config = self.root / 'config' / 'wtivo-runtime.json'
        self.config.parent.mkdir()
        self.job = self.root / 'job'
        self.job.mkdir()
        self.vertices = np.array([[0., 0., 0.], [.1, 0., 0.], [0., .1, 0.]], dtype=np.float32)
        self.faces = np.array([[0, 1, 2]], dtype=np.int32)

    def configure(self):
        self.config.write_text(json.dumps(dict(python=str(self.python), source=str(self.source))), encoding='utf-8')

    def emulate(self, command, *, cwd, env, grid=1024, final_grid=None, component_mode='keep_all', **kwargs):
        self.assertEqual(command[:7], [str(self.python), '-I', '-B', '-u', '-X', 'utf8', str(self.script)])
        self.assertEqual(cwd.resolve().parent, self.job.resolve())
        self.assertEqual(Path(env['TMP']), cwd)
        self.assertEqual(kwargs['timeout'], 1800)

        def path(option):
            result = Path(command[command.index(option) + 1])
            self.assertEqual(result.parent, cwd)
            return result

        v = np.load(path('--input-vertices-npy'), allow_pickle=False)
        f = np.load(path('--input-faces-npy'), allow_pickle=False)
        self.assertTrue(np.array_equal(v, self.vertices))
        self.assertTrue(np.array_equal(f, self.faces))
        for option, expected in (('--input-res', str(grid)), ('--final-res', str(final_grid or grid)),
                                 ('--faithc_component_mode', component_mode)):
            self.assertEqual(command[command.index(option) + 1], expected)
        np.save(path('--output-vertices-npy'), v, allow_pickle=False)
        np.save(path('--output-faces-npy'), f, allow_pickle=False)
        return subprocess.CompletedProcess(command, 0,
            '[FINAL] watertight=False | bad_edge_groups=0 | native_edge_watertight=True | trimesh_closed=False\n')

    def test_unavailable_and_changed_binary(self):
        self.assertFalse(wtivo.wtivo_capability(self.root, 1024)[0])
        self.configure()
        self.assertFalse(wtivo.wtivo_capability(self.root, 512)[0])
        self.assertEqual(wtivo.wtivo_capability(self.root, 1024), (True, ''))
        self.native.write_bytes(b'Modified external binary')
        available, reason = wtivo.wtivo_capability(self.root, 1024)
        self.assertFalse(available)
        self.assertIn('verified', reason)
        with patch.object(wtivo.subprocess, 'run') as process, self.assertRaises(ValueError):
            wtivo.reconstruct(self.root, self.job, self.vertices, self.faces)
        process.assert_not_called()

    def test_verified_geometry_exchange_cleans_temporary_files(self):
        self.configure()
        with patch.object(wtivo.subprocess, 'run', side_effect=self.emulate):
            vertices, faces, audit = wtivo.reconstruct(self.root, self.job, self.vertices, self.faces)
        self.assertTrue(np.array_equal(vertices, self.vertices))
        self.assertTrue(np.array_equal(faces, self.faces))
        self.assertFalse(audit['watertight_before_reduction'])
        self.assertTrue(audit['native_edge_watertight_before_reduction'])
        self.assertFalse(list(self.job.iterdir()))

    def test_character_geometry_uses_the_1536_largest_component_recipe(self):
        self.configure()

        def emulate_character(command, **kwargs):
            return self.emulate(command, grid=1536, component_mode='largest', **kwargs)

        with patch.object(wtivo.subprocess, 'run', side_effect=emulate_character):
            _, _, audit = wtivo.reconstruct(self.root, self.job, self.vertices, self.faces,
                                            grid=1536, component_mode='largest')
        self.assertEqual((audit['grid'], audit['component_mode']), (1536, 'largest'))

    def test_character_recipe_passes_distinct_grids_and_proxy_to_runtime(self):
        self.configure()
        options = dict(input_res=768, final_res=1024, proxy_points=100_000, proxy_eps_scale=1.5,
                       proxy_feature_weight=2.0, lambda_fill=30.0, threads=4, thin_iso_vox=-0.5,
                       faithc_component_mode='auto')

        def emulate_character(command, **kwargs):
            for flag, value in (('--proxy_points', '100000'), ('--proxy_eps_scale', '1.5'),
                                ('--proxy_feature_weight', '2.0'), ('--lambda_fill', '30.0'),
                                ('--threads', '4'), ('--thin_iso_vox', '-0.5')):
                self.assertEqual(command[command.index(flag) + 1], value)
            return self.emulate(command, grid=768, final_grid=1024, component_mode='auto', **kwargs)

        with patch.object(wtivo.subprocess, 'run', side_effect=emulate_character):
            _, _, report = wtivo.reconstruct(self.root, self.job, self.vertices, self.faces, options=options)
        self.assertEqual((report['grid'], report['final_grid'], report['proxy_points']), (768, 1024, 100_000))

    def test_failed_audit_leaves_no_stage_or_result(self):
        self.configure()
        with patch.object(wtivo.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '')):
            with self.assertRaisesRegex(ValueError, 'did not return a verified mesh'):
                wtivo.reconstruct(self.root, self.job, self.vertices, self.faces)
        self.assertFalse(list(self.job.iterdir()))

    def test_timeout_keeps_the_last_native_stage_in_the_log(self):
        self.configure()
        log = StringIO()
        output = f'{self.source}\nTetrahedralizing + exporting neighbors...\n'.encode()
        failure = subprocess.TimeoutExpired([], 1800, output=output)
        with (patch.object(wtivo.subprocess, 'run', side_effect=failure), redirect_stdout(log),
              self.assertRaisesRegex(wtivo.WTiVoTimeout, 'exceeded 30 minutes at 1024')):
            wtivo.reconstruct(self.root, self.job, self.vertices, self.faces)
        self.assertIn('Tetrahedralizing + exporting neighbors...', log.getvalue())
        self.assertNotIn(str(self.source), log.getvalue())
        self.assertFalse(list(self.job.iterdir()))


if __name__ == '__main__':
    unittest.main()
