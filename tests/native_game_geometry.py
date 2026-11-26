"""Server validation and process transport for native Game Asset geometry controls."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_game_finish
import native_game_geometry
import native_pixal3d
from installer import install_native_game_geometry as provision
from installer import install_native_ultratex as texture_provision
from native_ultratex import dependencies as texture_dependencies
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
            progress, quad_progress, wtivo_progress = [], [], []

            @contextmanager
            def process(command, *, cwd, **_):
                self.assertEqual(cwd, root)
                self.assertEqual(command[4:6], ['-X', 'utf8'])
                config = Path(command[command.index('--options') + 1])
                received = json.loads(config.read_text(encoding='utf-8'))
                self.assertEqual(received, {stage: options[stage] for stage in GAME_ASSET_FINISHING})
                work.mkdir()
                (work / 'summary.json').write_text('{"triangles": 7512}', encoding='utf-8')
                yield SimpleNamespace(stdout=[
                    'FINISH_PROGRESS Reconstructing the mesh with QuadDC\n',
                    'FINISH_QUAD_PROGRESS Filtering inner faces with QuadDC (1/3 batches complete)\n',
                    'FINISH_PROGRESS Closing the mesh with WTiVo\n',
                    'FINISH_WTIVO_PROGRESS WTiVo · Building tetrahedra · 0m 32s elapsed · 40m limit\n',
                    'FINISH_PROGRESS Preparing high- and low-poly meshes with LODTailor\n',
                ], wait=lambda: 0, poll=lambda: 0)

            with (patch.object(native_game_geometry, '_runtime', return_value={'python': Path(sys.executable)}),
                  patch.object(native_game_geometry.subprocess, 'Popen', side_effect=process)):
                surface, receipt = native_game_geometry.finish_geometry(
                    raw, work, progress.append, options=options, quad_progress=quad_progress.append,
                    wtivo_progress=wtivo_progress.append)
            self.assertEqual(surface, work / 'uv-bake/normal-ready.glb')
            self.assertEqual(receipt, {'triangles': 7512})
            self.assertEqual(progress, ['Reconstructing the mesh with QuadDC', 'Closing the mesh with WTiVo',
                                        'Preparing high- and low-poly meshes with LODTailor'])
            self.assertEqual(quad_progress, ['Filtering inner faces with QuadDC (1/3 batches complete)'])
            self.assertEqual(wtivo_progress, ['WTiVo · Building tetrahedra · 0m 32s elapsed · 40m limit'])
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['decoded.npz', 'finishing'])

    def test_vertex_merge_preserves_the_baked_surface_for_ultratex(self):
        options = settings(PROFILE)
        cases = (
            (12_000, 12_000, 2, 2, False),
            (12_000, 12_000, 12_000, 11_998, False),
            (12_000, 11_998, 11_998, 11_998, True),
            (12_000, 12_000, 12_000, 12_000, True),
        )
        for baked_total, baked_usable, welded_total, welded_usable, keep_weld in cases:
            with self.subTest(welded_total=welded_total, welded_usable=welded_usable, keep_weld=keep_weld):
                with TemporaryDirectory(prefix='native-merge-') as folder:
                    work = Path(folder) / 'uv-bake'
                    low = {'vertices': [], 'faces': range(baked_total)}
                    baked = work / 'baked.glb'

                    def bake(*_args, **_kwargs):
                        baked.write_bytes(b'baked mesh, UVs and normal map')
                        return baked, {'warnings': ['Bake warning']}

                    def weld(_toolkit, source, output, **_kwargs):
                        self.assertEqual(source, baked)
                        output.write_bytes(b'welded mesh')

                    reports = [dict(triangles=baked_total, surface_triangles=baked_usable, normal_size=2048),
                               dict(triangles=welded_total, surface_triangles=welded_usable, normal_size=2048)]
                    with (patch.object(native_game_finish, '_step', side_effect=lambda node, *_: options[node]),
                          patch.object(native_game_finish, 'write_character_glb'),
                          patch.object(native_game_finish, 'unwrap_character_uv', return_value=low),
                          patch.object(native_game_finish, 'bake_normal_atlas', side_effect=bake),
                          patch.object(native_game_finish, 'texture_safe_merge', side_effect=weld),
                          patch.object(native_game_finish, '_normal_ready_model', side_effect=reports),
                          patch('builtins.print')):
                        model, receipt = native_game_finish.finish_reduced_character(
                            None, None, None, work, donor=low, low=low, device='cpu')

                    self.assertEqual(model, work / 'normal-ready.glb')
                    self.assertEqual(model.read_bytes(), b'welded mesh' if keep_weld else b'baked mesh, UVs and normal map')
                    self.assertEqual(receipt['triangles'], welded_total if keep_weld else baked_total)
                    self.assertEqual(receipt['welded_triangles'], welded_total)
                    self.assertEqual(receipt['warnings'][0], 'Bake warning')
                    self.assertEqual(len(receipt['warnings']), 1 if keep_weld else 2)

    def test_quad_filter_reports_completed_batches_and_remaining_time(self):
        with TemporaryDirectory(prefix='native-quad-progress-') as folder:
            source = Path(folder) / 'remeshing.py'
            source.write_text(
                'def reconstruct_mesh_dc_quad(points):\n'
                '    device, chunk_size = "cuda:0", 2\n'
                '    def chunked_sdf_raystab(pts):\n'
                '        N = len(pts)\n'
                '        result = []\n'
                '        for i in range(0, N, chunk_size):\n'
                '            result.extend(query(pts[i:i + chunk_size]))\n'
                '        return result\n'
                '    return chunked_sdf_raystab(points)\n', encoding='utf-8')
            events = []

            def query(points):
                events.append(tuple(points))
                return points

            def synchronize(device):
                self.assertEqual(device, 'cuda:0')
                events.append('synchronized')

            remeshing = SimpleNamespace(query=query, torch=SimpleNamespace(cuda=SimpleNamespace(
                synchronize=synchronize)))
            reconstruct = native_game_finish._quad_reconstruction_with_progress(remeshing, source, events.append)
            with patch.object(native_game_finish.time, 'perf_counter', side_effect=[10, 14, 18]):
                self.assertEqual(reconstruct([1, 2, 3, 4, 5]), [1, 2, 3, 4, 5])
            self.assertEqual(events, [
                'Filtering inner faces with QuadDC (0/3 batches complete)',
                'synchronized', (1, 2), 'synchronized',
                'Filtering inner faces with QuadDC (1/3 batches complete, about 6 s remaining)',
                (3, 4), 'synchronized',
                'Filtering inner faces with QuadDC (2/3 batches complete, about 2 s remaining)',
                (5,), 'synchronized',
                'Filtering inner faces with QuadDC (3/3 batches complete)',
            ])

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

            popen = subprocess.Popen
            def run(command, *, cwd, env, **options):
                self.assertEqual(command[0], str(python))
                self.assertEqual(command[-3], str(image))
                self.assertEqual(command[-1], '--mask-only')
                self.assertEqual(env['HF_HUB_OFFLINE'], '1')
                self.assertEqual(env['CUDA_VISIBLE_DEVICES'], '-1')
                scratch = Path(command[-2])
                self.assertEqual(scratch.parent, Path(cwd).resolve())
                script = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(b'mask fixture')"
                return popen([sys.executable, '-c', script, str(scratch)], cwd=cwd, env=env, **options)

            with (patch.object(native_pixal3d, 'CONFIG', config),
                  patch.object(native_pixal3d.subprocess, 'Popen', side_effect=run)):
                self.assertTrue(native_pixal3d.texture_mask_status()['available'])
                self.assertFalse(native_pixal3d.runtime_status()['available'])
                path = native_pixal3d.prepare_texture_mask(image, root / 'work/foreground.npy')
            self.assertEqual(path.read_bytes(), b'mask fixture')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_background_mask_is_stopped_if_geometry_fails_and_preserves_existing_output(self):
        popen = subprocess.Popen
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image, mask = root / 'ref.png', root / 'existing.npy'
            image.touch()
            mask.write_bytes(b'previous mask')
            children = []
            def launch(_command, **options):
                process = popen([sys.executable, '-c', 'import time; time.sleep(60)'], **options)
                children.append(process)
                return process
            with (patch.object(native_pixal3d, '_config', return_value={'python': sys.executable}),
                  patch.object(native_pixal3d.subprocess, 'Popen', side_effect=launch)):
                with self.assertRaisesRegex(ValueError, 'geometry failed'):
                    with native_pixal3d.pending_texture_mask(image, mask) as pending:
                        self.assertIsNone(children[0].poll())
                        self.assertFalse(pending.done())
                        raise ValueError('geometry failed')
                self.assertIsNotNone(children[0].poll())
                with self.assertRaisesRegex(RuntimeError, 'foreground preparation failed'):
                    pending.result()
            self.assertEqual(mask.read_bytes(), b'previous mask')
            self.assertEqual(set(root.iterdir()), {image, mask})


class NativeGameSetupTests(unittest.TestCase):
    @staticmethod
    def package(target, name, version):
        info = target / f'{name}-{version}.dist-info'
        info.mkdir(parents=True)
        (info / 'METADATA').write_text(f'Name: {name}\nVersion: {version}\n', encoding='utf-8')

    def test_ultratex_reuses_the_qualified_split_overlay(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            packages = root / texture_dependencies.GEOMETRY
            base, support = packages / 'deps', packages / 'support-deps'
            self.package(base, 'scipy', '1.18.1')
            self.package(support, 'scipy', '1.15.3')
            self.package(base, 'pillow', '13.0.0')
            provision.write_json(root, provision.SHAPE_CONFIG, {'dependencies': [str(base), str(support)]})
            lock = root / texture_dependencies.LOCK
            lock.parent.mkdir()
            lock.write_text('scipy==1.15.3\npillow==13.0.0\n', encoding='utf-8')
            with patch.object(texture_provision, 'install_packages') as install:
                texture_provision._install_dependencies(root)
            install.assert_not_called()
            self.assertEqual(texture_dependencies.dependency_paths(root), (support, base))

    def test_ultratex_setup_installs_only_missing_pins_with_merged_geometry(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            merged = root / texture_dependencies.GEOMETRY / 'studio-deps'
            self.package(merged, 'scipy', '1.15.3')
            self.package(root / texture_dependencies.PYTHON_SITE, 'sympy', '1.14.0')
            provision.write_json(root, provision.SHAPE_CONFIG, {'dependencies': [str(merged)]})
            lock = root / texture_dependencies.LOCK
            lock.parent.mkdir()
            missing = 'gguf==0.19.0 --hash=sha256:' + 'a' * 64
            lock.write_text(f'scipy==1.15.3\nsympy==1.14.0\n{missing}\n', encoding='utf-8')

            def install(location, python, target, requirements):
                self.assertEqual(location, root)
                self.assertEqual(target, texture_dependencies.EXTRA)
                self.assertEqual(requirements.read_text(encoding='utf-8'), missing + '\n')
                self.package(root / target, 'gguf', '0.19.0')

            with patch.object(texture_provision, 'install_packages', side_effect=install) as helper:
                texture_provision._install_dependencies(root)
            helper.assert_called_once()
            self.assertEqual(texture_dependencies.dependency_paths(root), (root / texture_dependencies.EXTRA, merged))

    def test_ultratex_finds_the_compiler_in_the_imported_triton_package(self):
        with TemporaryDirectory() as folder:
            package = Path(folder).resolve() / 'shared-deps/triton'
            compiler = package / 'runtime/tcc/tcc.exe'
            compiler.parent.mkdir(parents=True)
            compiler.touch()
            with patch.dict(sys.modules, {'triton': SimpleNamespace(__file__=str(package / '__init__.py'))}):
                self.assertEqual(texture_dependencies.triton_compiler(), compiler)

    def test_public_recipe_matches_the_qualified_windows_recipe(self):
        source = provision.HERE.parent / 'comfy_workflows/game-asset-character-05.json'
        windows = source.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        self.assertEqual(hashlib.sha256(windows).hexdigest(), provision.RECIPE_SHA256)
        spec = provision._manifest()
        self.assertEqual({item['path'] for item in spec['weights']}, {
            *native_pixal3d.CHECKPOINTS.values(), native_pixal3d.SHAPE_VAE,
            native_pixal3d.VISION, native_pixal3d.BIREFNET, native_pixal3d.MOGE,
        })
        self.assertIn('comfy/ldm/trellis2/model.py', {entry['path'] for entry in spec['operators']})
        self.assertTrue(all(entry['url'].startswith('https://raw.githubusercontent.com/')
                            for entry in spec['operators']))

    def test_managed_repair_reinstalls_missing_components_before_receipt(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            python = root / provision.RUNTIME_PATH
            python.parent.mkdir(parents=True)
            python.touch()
            for path, settings in provision._configurations().items():
                provision.write_json(root, path, settings)
            backends = dict.fromkeys(provision.ENGINES, True)
            with (patch.object(provision, 'verify', side_effect=[FileNotFoundError(), backends]),
                  patch.object(provision, '_install_operators') as operators,
                  patch.object(provision, '_install_vision') as vision,
                  patch.object(provision, '_install_wheels') as wheels,
                  patch.object(provision, '_install_blender'),
                  patch.object(provision, '_install_wtivo')):
                self.assertEqual(provision.install(root), backends)
            operators.assert_called_once()
            vision.assert_called_once()
            wheels.assert_called_once()
            self.assertEqual(json.loads((root / provision.SHAPE_RECEIPT).read_text()), backends)

    def test_game_blender_preserves_supported_configuration_and_falls_back(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            custom = root / 'custom/blender.exe'
            bundled = root / provision.BLENDER / 'blender.exe'
            for path in (custom, bundled):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with (patch.object(native_game_geometry, 'ROOT', root),
                  patch('studio_import.find_blender', return_value=custom),
                  patch.object(native_game_geometry, '_supports_blender', return_value=True) as check):
                self.assertEqual(native_game_geometry.find_game_blender(), custom)
                check.side_effect = lambda candidate, _: candidate == bundled
                self.assertEqual(native_game_geometry.find_game_blender(), bundled)


if __name__ == '__main__':
    unittest.main()
