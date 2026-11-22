"""Exercise optional High Detail adapters without loading models or using CUDA."""

from importlib.util import find_spec
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import native_high_detail as native


class HighDetailSettingsTests(unittest.TestCase):
    def test_schema_uses_same_values_as_standalone_runtime(self):
        schema = json.loads((ROOT / 'native_workflows/high_detail_native.json').read_text(encoding='utf-8'))
        defaults = {stage['id']: {field['name']: field['default'] for field in stage['fields']}
                    for stage in schema['stages'] if 'fields' in stage}
        self.assertEqual(defaults, native.DEFAULTS)

    def test_default_sampler_and_occupancy_pass_through_unchanged(self):
        coordinates = object()

        class PinnedPipeline:
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
            pipeline = native._load_model(Mock(), {'models': Path('.')}, native.DEFAULTS['44'])
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
