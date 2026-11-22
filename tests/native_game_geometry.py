"""Server validation and process transport for native Game Asset geometry controls."""

from contextlib import contextmanager
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_game_geometry
import native_pixal3d
from native_workflow_options import GAME_ASSET_FINISHING, settings, summary


PROFILE = 'game_asset_native'


class NativeGeometryOptionsTests(unittest.TestCase):
    def test_changing_geometry_keeps_unedited_recipe_defaults(self):
        baseline = settings(PROFILE)
        self.assertEqual([baseline[stage]['steps'] for stage in ('3', '18', '23')], [12, 20, 12])
        self.assertEqual(baseline['312']['background'], '#000000')
        self.assertEqual(baseline['341']['normal_map_resolution'], 2048)
        defaults = dict(resolution=1536, triangles=12000, texture_size=2048, seed=56, remove_bg=True,
                        geometry_backend='pixal3d')
        initial = summary(PROFILE, baseline)
        self.assertEqual({key: initial[key] for key in defaults}, defaults)
        overrides = {'312': {'width': 768, 'height': 512, 'background': '#203040'},
                     '3': {'sampler_name': 'heun', 'scheduler': 'karras', 'denoise': .6},
                     '119': {'resolution': '64'}, '94': {'target_resolution': 1280},
                     '327': {'input_res': 768, 'final_res': 1024, 'proxy_points': 800_000},
                     '335': {'target_tris': 8_000}, '337': {'segmenter': 'adaptive', 'resolution': 0},
                     '338': {'crease_angle': 75},
                     '341': {'bake_normal_detail': False, 'material_base_color': '.8; .8; .8; 1'}}
        updated = settings(PROFILE, overrides)
        self.assertEqual(updated, {stage: {**values, **overrides.get(stage, {})} for stage, values in baseline.items()})
        self.assertEqual(summary(PROFILE, updated)['triangles'], 8000)

    def test_incompatible_geometry_controls_are_rejected_before_launch(self):
        cases = [
            ({'312': {'background': '#abc'}}, 'hex colour'),
            ({'3': {'sampler_name': 'sa_solver'}}, 'listed values'),
            ({'3': {'denoise': 0}}, 'outside the supported range'),
            ({'108': {'shift': 0}}, 'outside the supported range'),
            ({'94': {'target_resolution': 1300}}, 'multiple of 128'),
            ({'327': {'input_res': 8192, 'proxy_eps_scale': .25}}, 'proxy epsilon'),
            ({'327': {'proxy_points': 3}}, 'outside the supported range'),
            ({'341': {'material_base_color': '.1,1.1,0'}}, 'comma-separated values'),
            ({'341': {'load_output_images': False}}, 'fixed by this profile'),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                settings(PROFILE, overrides)

    def test_worker_receives_the_validated_finishing_stages(self):
        options = settings(PROFILE, {'335': {'target_tris': 7500}, '338': {'crease_angle': 55},
                                     '341': {'bake_normal_detail': False}, '358': {'steps': 7}})
        with TemporaryDirectory(prefix='native-finishing-contract-') as folder:
            root = Path(folder).resolve()
            work = root / 'finishing'
            raw = root / 'decoded.npz'
            raw.touch()
            progress = []

            @contextmanager
            def process(command, *, cwd, **_):
                self.assertEqual(cwd, root)
                config = Path(command[command.index('--options') + 1])
                received = json.loads(config.read_text(encoding='utf-8'))
                self.assertEqual(received, {stage: options[stage] for stage in GAME_ASSET_FINISHING})
                work.mkdir()
                (work / 'summary.json').write_text('{"triangles": 7512}', encoding='utf-8')
                yield SimpleNamespace(stdout=['FINISH_PROGRESS Decimating the low mesh\n'],
                                      wait=lambda: 0, poll=lambda: 0)

            with (patch.object(native_game_geometry, '_runtime', return_value={'python': Path(sys.executable)}),
                  patch.object(native_game_geometry.subprocess, 'Popen', side_effect=process)):
                surface, receipt = native_game_geometry.finish_geometry(raw, work, progress.append, options=options)
            self.assertEqual(surface, work / 'uv-bake/normal-ready.glb')
            self.assertEqual(receipt, {'triangles': 7512})
            self.assertEqual(progress, ['Decimating the low mesh'])
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['decoded.npz', 'finishing'])

    def test_mask_worker_needs_only_birefnet_and_runs_offline_on_cpu(self):
        with TemporaryDirectory(prefix='native-mask-contract-') as folder:
            root = Path(folder).resolve()
            python, image = root / 'python.exe', root / 'reference.png'
            python.touch()
            image.touch()
            weights = root / 'weights'
            checkpoint = weights / native_pixal3d.BIREFNET
            checkpoint.parent.mkdir(parents=True)
            checkpoint.touch()
            source = root / 'birefnet'
            source.mkdir()
            (source / 'birefnet.py').touch()
            config = root / 'config.json'
            config.write_text(json.dumps(dict(python=python.name, weights=weights.name, birefnet=source.name,
                                              dependencies=[])), encoding='utf-8')

            def run(command, *, cwd, env, **_):
                self.assertEqual(command[0], str(python))
                self.assertEqual(command[-3], str(image))
                self.assertEqual(command[-1], '--mask-only')
                self.assertEqual(env['HF_HUB_OFFLINE'], '1')
                self.assertEqual(env['CUDA_VISIBLE_DEVICES'], '-1')
                scratch = Path(command[-2])
                self.assertEqual(scratch.parent, Path(cwd).resolve())
                scratch.write_bytes(b'mask fixture')
                return SimpleNamespace(returncode=0, stdout='')

            with (patch.object(native_pixal3d, 'CONFIG', config),
                  patch.object(native_pixal3d.subprocess, 'run', side_effect=run)):
                self.assertTrue(native_pixal3d.texture_mask_status()['available'])
                self.assertFalse(native_pixal3d.runtime_status()['available'])
                path = native_pixal3d.prepare_texture_mask(image, root / 'work/foreground.npy')
            self.assertEqual(path.read_bytes(), b'mask fixture')
            self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
