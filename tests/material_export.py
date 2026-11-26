"""Preview-to-export regression. Add --dcc to also exercise Blender and both FBX targets."""
import base64
import copy
import io
import json
from pathlib import Path
import runpy
import shutil
import sys
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generated_motion import append_access
from glb_data import image, read
from target_export import export_asset, prepare_material_glb, write_glb


def fixture(source):
    source.mkdir()
    path = source / 'model.glb'
    runpy.run_path(str(ROOT / 'tests/texture_projection.py'))['fixture'](path)
    doc, binary = read(path)
    binary = bytearray(binary)
    pixels = np.array([[[0, 255, 10, 0], [63, 0, 175, 96]],
                       [[211, 74, 255, 189], [255, 160, 0, 255]]], dtype=np.uint8)
    png = io.BytesIO()
    Image.fromarray(pixels).save(png, format='PNG')
    doc['images'][1] = {'uri': 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode()}
    material = doc['materials'][0]
    material['occlusionTexture'] = {'index': 1}
    material['pbrMetallicRoughness']['baseColorTexture']['index'] = 1
    doc['materials'].append(copy.deepcopy(material))
    doc['materials'][1]['pbrMetallicRoughness']['roughnessFactor'] = 0
    doc['materials'].append({'extensions': {'KHR_materials_unlit': {}}})
    doc['extensionsUsed'] = ['KHR_materials_unlit']
    mesh = doc['meshes'][0]
    attrs = mesh['primitives'][0]['attributes']
    count = doc['accessors'][attrs['POSITION']]['count']
    attrs['WEIGHTS_0'] = append_access(doc, binary, [[1, 0, 0, 0]] * count, 'VEC4')
    binary.extend(b'\0' * (-len(binary) % 4))
    doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': count * 4})
    binary.extend(bytes(count * 4))
    doc['accessors'].append({'componentType': 5121, 'type': 'VEC4', 'count': count,
                             'bufferView': len(doc['bufferViews']) - 1})
    attrs['JOINTS_0'] = len(doc['accessors']) - 1
    mesh['primitives'].extend([dict(mesh['primitives'][0], material=1), {'attributes': dict(attrs),
                                'indices': mesh['primitives'][0]['indices']}])
    doc['nodes'].append({'name': 'Joint'})
    joint = len(doc['nodes']) - 1
    doc['scenes'][0]['nodes'].append(joint)
    doc['skins'] = [{'joints': [joint]}]
    doc['nodes'][0]['skin'] = 0
    times = append_access(doc, binary, [0, 1], 'SCALAR')
    positions = append_access(doc, binary, [[0, 0, 0], [.25, 0, 0]], 'VEC3')
    doc['animations'] = [{'name': 'Original clip', 'samplers': [{'input': times, 'output': positions}],
                          'channels': [{'sampler': 0, 'target': {'node': joint, 'path': 'translation'}}]}]
    doc['buffers'][0]['byteLength'] = len(binary)
    write_glb(doc, binary, path)
    (source / 'settings.json').write_text('{"name":"Test","height_m":1}')
    (source / 'metadata.json').write_text('{}')
    (source / 'LODs').mkdir()
    shutil.copy2(path, source / 'LODs/LOD1.glb')
    (source / 'lod-manifest.json').write_text('{"levels":[{"level":0},{"level":1,"file":"LOD1.glb"}]}')


def effective(doc, binary, material):
    pbr = doc['materials'][material]['pbrMetallicRoughness']
    mr = doc['textures'][pbr['metallicRoughnessTexture']['index']]
    return (np.asarray(image(doc, binary, mr['source']))[:, :, 1:3] / 255
            * [pbr.get('roughnessFactor', 1), pbr.get('metallicFactor', 1)])


def check(work, dcc=False):
    source = work / 'source'
    fixture(source)
    path = source / 'model.glb'
    original = path.read_bytes()
    doc, binary = read(path)
    for neutral in (None, {'roughness': 0, 'metalness': 0}):
        prepare_material_glb(path, work / 'neutral.glb', neutral)
        assert (work / 'neutral.glb').read_bytes() == original
    for rough, metal in ((.65, -.8), (-.75, .45), (-1, -1), (1, 1), (0, .3)):
        output = work / 'adjusted.glb'
        prepare_material_glb(path, output, {'roughness': rough, 'metalness': metal})
        out, payload = read(output)
        for key in ('nodes', 'skins', 'animations', 'accessors', 'samplers', 'scenes'):
            assert out[key] == doc[key]
        assert payload.startswith(binary)
        for key in ('textures', 'bufferViews', 'images'):
            assert out[key][:len(doc[key])] == doc[key]
        del out['meshes'][0]['primitives'][2]['material']
        assert out['meshes'] == doc['meshes']
        assert out['materials'][2] == doc['materials'][2]
        assert out['materials'][3]['pbrMetallicRoughness'].get('roughnessFactor', 1) == 1 + min(0, rough)
        for i in (0, 1):
            delta = np.array([rough, metal])
            expected = effective(doc, binary, i) * (1 - abs(delta)) + np.maximum(delta, 0)
            np.testing.assert_allclose(effective(out, payload, i), expected, atol=.5 / 255)
            index = out['materials'][i]['pbrMetallicRoughness']['metallicRoughnessTexture']['index']
            packed = np.asarray(image(out, payload, out['textures'][index]['source']))
            original_packed = np.asarray(image(doc, binary, 1))
            np.testing.assert_array_equal(packed[:, :, (0, 3)], original_packed[:, :, (0, 3)])
            assert out['materials'][i]['occlusionTexture'] == doc['materials'][i]['occlusionTexture']
            assert out['materials'][i]['pbrMetallicRoughness']['baseColorTexture']['index'] == 1

    embedded = copy.deepcopy(doc)
    embedded['buffers'][0]['uri'] = 'data:application/octet-stream;base64,' + base64.b64encode(binary).decode()
    write_glb(embedded, b'', work / 'inline.glb')
    converted, payload = prepare_material_glb(work / 'inline.glb', work / 'inline-adjusted.glb', {'roughness': 1})
    assert 'uri' not in converted['buffers'][0]
    assert payload.startswith(binary)
    assert np.allclose(effective(converted, payload, 0)[:, :, 0], 1)

    adjustment = {'roughness': .5, 'metalness': -.5}
    for target in (('glb', 'blender', 'unity', 'unreal') if dcc else ('glb',)):
        bundle = work / target
        manifest = export_asset(source, bundle, target, material_adjustment=adjustment)
        assert manifest['material_adjustment'] == adjustment
        for directory in (bundle, bundle / 'LODs/LOD1'):
            exported, payload = read(directory / 'model.glb')
            mr = effective(exported, payload, 0)
            np.testing.assert_allclose(mr, effective(doc, binary, 0) * .5 + [.5, 0], atol=.5 / 255)
            if target == 'glb':
                continue
            report = json.loads((directory / 'conversion-report.json').read_text())
            assert report['bones'] == 1 and report['actions'] and report['triangles'] > 0
            assert (directory / ('model.blend' if target == 'blender' else 'model.fbx')).stat().st_size > 0
            maps = json.loads((directory / 'studio-export.json').read_text())['materials'][0]['maps']
            orm = np.array(Image.open(directory / maps['ORM']))
            packed = np.array(Image.open(directory / maps['MetallicSmoothness']))
            np.testing.assert_allclose(orm[:, :, 1:3], mr * 255, atol=1)
            assert (orm[:, :, 0] == np.array(image(doc, binary, 1))[:, :, 0]).all()
            assert (packed[:, :, 0] == orm[:, :, 2]).all()
            assert (packed[:, :, 3] == 255 - orm[:, :, 1]).all()
    assert path.read_bytes() == (source / 'LODs/LOD1.glb').read_bytes() == original


if __name__ == '__main__':
    with TemporaryDirectory(prefix='trellis-material-export-') as temporary:
        check(Path(temporary), dcc='--dcc' in sys.argv)
    print('Material export checks passed.')
