"""CPU projection contracts: preservation, visibility, overlap, pixels and cancellation."""
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from glb_data import read, accessor, image
from model_finishing_worker import run as run_finishing_worker
import texture_projection as projection
from api_contract import TextureProjection
from generation_view_projection import (digest, generation_reference_name, inspect_generation_views,
                                        project_generation_texture, PROVENANCE_FILE, save_generation_views)
from pixal3d_runtime import CAMERA_FOV, camera_matrix
from studio_workspace import read as read_record, write as write_record


def write_glb(path, doc, binary):
    binary += b'\0' * (-len(binary) % 4)
    doc['buffers'][0]['byteLength'] = len(binary)
    text = json.dumps(doc, separators=(',', ':')).encode()
    text += b' ' * (-len(text) % 4)
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 28 + len(text) + len(binary))
                     + struct.pack('<I4s', len(text), b'JSON') + text
                     + struct.pack('<I4s', len(binary), b'BIN\0') + binary)


def fixture(path, *, occluder=True, overlap=False, zero_normals=(), opposing_faces=False):
    vertices, normals, uv, faces = [], [], [], []

    def quad(x0, y0, x1, y1, z, rect, back=False):
        start = len(vertices)
        vertices.extend([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)])
        normals.extend([(0, 0, -1 if back else 1)] * 4)
        a, b, c, d = rect
        uv.extend([(a, d), (c, d), (c, b), (a, b)])
        triangles = [(0, 2, 1), (0, 3, 2)] if back else [(0, 1, 2), (0, 2, 3)]
        faces.extend([[start + i for i in triangle] for triangle in triangles])

    quad(-.4, -.4, .4, .4, 0, (.05, .05, .45, .45))
    quad(-.4, -.4, .4, .4, -.1, (.05, .05, .45, .45) if overlap else (.55, .05, .95, .45), True)
    if occluder:
        quad(-.08, -.4, .08, .4, .2, (.55, .55, .95, .95))
    if opposing_faces:
        start = len(vertices)
        vertices.extend([(-.15, -.15, -1), (.15, -.15, -1), (.15, .15, -1)])
        normals.extend([(0, 0, 1)] * 3)
        uv.extend([(.99, .99)] * 3)
        faces.extend([(start, start + 1, start + 2), (start, start + 2, start + 1)])
    for index in zero_normals:
        normals[index] = (0, 0, 0)
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
           'nodes': [{'mesh': 0}], 'meshes': [{'primitives': [{}]}], 'materials': [],
           'buffers': [{'byteLength': 0}], 'bufferViews': [], 'accessors': [],
           'images': [], 'textures': [], 'samplers': [{'wrapS': 33071, 'wrapT': 33071}]}
    blob = bytearray()

    def view(payload):
        blob.extend(b'\0' * (-len(blob) % 4))
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(blob), 'byteLength': len(payload)})
        blob.extend(payload)
        return len(doc['bufferViews']) - 1

    def acc(values, kind, component, dtype):
        values = np.asarray(values, dtype=dtype)
        entry = {'bufferView': view(values.tobytes()), 'componentType': component,
                 'count': len(values), 'type': kind}
        if kind == 'VEC3':
            entry.update(min=values.min(axis=0).tolist(), max=values.max(axis=0).tolist())
        doc['accessors'].append(entry)
        return len(doc['accessors']) - 1

    prim = doc['meshes'][0]['primitives'][0]
    prim.update(attributes={'POSITION': acc(vertices, 'VEC3', 5126, '<f4'),
                            'NORMAL': acc(normals, 'VEC3', 5126, '<f4'),
                            'TEXCOORD_0': acc(uv, 'VEC2', 5126, '<f4')},
                indices=acc(np.asarray(faces).ravel(), 'SCALAR', 5125, '<u4'), material=0, mode=4)
    atlas = np.zeros((256, 256, 4), np.uint8)
    atlas[..., 3] = np.arange(256, dtype=np.uint8)[None, :]
    for picture in (atlas, np.full((4, 4, 3), [0, 180, 60], np.uint8), np.full((4, 4, 3), [128, 128, 255], np.uint8)):
        buffer = io.BytesIO()
        Image.fromarray(picture).save(buffer, format='PNG')
        doc['images'].append({'bufferView': view(buffer.getvalue()), 'mimeType': 'image/png'})
        doc['textures'].append({'source': len(doc['images']) - 1, 'sampler': 0})
    doc['materials'].append({'name': 'Preserved material', 'alphaMode': 'OPAQUE',
                            'pbrMetallicRoughness': {'baseColorTexture': {'index': 0},
                                                   'metallicRoughnessTexture': {'index': 1},
                                                   'roughnessFactor': .7, 'metallicFactor': .3},
                            'normalTexture': {'index': 2, 'scale': .8}, 'extras': {'keep': True}})
    write_glb(path, doc, bytes(blob))


def options(**updates):
    result = {'camera': {'view': 'front', 'frame': [-.5, -.5, .5, .5]},
              'source_rect': [0, 0, 1, 1], 'target_rect': [0, 0, 1, 1],
              'mask_shape': 'rectangle', 'opacity': .5, 'feather': 0}
    result.update(updates)
    return result


def color(path):
    doc, blob = read(path)
    prim = doc['meshes'][0]['primitives'][0]
    index = doc['materials'][prim['material']]['pbrMetallicRoughness']['baseColorTexture']['index']
    return np.asarray(image(doc, blob, doc['textures'][index]['source']).convert('RGBA'))


class TextureProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='trellis-projection-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.glb'
        self.reference = self.root / 'reference.png'
        self.output = self.root / 'projected.glb'
        fixture(self.source)
        Image.new('RGBA', (128, 128), (255, 255, 255, 255)).save(self.reference)

    def tearDown(self):
        self.temp.cleanup()

    def run_projection(self, **kwargs):
        return projection.project_texture(self.source, self.reference, self.output, options=options(), **kwargs)

    def test_preserves_glb_geometry_materials_alpha_and_uses_linear_blending(self):
        original = self.source.read_bytes()
        before, binary = read(self.source)
        report = self.run_projection()
        after, result_binary = read(self.output)
        self.assertEqual(original, self.source.read_bytes())
        self.assertEqual(binary, result_binary[:len(binary)])
        for key in ('meshes', 'nodes', 'accessors', 'scenes', 'samplers'):
            self.assertEqual(before[key], after[key])
        expected = deepcopy(after['materials'])
        expected[0]['pbrMetallicRoughness']['baseColorTexture'] = before['materials'][0]['pbrMetallicRoughness']['baseColorTexture']
        self.assertEqual(expected, before['materials'])
        old, new = color(self.source), color(self.output)
        self.assertTrue(np.array_equal(old[..., 3], new[..., 3]))
        self.assertEqual(int(new[64, 38, 0]), 188)  # 50% white over black in linear RGB, not 128.
        self.assertEqual(report['triangles'], 6)
        self.assertTrue(report['geometry_binary_unchanged'])
        self.assertGreater(report['occluded_samples'], 0)

    def test_occluded_and_back_surface_texels_are_untouched(self):
        self.run_projection()
        new = color(self.output)
        self.assertEqual(int(new[64, 64, 0]), 0)  # The raised strip hides this panel location.
        self.assertFalse(np.any(new[15:110, 145:235, :3]))  # Separate back-face UV island.
        self.assertEqual(int(new[192, 192, 0]), 188)  # Visible strip itself receives the reference.

    def test_missing_vertex_directions_are_derived_without_modifying_the_glb(self):
        fixture(self.source, opposing_faces=True)
        self.run_projection()
        expected = color(self.output)
        self.output.unlink()
        fixture(self.source, zero_normals=(0, 4, 12, 13, 14), opposing_faces=True)
        original = self.source.read_bytes()
        before, binary = read(self.source)

        self.assertTrue(projection.inspect_texture_projection(self.source)['supported'])
        self.run_projection()

        after, saved = read(self.output)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(saved[:len(binary)], binary)
        self.assertEqual(after['meshes'], before['meshes'])
        self.assertEqual(after['accessors'], before['accessors'])
        np.testing.assert_array_equal(color(self.output), expected)

    def test_saved_generation_projection_preserves_non_base_data_and_verifies_the_staged_references(self):
        model = self.root / 'model.glb'
        model.write_bytes(self.source.read_bytes())
        views = []
        for role, azimuth, shade in (('front', 0, 'red'), ('back', 180, 'blue')):
            reference = self.root / generation_reference_name(role, staged=True)
            Image.new('RGBA', (1024, 1024), shade).save(reference)
            views.append(dict(role=role, azimuth=azimuth, sha256=digest(reference), camera_angle_x=CAMERA_FOV,
                              transform_matrix=camera_matrix(azimuth)))
        save_generation_views(self.root, dict(scale=1, translation=[0, 0, 0], coordinate_system='y_up'), views)
        provenance = read_record(self.root / PROVENANCE_FILE)
        original = digest(self.source)
        before, binary = read(self.source)
        report = project_generation_texture(self.root, self.source, self.output, provenance, progress=lambda *_: None)
        after, output_binary = read(self.output)
        self.assertEqual(original, digest(self.source))
        self.assertEqual(binary, output_binary[:len(binary)])
        for key in ('bufferViews', 'images', 'textures'):
            after[key].pop()
        after['buffers'] = before['buffers']
        after['materials'][0]['pbrMetallicRoughness']['baseColorTexture'] = before['materials'][0]['pbrMetallicRoughness']['baseColorTexture']
        self.assertEqual(before, after)
        self.assertTrue(np.array_equal(color(self.source)[..., 3], color(self.output)[..., 3]))
        self.assertGreater(report['projected_texels'], 0)
        self.assertEqual(report['method'], 'generation_views')
        self.assertEqual([view['role'] for view in report['views']], ['front', 'back'])

        Image.new('RGBA', (1024, 1024), 'white').save(reference)
        rejected = self.root / 'rejected.glb'
        with self.assertRaisesRegex(ValueError, 'generation reference has changed'):
            project_generation_texture(self.root, self.source, rejected, provenance, progress=lambda *_: None)
        self.assertFalse(rejected.exists())

    def test_single_generation_cutout_worker_preserves_hidden_surfaces_and_requires_the_staged_input(self):
        reference = self.root / 'projection-source.png'
        cutout = Image.new('RGBA', (128, 128))
        cutout.paste((255, 255, 255, 255), (16, 12, 112, 116))
        cutout.save(reference)
        provenance = dict(source_sha256=digest(self.source), reference_sha256=digest(reference))
        request = dict(operation='texture_projection', result={}, reference_file=reference.name,
                       options=options(), generation_reference=provenance)
        write_record(self.root / 'finishing-request.json', request)
        source_bytes = self.source.read_bytes()
        original, binary = read(self.source)
        run_finishing_worker(self.root)
        output = self.root / 'model.glb'
        result = read_record(self.root / 'status.json')
        self.assertEqual(result['state'], 'done')
        report = result['result']['texture_projection']
        self.assertEqual(report['method'], 'generation_reference')
        self.assertEqual(report['reference_sha256'], provenance['reference_sha256'])
        self.assertEqual(report['model_sha256'], digest(output))
        after, final_binary = read(output)
        self.assertEqual(binary, final_binary[:len(binary)])
        for key in ('meshes', 'nodes', 'accessors'):
            self.assertEqual(original[key], after[key])
        pixels = color(output)
        self.assertEqual(int(pixels[64, 38, 0]), 188)
        self.assertEqual(int(pixels[64, 64, 0]), 0)  # Hidden by the front strip.
        self.assertFalse(np.any(pixels[15:110, 145:235, :3]))  # Rear-facing island.

        self.source.write_bytes(source_bytes)
        Image.new('RGBA', (128, 128), 'red').save(reference)
        run_finishing_worker(self.root)
        status = read_record(self.root / 'status.json')
        self.assertEqual(status['state'], 'error')
        self.assertIn('changed before projection', status['error'])
        self.assertFalse(output.exists())

    def test_estimated_generation_view_is_rebound_to_texture_copy(self):
        model = self.root / 'model.glb'
        model.write_bytes(self.source.read_bytes())
        cutout = Image.new('RGBA', (896, 896))
        cutout.paste('#cc7766', (100, 100, 796, 796))
        prepared = self.root / generation_reference_name('front')
        staged = self.root / generation_reference_name('front', staged=True)
        cutout.save(prepared)
        staged.write_bytes(prepared.read_bytes())
        fov = math.radians(29)
        camera = camera_matrix(0)
        camera[1][3] = -.5 / math.tan(fov / 2)
        view = dict(role='front', azimuth=0, sha256=digest(prepared), camera_angle_x=fov, transform_matrix=camera)
        save_generation_views(self.root, dict(scale=1, translation=[0, 0, 0], coordinate_system='y_up'),
                              [view], camera_model='moge')
        provenance = read_record(self.root / PROVENANCE_FILE)
        request = dict(operation='texture_projection', result={}, generation_views=provenance,
                       retained_generation_views=provenance)
        write_record(self.root / 'finishing-request.json', request)
        run_finishing_worker(self.root)
        status = read_record(self.root / 'status.json')
        self.assertEqual(status['state'], 'done')
        report = status['result']['texture_projection']
        self.assertTrue(report['generation_views_retained'])
        self.assertGreater(report['projected_texels'], 0)
        capability, retained = inspect_generation_views(self.root, model, {'engine': 'pixal3d'}, True, '')
        self.assertEqual(capability, dict(supported=True, reason='', views=['front'], camera_model='moge'))
        self.assertEqual(retained['model_sha256'], digest(model))
        self.assertNotEqual(retained['model_sha256'], provenance['model_sha256'])
        self.assertEqual(retained['views'], provenance['views'])

    def test_two_views_preserve_geometry_and_paint_separate_front_and_rear_islands(self):
        rear = self.root / 'rear.png'
        Image.new('RGBA', (128, 128), (255, 0, 0, 255)).save(self.reference)
        Image.new('RGBA', (128, 128), (0, 0, 255, 255)).save(rear)
        source_doc, source_binary = read(self.source)
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        front_options = options(opacity=1)
        back_options = options(camera={'view': 'back', 'frame': [-.5, -.5, .5, .5]}, opacity=1)
        report = projection.project_texture_views(self.source, [(self.reference, front_options), (rear, back_options)], self.output)
        self.assertEqual(report['view_count'], 2)
        self.assertEqual(report['overlap_texels'], 0)
        self.assertEqual(report['projected_texels'], sum(view['projected_texels'] for view in report['views']))
        original, result = color(self.source), color(self.output)
        np.testing.assert_array_equal(result[64, 38, :3], [255, 0, 0])
        np.testing.assert_array_equal(result[64, 192, :3], [0, 0, 255])
        np.testing.assert_array_equal(result[..., 3], original[..., 3])
        source_after = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(source_after, source_hash)
        result_doc, result_binary = read(self.output)
        self.assertEqual(result_binary[:len(source_binary)], source_binary)
        for key in ('nodes', 'meshes', 'accessors'):
            self.assertEqual(source_doc[key], result_doc[key])
        result_doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture'] = source_doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture']
        self.assertEqual(source_doc['materials'], result_doc['materials'])
        self.assertEqual(len(result_doc['images']), len(source_doc['images']) + 1)

    def test_shared_view_uses_weighted_linear_fusion_without_stacking_opacity(self):
        other = self.root / 'other.png'
        Image.new('RGBA', (128, 128), (255, 0, 0, 255)).save(self.reference)
        Image.new('RGBA', (128, 128), (0, 0, 255, 255)).save(other)
        report = projection.project_texture_views(self.source, [(self.reference, options()), (other, options())], self.output)
        self.assertEqual(report['overlap_texels'], report['projected_texels'])
        self.assertEqual(report['blend_mode'], 'confidence_weighted')
        expected = projection._srgb(np.array([.5 * 1.33 / 2.33, 0, .5 / 2.33]))
        np.testing.assert_array_equal(color(self.output)[64, 38, :3], expected)

    def test_invalid_second_view_does_not_publish_a_partial_projection(self):
        transparent = self.root / 'transparent.png'
        Image.new('RGBA', (128, 128)).save(transparent)
        with self.assertRaisesRegex(projection.TextureProjectionError, r'View 2: No visible surface'):
            projection.project_texture_views(self.source, [(self.reference, options()), (transparent, options())], self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.projection-*')))

    def test_overlapping_uvs_cannot_repaint_hidden_reused_surfaces(self):
        fixture(self.source, occluder=False, overlap=True)
        with self.assertRaisesRegex(projection.TextureProjectionError, 'overlapping UV'):
            self.run_projection()
        self.assertFalse(self.output.exists())

    def test_cancel_during_work_and_output_collision_preserve_originals(self):
        original = self.source.read_bytes()
        cancel = [False]

        def progress(stage, fraction):
            self.assertGreaterEqual(fraction, 0)
            self.assertLessEqual(fraction, 1)
            if stage == 'Projecting reference colors':
                cancel[0] = True

        with self.assertRaises(projection.TextureProjectionCancelled):
            self.run_projection(progress=progress, cancelled=lambda: cancel[0])
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.projection-*')))
        self.output.write_bytes(b'preserve destination')
        with self.assertRaisesRegex(projection.TextureProjectionError, 'new file'):
            self.run_projection()
        self.assertEqual(self.output.read_bytes(), b'preserve destination')
        self.assertEqual(self.source.read_bytes(), original)
        with self.assertRaises(projection.TextureProjectionError):
            projection.project_texture(self.source, self.reference, self.source, options=options())

    def test_all_axis_bases_are_orthonormal_and_back_projection_works(self):
        for basis in projection.VIEWS.values():
            basis = np.asarray(basis)
            self.assertTrue(np.array_equal(basis @ basis.T, np.eye(3)))
            self.assertEqual(round(np.linalg.det(basis)), 1)
        report = projection.project_texture(self.source, self.reference, self.output,
                                            options=options(camera={'view': 'back', 'frame': [-.5, -.5, .5, .5]}))
        new = color(self.output)
        self.assertEqual(int(new[64, 192, 0]), 188)
        self.assertEqual(int(new[64, 64, 0]), 0)
        self.assertEqual(report['options']['camera']['view'], 'back')

    def test_oblique_perspective_samples_world_points_and_respects_occlusion(self):
        angle = np.deg2rad(35)
        right = np.array([np.cos(angle), 0, -np.sin(angle)])
        outward = np.array([np.sin(angle), 0, np.cos(angle)])
        origin = outward * 2
        camera = dict(view='custom', projection='perspective', frame=[-.26, -.26, .26, .26],
                      basis=[right.tolist(), [0, 1, 0], outward.tolist()], origin=origin.tolist())
        rows, columns = np.mgrid[:128, :128]
        Image.fromarray(np.stack((2 * columns, 2 * rows, np.full_like(rows, 200)), axis=-1).astype(np.uint8)).save(self.reference)
        recipe = options(camera=camera, opacity=1, linked_selection=True)
        public = TextureProjection.model_validate(dict(recipe, reference='conditioning'))
        payload = public.model_dump(mode='json', exclude={'reference', 'upload_id'}, exclude_none=True)
        original = self.source.read_bytes()
        report = projection.project_texture(self.source, self.reference, self.output, options=payload)
        new = color(self.output)
        np.testing.assert_array_equal(new[..., 3], color(self.source)[..., 3])
        self.assertEqual(self.source.read_bytes(), original)
        doc, binary = read(self.source)
        result, result_binary = read(self.output)
        self.assertEqual(result['accessors'], doc['accessors'])
        self.assertEqual(result_binary[:len(binary)], binary)
        self.assertEqual(report['options']['camera'], camera)
        self.assertGreater(report['occluded_samples'], 0)
        self.assertFalse(np.any(new[15:110, 145:235, :3]))
        self.assertFalse(np.any(new[74, 46, :3]))  # Visible from front, occluded at this camera angle.
        for row, column in [(74, 27), (38, 64), (74, 93)]:
            u, v = (np.array([column, row]) + .5) / 256
            world = np.array([2 * (u - .05) - .4, 2 * (.45 - v) - .4, 0.])
            ray = world - origin
            distance = -ray @ outward
            x = .5 + ray @ right / (distance * .52)
            y = .5 - ray[1] / (distance * .52)
            np.testing.assert_allclose(new[row, column, :3], [256 * x - 1, 256 * y - 1, 200], atol=1)
        preview = self.root / 'oblique.png'
        info = projection.render_projection_preview(self.output, preview, camera=camera, size=128)
        self.assertEqual(info['camera'], camera)
        with Image.open(preview) as image:
            pixels = np.asarray(image)
            np.testing.assert_allclose(pixels[42, 80], [160, 84, 200, 255], atol=3)

    def test_custom_camera_validation_and_camera_plane_rejection(self):
        basis = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        camera = dict(view='custom', frame=[-.5, -.5, .5, .5], basis=basis)
        self.assertEqual(projection.normalize_projection_options(options(camera=camera))['camera']['projection'], 'orthographic')
        invalid = [dict(camera, basis=None), dict(camera, basis=[[1, 0, 0], [0, 1, 0], [0, 0, -1]]),
                   dict(camera, basis=[[1, 0, 0], [1, 0, 0], [0, 0, 1]]), dict(camera, view='front'),
                   dict(camera, origin=[0, 0, 2]), dict(camera, projection='perspective'),
                   dict(camera, projection='perspective', origin=[0, 0, float('inf')])]
        for invalid_camera in invalid:
            with self.subTest(camera=invalid_camera), self.assertRaises(projection.TextureProjectionError):
                projection.normalize_projection_options(options(camera=invalid_camera))
        camera = dict(view='front', projection='perspective', frame=[-.5, -.5, .5, .5])
        with self.assertRaisesRegex(ValueError, 'camera origin'):
            TextureProjection.model_validate(dict(options(camera=camera), reference='prepared'))
        with self.assertRaisesRegex(projection.TextureProjectionError, 'entire model'):
            projection.project_texture(self.source, self.reference, self.output,
                                       options=options(camera=dict(camera, origin=[0, 0, .1])))
        self.assertFalse(self.output.exists())

    def test_normalized_options_landmarks_and_bad_values(self):
        good = options(source_points=[[.2, .2], [.8, .2], [.5, .5], [.5, .8]],
                       target_points=[[.2, .2], [.8, .2], [.5, .5], [.5, .8]])
        self.assertEqual(projection.normalize_projection_options(good)['source_points'], good['source_points'])
        bad = [options(camera={'view': [], 'frame': [-1, -1, 1, 1]}), options(opacity=float('nan')),
               options(camera={'view': 'front', 'frame': [-1, -1, float('inf'), 1]}),
               options(camera={'view': 'front', 'frame': [-1, -1, 1e100, 1]}),
               options(camera={'view': 'front', 'frame': [0, 0, 1e-8, 1]}),
               options(target_rect=[.3, .3, .2, .8]), options(feather=True), options(unknown=1),
               options(source_points=good['source_points']),
               options(source_points=good['source_points'], target_points=[[.2, .5], [.4, .5], [.6, .5], [.8, .5]]),
               options(source_points=good['source_points'][::-1], target_points=good['target_points'])]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                projection.normalize_projection_options(value)

    def test_capabilities_preview_and_unsupported_mesh(self):
        capability = projection.inspect_texture_projection(self.source)
        self.assertTrue(capability['supported'])
        preview = self.root / 'preview.png'
        info = projection.render_projection_preview(self.source, preview, size=128)
        self.assertEqual(info['camera']['view'], 'front')
        with Image.open(preview) as pixels:
            self.assertEqual(pixels.size, tuple(info['image_size']))
        before, binary = read(self.source)
        variants = [dict(animations=[{}]), dict(skins=[{}])]
        for updates in variants:
            doc = deepcopy(before)
            doc.update(updates)
            write_glb(self.source, doc, binary)
            self.assertFalse(projection.inspect_texture_projection(self.source)['supported'])
        doc = deepcopy(before)
        doc['nodes'][0]['translation'] = [0, 0, 0]
        write_glb(self.source, doc, binary)
        self.assertIn('transforms', projection.inspect_texture_projection(self.source)['reason'])

    def test_studio_placement_projects_in_world_coordinates_and_preserves_the_glb(self):
        original, binary = read(self.source)
        baseline = self.root / 'baseline.glb'
        projection.project_texture(self.source, self.reference, baseline, options=options())
        original['nodes'] += [{'children': [0]}, {'children': [1], 'translation': [2, 3, .5], 'scale': [2, 2, 2]}]
        original['scenes'][0]['nodes'] = [2]
        write_glb(self.source, original, binary)
        source_bytes = self.source.read_bytes()
        support = projection.inspect_texture_projection(self.source)
        self.assertTrue(support['supported'])
        np.testing.assert_allclose(support['bounds'], [[1.2, 2.2, .3], [2.8, 3.8, .9]])
        preview = self.root / 'placed.png'
        info = projection.render_projection_preview(self.source, preview, size=128)
        np.testing.assert_allclose(info['camera']['frame'], support['default_camera']['frame'])

        placed_camera = {'view': 'front', 'frame': [1, 2, 3, 4]}
        report = projection.project_texture(self.source, self.reference, self.output,
                                            options=options(camera=placed_camera))
        doc, after = read(self.output)
        self.assertTrue(report['geometry_binary_unchanged'])
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(doc['nodes'], original['nodes'])
        self.assertEqual(doc['scenes'], original['scenes'])
        self.assertEqual(after[:len(binary)], binary)
        np.testing.assert_array_equal(color(self.output), color(baseline))

    def test_placement_rejects_reflections_rotation_and_ambiguous_scenes(self):
        original, binary = read(self.source)
        original['nodes'].append({'children': [0], 'scale': [2, 2, 2]})
        original['scenes'][0]['nodes'] = [1]
        for update in ({'scale': [-2, -2, -2]}, {'scale': [2, 3, 2]}, {'rotation': [0, 0, 0, 1]},
                       {'children': [0, 0]}, {'children': [1]}):
            with self.subTest(update=update):
                doc = deepcopy(original)
                doc['nodes'][1].update(update)
                write_glb(self.source, doc, binary)
                self.assertFalse(projection.inspect_texture_projection(self.source)['supported'])
        original['scenes'].append({'nodes': [0]})
        write_glb(self.source, original, binary)
        self.assertFalse(projection.inspect_texture_projection(self.source)['supported'])

    def test_transparent_reference_does_not_paint_and_nonopaque_material_is_rejected(self):
        Image.new('RGBA', (128, 128), (255, 0, 0, 0)).save(self.reference)
        with self.assertRaisesRegex(projection.TextureProjectionError, 'No visible surface'):
            self.run_projection()
        self.assertFalse(self.output.exists())
        doc, blob = read(self.source)
        doc['materials'][0]['alphaMode'] = 'BLEND'
        write_glb(self.source, doc, blob)
        self.assertIn('opaque', projection.inspect_texture_projection(self.source)['reason'])

    def test_exif_reference_orientation_matches_a_physically_transposed_image(self):
        pixels = np.zeros((64, 128, 3), dtype=np.uint8)
        pixels[:, :64] = [240, 20, 40]
        pixels[:, 64:] = [20, 190, 240]
        metadata = Image.Exif()
        metadata[274] = 6  # Browser image orientation: 90 degrees clockwise.
        oriented = self.root / 'oriented.jpg'
        Image.fromarray(pixels).save(oriented, quality=95, exif=metadata)
        with Image.open(oriented) as decoded:
            physically_rotated = decoded.transpose(Image.Transpose.ROTATE_270)
            physically_rotated.info.clear()
            physically_rotated.save(self.reference)
        report = projection.project_texture(self.source, oriented, self.output, options=options())
        other = self.root / 'physical.glb'
        projection.project_texture(self.source, self.reference, other, options=options())
        self.assertEqual(report['reference_size'], [64, 128])
        self.assertTrue(np.array_equal(color(self.output), color(other)))

    def test_late_cancel_collision_and_source_revision_cannot_publish(self):
        original = self.source.read_bytes()
        cancelled = [False]

        def late_cancel(stage, fraction):
            if stage == 'Saving the texture copy':
                cancelled[0] = True

        with self.assertRaises(projection.TextureProjectionCancelled):
            self.run_projection(progress=late_cancel, cancelled=lambda: cancelled[0])
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.projection-*')))

        def create_competing_output(stage, fraction):
            if stage == 'Saving the texture copy':
                self.output.write_bytes(b'concurrent output must survive')

        with self.assertRaisesRegex(projection.TextureProjectionError, 'already exists'):
            self.run_projection(progress=create_competing_output)
        self.assertEqual(self.output.read_bytes(), b'concurrent output must survive')
        self.assertFalse(list(self.root.glob('.projection-*')))
        self.output.unlink()

        def edit_source(stage, fraction):
            if stage == 'Extending texture island borders':
                self.source.write_bytes(original + b'external edit')

        with self.assertRaisesRegex(projection.TextureProjectionError, 'source model changed'):
            self.run_projection(progress=edit_source)
        self.assertEqual(self.source.read_bytes(), original + b'external edit')
        self.assertFalse(self.output.exists())

    def test_concave_polygon_uses_source_domain_and_ignores_unrelated_target_guard(self):
        fixture(self.source, occluder=False)
        points = [[.1, .1], [.9, .1], [.9, .9], [.1, .9]]
        recipe = options(mask_shape='polygon', source_polygon=[
            [.2, .2], [.8, .2], [.8, .4], [.4, .4], [.4, .8], [.2, .8]],
            source_rect=[.2, .2, .8, .8], target_rect=[.45, .45, .55, .55],
            source_points=points, target_points=points)
        original = self.source.read_bytes()
        report = projection.project_texture(self.source, self.reference, self.output, options=recipe)
        pixels = color(self.output)
        self.assertEqual(int(pixels[38, 77, 0]), 188)  # Horizontal arm, outside declared target guard.
        self.assertEqual(int(pixels[77, 38, 0]), 188)  # Vertical arm.
        self.assertEqual(int(pixels[77, 77, 0]), 0)    # Concave cutout remains unchanged.
        self.assertEqual(int(pixels[17, 17, 0]), 0)    # Outside source outline.
        self.assertTrue(np.allclose(report['effective_target_rect'], [.2, .2, .8, .8]))
        self.assertEqual(self.source.read_bytes(), original)
        before, binary = read(self.source)
        after, changed = read(self.output)
        self.assertEqual(changed[:len(binary)], binary)
        self.assertEqual(after['meshes'], before['meshes'])
        self.assertTrue(np.array_equal(color(self.source)[..., 3], pixels[..., 3]))

    def test_polygon_validation_and_versioned_contract(self):
        triangle = [[.2, .2], [.8, .2], [.5, .8]]
        good = options(mask_shape='polygon', source_polygon=triangle + [triangle[0]], linked_selection=True)
        normalized = projection.normalize_projection_options(good)
        self.assertEqual(normalized['source_polygon'], triangle)
        public = TextureProjection.model_validate(dict(good, reference='conditioning'))
        payload = public.model_dump(mode='json', exclude={'reference', 'upload_id'}, exclude_none=True)
        self.assertEqual(projection.normalize_projection_options(payload), normalized)
        for polygon in [triangle[:2], [[.1, .1]] * 129, [[.1, .1], [.5, .5], [.8, .8]],
                        [[.1, .1], [.9, .9], [.1, .9], [.8, .1]],
                        [[.1, .1], [.9, .1], [.5, .1], [.9, .9], [.1, .9]],
                        [[.1, .1], [.9, .1], [.9, .9], [.1, .1], [.1, .9]],
                        [[float('inf'), .1], [.8, .2], [.5, .8]],
                        [[-.01, .1], [.8, .2], [.5, .8]], [[True, .1], [.8, .2], [.5, .8]]]:
            with self.subTest(polygon=polygon), self.assertRaises(ValueError):
                projection.normalize_projection_options(options(mask_shape='polygon', source_polygon=polygon))
        for bad in [options(source_polygon=triangle), options(mask_shape='polygon'), options(linked_selection=1)]:
            with self.subTest(options=bad), self.assertRaises(ValueError):
                projection.normalize_projection_options(bad)
        with self.assertRaises(ValueError):
            TextureProjection.model_validate(dict(options(mask_shape='polygon'), reference='conditioning'))

    def test_polygon_winding_and_feather_remain_strictly_inside_source_outline(self):
        polygon = [[.2, .2], [.8, .2], [.8, .8], [.2, .8]]
        recipe = projection.normalize_projection_options(options(
            mask_shape='polygon', source_polygon=polygon, feather=.1))
        points = np.array([[.19, .5], [.2, .5], [.23, .5], [.26, .5], [.5, .5], [.81, .5]])
        alpha = projection._source_alpha(points, recipe, [100, 100])
        self.assertTrue(np.allclose(alpha, [0, 0, .5, 1, 1, 0]))
        recipe['source_polygon'] = polygon[::-1]
        self.assertTrue(np.array_equal(alpha, projection._source_alpha(points, recipe, [100, 100])))
        recipe['feather'] = 0
        self.assertTrue(np.array_equal(projection._source_alpha(points, recipe, [100, 100]), [0, 1, 1, 1, 1, 0]))

    def test_linked_affine_rotates_color_and_selection_together(self):
        fixture(self.source, occluder=False)
        gradient = np.zeros((128, 128, 4), dtype=np.uint8)
        gradient[..., 0] = np.arange(128, dtype=np.uint8)[None, :] * 2
        gradient[..., 1] = 100
        gradient[..., 3] = 255
        Image.fromarray(gradient).save(self.reference)
        source = np.array([[.2, .2], [.8, .2], [.8, .8], [.2, .8]])
        radians = np.deg2rad(35)
        rotation = np.array([[np.cos(radians), -np.sin(radians)], [np.sin(radians), np.cos(radians)]])
        target = (source - .5) @ rotation + .5
        recipe = options(linked_selection=True, mask_shape='rectangle', source_rect=[.4, .25, .6, .75],
                         target_rect=[.49, .49, .51, .51], opacity=1,
                         source_points=source.tolist(), target_points=target.tolist())
        report = projection.project_texture(self.source, self.reference, self.output, options=recipe)
        pixels = color(self.output)
        # UV centres map to camera image coordinates 2 * UV on the front fixture panel.
        yy, xx = np.mgrid[14:114, 14:114]
        camera = np.stack(((xx + .5) / 128, (yy + .5) / 128), axis=-1)
        reference = (camera - .5) @ rotation.T + .5
        local = pixels[14:114, 14:114, :3]
        inner = ((reference > [.405, .255]) & (reference < [.595, .745])).all(axis=-1)
        outside = ((reference < [.395, .245]) | (reference > [.605, .755])).any(axis=-1)
        self.assertTrue(np.all(local[..., 1][inner] == 100))
        expected_red = reference[..., 0] * 256 - 1
        self.assertLess(float(np.abs(local[..., 0][inner] - expected_red[inner]).max()), 2)
        self.assertFalse(np.any(local[outside]))
        self.assertGreater(report['effective_target_rect'][2] - report['effective_target_rect'][0], .4)

    def test_small_linked_patch_accepts_surrounding_guides_and_conditioned_detail_fit(self):
        source = np.array([[.1, .1], [.9, .1], [.9, .9], [.1, .9]])
        noisy = source.copy()
        noisy[-1] += [.002, -.002]
        linked = options(linked_selection=True, source_rect=[.499, .499, .501, .501],
                         target_rect=[.499, .499, .501, .501], source_points=noisy.tolist(), target_points=source.tolist())
        projection.normalize_projection_options(linked)
        with self.assertRaises(ValueError):
            projection.normalize_projection_options(dict(linked, linked_selection=False))
        detail = (source - .5) * 1e-6 + .5
        small = options(linked_selection=True, source_points=detail.tolist(), target_points=detail.tolist())
        transform, residual = projection._alignment(projection.normalize_projection_options(small))
        self.assertTrue(np.allclose(transform, [[1, 0], [0, 1], [0, 0]], atol=1e-7))
        self.assertLess(float(np.abs(residual).max()), 1e-12)


if __name__ == '__main__':
    unittest.main()
