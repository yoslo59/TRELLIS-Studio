"""Preview/commit agreement, source preservation and rejection of stale face patches."""
from pathlib import Path
import runpy
import struct
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from face_refine_geometry import apply_patch, supported_surface
from glb_data import accessor, read

fixture = runpy.run_path(str(Path(__file__).with_name('baked_game_copy.py')))['fixture']


class FacePatchTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root/'source.glb'
        self.patch = root/'delta.bin'
        self.copy = root/'copy.glb'
        fixture(self.source)
        self.patch.write_bytes(struct.pack('<4sIIffff', b'FRP1', 1, 1, .2, 0, 1, -1))

    def test_continuous_strength_and_original(self):
        original = self.source.read_bytes()
        apply_patch(self.source, self.patch, self.copy, 0)
        self.assertEqual(self.copy.read_bytes(), original)
        for strength in (.5, 1):
            apply_patch(self.source, self.patch, self.copy, strength)
            doc, blob = read(self.copy)
            primitive = doc['meshes'][0]['primitives'][0]
            attrs = primitive['attributes']
            xyz = accessor(doc, blob, attrs['POSITION'])
            normal = accessor(doc, blob, attrs['NORMAL'])
            self.assertAlmostEqual(xyz[1, 2], strength*.2)
            self.assertTrue(np.all(xyz[[0,2,3],2] == 0))
            expected = np.array([0., strength, 1-strength])
            np.testing.assert_allclose(normal[1], expected/np.linalg.norm(expected))
        self.assertEqual(self.source.read_bytes(), original)

    def test_bad_patch_leaves_previous_copy_untouched(self):
        self.copy.write_bytes(b'previous')
        self.patch.write_bytes(self.patch.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            apply_patch(self.source,self.patch,self.copy,.8)
        self.assertEqual(self.copy.read_bytes(), b'previous')

    def test_only_static_sources(self):
        doc, _ = read(self.source)
        doc['skins'] = [{'joints': [0]}]
        with self.assertRaisesRegex(ValueError, 'before rigging'):
            supported_surface(doc)

    def test_thin_triangle_at_uv_seam_stays_oriented_at_every_strength(self):
        from face_refine_transfer import _no_fold_shifts
        xyz = np.array([[0,0,0], [1,0,0], [1,.001,.01], [1,.001,.01]], dtype='float32')
        faces = np.array([[0,1,2], [0,1,3]])
        # The unguarded proposal reverses the geometric normals of both copies.
        initial = np.array([0., .018, -.018, -.018])
        def geometric(z):
            p = xyz.astype('float64')
            p[:,2] += z
            triangles = p[faces]
            return np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
        before = geometric(0)
        self.assertTrue(np.all((before*geometric(initial)).sum(axis=1) < 0))
        guarded = _no_fold_shifts(xyz,faces,initial)
        self.assertEqual(guarded[2], guarded[3])
        for intensity in np.linspace(0,1,11):
            self.assertTrue(np.all((before*geometric(guarded*intensity)).sum(axis=1) > 0))


if __name__ == '__main__':
    unittest.main()
