"""Bounded CPU unit checks for optional HD-to-game transfer; no Studio data."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch
import io
import json
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import baked_game_copy as game


def fixture(path, change=None, *, automatic_normal=False, color=None):
    positions = np.array([[-1, 0, 0], [1, 0, 0], [1, 2, 0], [-1, 2, 0]], '<f4')
    arrays = [positions, np.tile([0, 0, 1], (4, 1)).astype('<f4'),
              np.array([[0, 0], [1, 0], [1, 1], [0, 1]], '<f4'), np.array([0, 1, 2, 0, 2, 3], '<u4')]
    if automatic_normal:
        arrays[1][0] = 0
    binary = bytearray()
    views, accessors = [], []
    for array, kind in zip(arrays, ['VEC3', 'VEC3', 'VEC2', 'SCALAR']):
        views.append(dict(buffer=0, byteOffset=len(binary), byteLength=array.nbytes))
        binary += array.tobytes()
        accessors.append(dict(bufferView=len(views) - 1, componentType=5125 if kind == 'SCALAR' else 5126,
                              count=len(array), type=kind))
    image = io.BytesIO()
    Image.new('RGB', (4, 4), (180, 70, 30)).save(image, format='PNG')
    views.append(dict(buffer=0, byteOffset=len(binary), byteLength=len(image.getvalue())))
    binary += image.getvalue()
    binary += b'\0' * (-len(binary) % 4)
    data = dict(asset=dict(version='2.0'), buffers=[dict(byteLength=len(binary))], bufferViews=views,
                accessors=accessors, images=[dict(bufferView=4, mimeType='image/png')], textures=[dict(source=0)],
                materials=[dict(pbrMetallicRoughness=dict(baseColorTexture=dict(index=0)))],
                meshes=[dict(primitives=[dict(attributes=dict(POSITION=0, NORMAL=1, TEXCOORD_0=2), indices=3, material=0)])],
                nodes=[dict(mesh=0)], scenes=[dict(nodes=[0])], scene=0)
    if color is not None:
        colors = np.repeat(np.asarray([color], dtype='<f4'), len(positions), axis=0).tobytes()
        views.append(dict(buffer=0, byteOffset=len(binary), byteLength=len(colors)))
        accessors.append(dict(bufferView=len(views)-1, componentType=5126, count=len(positions), type='VEC4'))
        data['meshes'][0]['primitives'][0]['attributes']['COLOR_0'] = len(accessors)-1
        binary += colors
        data['buffers'][0]['byteLength'] = len(binary)
    if change:
        change(data)
    descriptor = json.dumps(data).encode()
    descriptor += b' ' * (-len(descriptor) % 4)
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 28 + len(descriptor) + len(binary)) +
                     struct.pack('<II', len(descriptor), 0x4E4F534A) + descriptor +
                     struct.pack('<II', len(binary), 0x004E4942) + binary)


class GameCopyTests(TestCase):
    def test_defaults_and_strict_options(self):
        self.assertEqual(game.normalize_game_copy_options({})['triangles'], 28000)
        self.assertEqual(game.normalize_game_copy_options({'profile': 'humanoid'})['triangles'], 14000)
        for invalid in ({'triangles': True}, {'triangles': 4999}, {'triangles': 150001}, {'triangles': 14000.},
                        {'texture_size': 8192}, {'bake_normals': 1}, {'profile': 'quad'}, {'unexpected': 0}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                game.normalize_game_copy_options(invalid)

    def test_protected_region_options(self):
        region = dict(center=[-.25, 1, 0], radius=.15, strength=1)
        normalized = game.normalize_game_copy_options({'protected_regions': [region]})
        self.assertEqual(normalized['protected_regions'], [region])
        self.assertNotIn('protected_regions', game.normalize_game_copy_options({'protected_regions': []}))
        for invalid in (None, region, [region] * (game.PROTECTED_REGION_LIMIT + 1),
                        [{**region, 'unknown': True}], [{**region, 'center': [0, 0]}],
                        [{**region, 'center': [0, 0, float('nan')]}],
                        [{**region, 'center': [0, 0, 10 ** 400]}],
                        [{**region, 'center': [False, 0, 0]}], [{**region, 'radius': True}],
                        [{**region, 'radius': 0}], [{**region, 'radius': float('inf')}],
                        [{**region, 'strength': -.01}], [{**region, 'strength': 1.01}],
                        [{**region, 'strength': '1'}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                game.normalize_game_copy_options({'protected_regions': invalid})

    def test_static_capability_and_unused_material(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'source.glb'
            fixture(path)
            result = game.inspect_baked_game_copy(path)
            self.assertTrue(result['supported'], result)
            self.assertEqual(result['triangles'], 2)
            self.assertEqual(result['protected_region_limit'], game.PROTECTED_REGION_LIMIT)
            fixture(path, lambda data: data['materials'].append({}))
            self.assertTrue(game.inspect_baked_game_copy(path)['supported'])

    def test_unsupported_features(self):
        changes = [lambda d: d.update(skins=[{}]), lambda d: d.update(animations=[{}]),
                   lambda d: d['nodes'][0].update(translation=[1, 0, 0]),
                   lambda d: d['materials'][0].update(alphaMode='BLEND'),
                   lambda d: d['materials'][0].update(occlusionTexture={'index': 0}),
                   lambda d: d['meshes'][0]['primitives'].append(d['meshes'][0]['primitives'][0].copy()),
                   lambda d: d['images'][0].update(uri='../private.png')]
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'source.glb'
            for change in changes:
                fixture(path, change)
                self.assertFalse(game.inspect_baked_game_copy(path)['supported'])

    def test_native_root_preserves_local_protection_and_bakes_in_world_space(self):
        def placed(data):
            data['nodes'].extend([{'children': [0]}, {'children': [1], 'scale': [2, 2, 2],
                                                      'translation': [3, .5, -1]}])
            data['scenes'][0]['nodes'] = [2]

        with TemporaryDirectory() as folder:
            path = Path(folder) / 'source.glb'
            fixture(path, placed, automatic_normal=True)
            original = path.read_bytes()
            report = game.inspect_baked_game_copy(path)
            self.assertTrue(report['supported'], report)
            _, local, faces, placement = game._source(path)
            np.testing.assert_allclose(local.min(0), [-1, 0, 0])
            np.testing.assert_allclose(local.max(0), [1, 2, 0])
            world = game.placed_vertices(local, placement)
            np.testing.assert_allclose(world.min(0), [1, .5, -1])
            np.testing.assert_allclose(world.max(0), [5, 4.5, -1])
            self.assertEqual(len(faces), 2)
            self.assertEqual(path.read_bytes(), original)

    def test_only_neutral_vertex_colors_can_pass_through(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'source.glb'
            fixture(path, color=[1, 1, 1, 1])
            self.assertTrue(game.inspect_baked_game_copy(path)['supported'])
            fixture(path, color=[0, 1, 1, 1])
            self.assertIn('neutral white vertex colour', game.inspect_baked_game_copy(path)['reason'])

    def test_region_weights_scale_and_translation(self):
        rng = np.random.default_rng(42)
        head = rng.uniform([-.06, .85, -.04], [.06, 1, .06], (300, 3))
        left = rng.uniform([-.5, .69, -.03], [-.4, .8, .03], (300, 3))
        right = left * [-1, 1, 1]
        body = rng.uniform([-.1, 0, -.05], [.1, .8, .05], (300, 3))
        vertices = np.concatenate([head, left, right, body])
        first, report = game._region_weights(vertices, 'humanoid')
        second, _ = game._region_weights(vertices * 2.5 + [4, -2, 3], 'humanoid')
        np.testing.assert_array_equal(first, second)
        self.assertGreater(report['face_vertices'], 10)
        self.assertGreater(report['hand_vertices'], 10)
        with self.assertRaises(ValueError):
            game._region_weights(body, 'humanoid')

    def test_protected_regions_add_soft_weights_and_keep_profile_priorities(self):
        vertices = np.array([[x, 0, 0] for x in (0, .25, .5, .75, 1., 2.)])
        base = np.array([40, 25, 12, 8, 4, 1], float)
        regions = [dict(center=[0, 0, 0], radius=1, strength=.75),
                   dict(center=[0, 0, 0], radius=1, strength=.5),
                   dict(center=[10, 0, 0], radius=1, strength=1),
                   dict(center=[2, 0, 0], radius=1, strength=0)]
        weights = base.copy()
        report = game._add_protected_regions(vertices, weights, regions, 'humanoid')
        np.testing.assert_allclose(weights, base + [33, 33, 33, 33 * .5 ** 1.5, 0, 0])
        self.assertEqual(report['affected_vertices'], 4)
        self.assertEqual([region['affected_vertices'] for region in report['regions']], [4, 4, 0, 0])
        translation = [2, -4, 1]
        transformed = [{**region, 'radius': region['radius'] * 2,
                        'center': (np.asarray(region['center']) * 2 + translation).tolist()} for region in regions]
        second = base.copy()
        game._add_protected_regions(vertices * 2 + translation, second, transformed, 'humanoid')
        np.testing.assert_array_equal(weights, second)

    def test_donor_never_overwritten_and_staging_cleans_on_failure(self):
        with TemporaryDirectory() as folder, patch.object(game, 'game_copy_dependencies', return_value={'ready': True}):
            path = Path(folder) / 'source.glb'
            fixture(path)
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                game.run_baked_game_copy(path, path, options={})
            def stop(stage, fraction):
                if fraction >= .12:
                    raise RuntimeError('Intentional QA cancellation before native reduction')
            with self.assertRaisesRegex(RuntimeError, 'Intentional QA'):
                game.run_baked_game_copy(path, Path(folder) / 'output.glb', options={}, progress=stop)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_clean_removes_degenerate_and_duplicate_faces_only(self):
        vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [2, 0, 0]], float)
        faces = np.array([[0, 1, 2], [2, 1, 0], [0, 1, 3], [0, 0, 2]])
        np.testing.assert_array_equal(game._clean(vertices, faces), [[0, 1, 2]])


if __name__ == '__main__':
    main()
