"""Exercise optional High Detail adapters without loading models or using CUDA."""

from importlib.util import find_spec
import hashlib
import json
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import native_high_detail as native
from installer import install_native_high_detail as provision


class HighDetailSettingsTests(unittest.TestCase):
    def test_cpu_mask_reports_elapsed_and_critical_time_separately(self):
        for ready, finished, wait, overlap in [(True, 7., 0., 5.), (False, 14., 4., 8.)]:
            with self.subTest(ready=ready), TemporaryDirectory() as directory:
                scratch = Path(directory)
                receipt = {}

                def texture(_geometry, _ref, mask, candidate, _work, report, *, options):
                    self.assertEqual(mask, scratch / 'foreground.npy')
                    candidate.write_bytes(b'textured')
                    report('Baking the texture')
                    return receipt

                future = Mock()
                future.done.return_value = ready
                future.result.return_value = (2., finished)
                progress = Mock()
                destination = scratch / 'finished.glb'
                with (patch.object(native, 'perf_counter', return_value=10.),
                      patch('native_game_profile.texture_mesh', side_effect=texture)):
                    native._retexture_ultratex(scratch/'input.glb', scratch/'ref.png', destination, scratch,
                                               future, 4096, progress)
                self.assertEqual(receipt['reference_mask'], dict(elapsed_seconds=finished-2.,
                                                                wait_seconds=wait, overlap_seconds=overlap))
                self.assertEqual(destination.read_bytes(), b'textured')
                self.assertEqual(progress.call_args_list[-1].args, ('Baking the texture', 96))
                self.assertEqual(progress.call_count, 1 if ready else 2)

    def test_ready_upgrade_verification_requires_patched_flex_gemm(self):
        expression = b'(out_offset_n[:, None] * Co + out_offset_co[None, :])'
        corrected = b'(out_offset_n[:, None].to(tl.int64) * Co + out_offset_co[None, :])'
        checksum = lambda source: hashlib.sha256(source).hexdigest()
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / provision.CONFIG
            config.parent.mkdir()
            config.write_text(json.dumps(provision._config()), encoding='utf-8')
            package = root / provision.PACKAGE
            package.mkdir(parents=True)
            folder = root / provision.CUDA_PACKAGES / provision.FLEX_GEMM_SPCONV
            folder.mkdir(parents=True)
            pins = {}
            for name in provision.FLEX_GEMM_OUTPUT_OFFSETS:
                (folder / name).write_bytes(expression)
                pins[name] = (checksum(expression), checksum(corrected))
            spec = {'patches': [], 'assets': []}
            runtime = {'package': package, 'u2net': root, 'models': root}
            distributions = [SimpleNamespace(metadata={'Name': name}, version=version)
                             for name, version in provision.EXPECTED_DISTRIBUTIONS.items()]
            with (patch.object(provision, 'FLEX_GEMM_OUTPUT_OFFSETS', pins),
                  patch.object(provision.importlib.metadata, 'distributions', return_value=distributions),
                  patch.object(provision, '_toolchain'), patch.object(provision, '_runtime', return_value=runtime),
                  patch.object(provision, '_manifest', return_value=spec), patch.object(provision, '_package_receipt'),
                  patch.object(provision, '_foundation_weights', return_value=({}, [])),
                  patch.object(provision, 'verify_packages'), patch.object(provision, 'verify_asset'),
                  patch.object(provision, '_smoke') as smoke,
                  patch.object(provision, 'fetch_asset', side_effect=AssertionError('Unexpected download'))):
                with self.assertRaisesRegex(ValueError, 'requires the pinned FlexGEMM output indexing fix'):
                    provision.verify(root)
                smoke.assert_not_called()
                self.assertTrue(all((folder / name).read_bytes() == expression for name in pins))

                provision._install_sources(root, sys.executable, spec)

                self.assertTrue(all((folder / name).read_bytes() == corrected for name in pins))
                self.assertEqual(provision.verify(root), config)
                smoke.assert_called_once()

    def test_schema_uses_same_values_as_standalone_runtime(self):
        schema = json.loads((ROOT / 'native_workflows/high_detail_native.json').read_text(encoding='utf-8'))
        defaults = {stage['id']: {field['name']: field['default'] for field in stage['fields']}
                    for stage in schema['stages'] if 'fields' in stage}
        self.assertEqual(defaults, native.DEFAULTS)

    def test_runtime_reads_foundation_dino_without_an_alias(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'native-high-detail.json'
            entry = {'schema': 1, 'package': 'package', 'models': 'models', 'u2net': 'rembg',
                     'dinov3': 'engine/MODELS/dinov3'}
            config.write_text(json.dumps(entry), encoding='utf-8')
            source = root / entry['package'] / native.MANIFEST
            source.parent.mkdir()
            source.write_text(json.dumps({'schema': 1, 'trellis_gguf_revision': native.PIPELINE_REVISION,
                                          'quant_revision': native.QUANT_REVISION}), encoding='utf-8')
            checkpoint = root / entry['dinov3']
            paths = [checkpoint / 'config.json', checkpoint / 'model.safetensors', root / entry['u2net'] / 'u2net.onnx']
            paths.extend(root / 'models/Trellis2' / name for name in native.MODEL_FILES)
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            self.assertEqual(native._runtime(config)['dinov3'], checkpoint.resolve())
            self.assertFalse((root / 'models' / native.DINO_DIRECTORY).exists())

    def test_default_sampler_and_occupancy_pass_through_unchanged(self):
        coordinates = object()
        dino = Path('engine/MODELS/dinov3').resolve()

        class PinnedPipeline:
            def __init__(self):
                self._pretrained_args = {'image_cond_model': {'args': {'model_name': 'default-folder'}}}

            @classmethod
            def from_pretrained(cls, *_args, **_kwargs):
                return cls()

            def cuda(self):
                pass

            def _get_sampler(self, _name, default):
                return default

            def sample_sparse_structure(self, _cond, _resolution):
                return coordinates

        module = 'trellis2_gguf.pipelines.trellis2_image_to_3d'
        with patch.dict(sys.modules, {module: SimpleNamespace(Trellis2ImageTo3DPipeline=PinnedPipeline)}), \
             patch.object(native, '_reset_cuda'):
            pipeline = native._load_model(Mock(), {'models': Path('.'), 'dinov3': dino}, native.DEFAULTS['44'])
        self.assertEqual(pipeline._pretrained_args['image_cond_model']['args']['model_name'], str(dino))
        pipeline.stage_settings = native.DEFAULTS['17']
        sampler = object()
        self.assertIs(pipeline._get_sampler('euler', sampler), sampler)
        self.assertIs(pipeline.sample_sparse_structure(None, 32), coordinates)

    def test_quad_repair_passes_selected_band(self):
        class ReachedCuMesh(Exception):
            pass

        reconstruct = Mock(side_effect=ReachedCuMesh)
        modules = {name: Mock() for name in ('pymeshlab', 'trimesh', 'meshlib')}
        modules['cumesh'] = SimpleNamespace(remeshing=SimpleNamespace(reconstruct_mesh_dc_quad=reconstruct))
        with patch.dict(sys.modules, modules), patch.object(native, '_reset_cuda'):
            with self.assertRaises(ReachedCuMesh):
                native._repair(Mock(), Mock(), native.DEFAULTS['41'] | {'remesh_band': 2},
                               native.DEFAULTS['11'], native.DEFAULTS['14'], lambda *_: None, start=0)
        self.assertEqual(reconstruct.call_args.kwargs['band'], 2)

    def test_forced_material_sides_preserve_other_material_fields(self):
        textured = {'material': 0}
        colored = {'attributes': {'COLOR_0': 2}}
        doc = {'meshes': [{'primitives': [textured, colored]}],
               'materials': [{'alphaMode': 'MASK', 'doubleSided': True}]}
        native._material_sides(doc, False)
        self.assertEqual(doc['materials'], [{'alphaMode': 'MASK', 'doubleSided': False}, {'doubleSided': False}])
        self.assertEqual(colored, {'attributes': {'COLOR_0': 2}, 'material': 1})

    @unittest.skipUnless(find_spec('trimesh') and find_spec('torch'), 'requires the Studio mesh runtime')
    def test_uv_donor_preserves_chart_seams_normals_and_texture_coordinates(self):
        import numpy as np
        import torch
        import trimesh
        from PIL import Image
        from glb_data import accessor, read
        from retexture_worker import append_textures

        surface = trimesh.creation.icosphere(subdivisions=0)
        pipeline = Mock()
        pipeline.preprocess_mesh.return_value = surface
        # Two adjacent faces share geometric vertices but have separate chart coordinates.
        chart_map = np.array(surface.faces[:2]).ravel()
        vertices = np.array(surface.vertices[chart_map], dtype=np.float32)
        faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
        uv = np.array([[0,.1], [.2,.3], [.4,.5], [.6,.7], [.8,.9], [1,0]], dtype=np.float32)
        chart = Mock()
        chart.uv_unwrap.return_value = tuple(torch.from_numpy(v.copy()) for v in (vertices, faces, uv, chart_map))
        modules = {'cumesh': SimpleNamespace(CuMesh=lambda: chart)}
        settings = native.DEFAULTS['25'] | {'use_custom_normals': True}
        with patch.dict(sys.modules, modules), patch.object(torch.Tensor, 'cuda', lambda value: value):
            model = native._unwrap_for_ultratex(pipeline, surface, settings)
        pipeline.preprocess_mesh.assert_called_once_with(surface)
        np.testing.assert_array_equal(model.vertices, vertices[:, [0, 2, 1]] * [1, 1, -1])
        np.testing.assert_array_equal(model.faces, faces)
        expected_normals = surface.vertex_normals[chart_map][:, [0, 2, 1]] * [1, 1, -1]
        np.testing.assert_allclose(model.vertex_normals, expected_normals)

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'donor.glb'
            model.export(path)
            document, binary = read(path)
            primitive = document['meshes'][0]['primitives'][0]
            np.testing.assert_allclose(accessor(document, binary, primitive['attributes']['TEXCOORD_0']), uv,
                                       atol=1e-7)
            self.assertFalse(document.get('images'))
            self.assertEqual(document['materials'][primitive['material']]['alphaMode'], 'OPAQUE')
            document.setdefault('images', [])
            document.setdefault('textures', [])
            result = path.with_name('result.glb')
            append_textures(result, document, binary, Image.new('RGB', (2, 2)), Image.new('RGB', (2, 2)))
            finished, _ = read(result)
            self.assertEqual(len(finished['images']), 2)
            self.assertEqual(finished['accessors'], document['accessors'])

    @unittest.skipUnless(find_spec('trimesh'), 'trimesh is a Studio runtime dependency')
    def test_material_sides_reaches_glb(self):
        import trimesh

        mesh = trimesh.creation.box()
        mesh.visual.vertex_colors = [255, 127, 0, 255]
        blob = mesh.export(file_type='glb', tree_postprocessor=lambda tree: native._material_sides(tree, True))
        length, = struct.unpack_from('<I', blob, 12)
        doc = json.loads(blob[20:20 + length])
        primitive = doc['meshes'][0]['primitives'][0]
        self.assertTrue(doc['materials'][primitive['material']]['doubleSided'])
        self.assertIn('COLOR_0', primitive['attributes'])

    @unittest.skipUnless(find_spec('tqdm'), 'tqdm is a Studio runtime dependency')
    def test_dino_uses_foundation_and_additional_substeps(self):
        prediction = Mock(side_effect=lambda _model, _x, _t, _cond, guidance_strength: guidance_strength)
        sampler = SimpleNamespace(_inference_model=prediction)
        first = native._DinoLockedSampler(sampler, native.DEFAULTS['25'] | {
            'dino_lock': .2, 'dino_foundation_cap': .95, 'dino_substeps': 1,
        })
        second = native._DinoLockedSampler(sampler, native.DEFAULTS['25'] | {
            'dino_lock': .2, 'dino_foundation_cap': .95, 'dino_substeps': 3,
        })
        run = lambda solver: solver.sample(None, 1., 'reference', steps=4, verbose=False, guidance_strength=3).samples
        first_result = run(first)
        self.assertEqual(prediction.call_count, 8)
        self.assertAlmostEqual(first._strength(0, 4), .95)
        self.assertAlmostEqual(first._strength(3, 4), .2)
        prediction.reset_mock()
        self.assertNotEqual(run(second), first_result)
        self.assertGreater(prediction.call_count, 8)

    @unittest.skipUnless(find_spec('numpy') and find_spec('scipy'), 'SciPy is a Studio runtime dependency')
    def test_occupancy_repair_fills_a_hole_and_can_keep_only_the_shell(self):
        import numpy as np

        voxels = np.zeros((11, 11, 11), dtype=bool)
        voxels[2:-2, 2:-2, 2:-2] = True
        voxels[5, 5, 5] = False
        settings = native.DEFAULTS['17'] | {'keep_only_shell': False}
        repaired = native._repair_occupancy(voxels, settings)
        self.assertTrue(repaired[5, 5, 5])
        shell = native._repair_occupancy(voxels, settings | {'keep_only_shell': True})
        self.assertFalse(shell[5, 5, 5])
        self.assertTrue(shell[2, 5, 5])


if __name__ == '__main__':
    unittest.main()
