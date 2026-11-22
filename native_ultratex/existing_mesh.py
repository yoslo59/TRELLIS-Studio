"""Bake UltraTex onto a verified copy of a finished native Studio mesh."""

from copy import deepcopy
import json
import struct

from glb_data import image, read, static_placement
from generation_view_projection import digest


ATLAS_SIZE = 2048


def model_space(document):
    """Remove Studio's uniform normalization roots from an isolated work copy."""
    result = deepcopy(document)
    meshes, nodes = result.get('meshes', []), result.get('nodes', [])
    if (len(meshes) != 1 or len(meshes[0].get('primitives', [])) != 1
            or [node.get('mesh') for node in nodes if 'mesh' in node] != [0]
            or result.get('skins') or result.get('animations')):
        raise ValueError('Native UltraTex needs one static mesh with a single surface material.')
    if len(result.get('buffers', [])) != 1 or result['buffers'][0].get('uri'):
        raise ValueError('Use a self-contained GLB with one embedded binary buffer.')
    primitive = meshes[0]['primitives'][0]
    if (primitive.get('mode', 4) != 4 or primitive.get('targets') or 'indices' not in primitive
            or not {'POSITION', 'TEXCOORD_0'} <= primitive.get('attributes', {}).keys()):
        raise ValueError('Native UltraTex needs indexed triangles and an existing UV0 atlas.')
    static_placement(result, allow_identity=True)
    for node in nodes:
        for key in ('matrix', 'rotation', 'scale', 'translation'):
            node.pop(key, None)
    return result, primitive


def inspect(source):
    try:
        document, binary = read(source)
        _, primitive = model_space(document)
        material = document['materials'][primitive['material']]
        color = material['pbrMetallicRoughness']['baseColorTexture']
        if color.get('texCoord', 0) != 0 or color.get('extensions'):
            raise ValueError('Choose a UV0 color atlas without a texture transform.')
        atlas = image(document, binary, document['textures'][color['index']]['source']).size
        count = document['accessors'][primitive['indices']]['count']
        if type(count) is not int or count < 3 or count % 3:
            raise ValueError('Native UltraTex needs a nonempty indexed triangle mesh.')
        return dict(supported=True, reason='', atlas=list(atlas), triangles=count // 3)
    except ValueError as error:
        return dict(supported=False, reason=str(error))
    except (OSError, KeyError, IndexError, TypeError, OverflowError):
        return dict(supported=False, reason='This model has no readable embedded color atlas for Native UltraTex.')


def _write_glb(path, document, binary):
    payload = json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    payload += b' ' * (-len(payload) % 4)
    with path.open('wb') as stream:
        stream.write(struct.pack('<4sIIII', b'glTF', 2, 28 + len(payload) + len(binary), len(payload), 0x4e4f534a))
        stream.write(payload)
        stream.write(struct.pack('<II', len(binary), 0x004e4942))
        stream.write(binary)


def bake_copy(source, reference, target, work, progress):
    from native_game_profile import texture_mesh
    from native_pixal3d import prepare_texture_mask

    source_hash, reference_hash = digest(source), digest(reference)
    original, binary = read(source)
    surface, primitive = model_space(original)
    geometry, textured = work / 'surface.glb', work / 'textured.glb'
    _write_glb(geometry, surface, binary)
    progress('Preparing the saved reference for Native UltraTex', .08)
    mask = prepare_texture_mask(reference, work / 'foreground.npy')
    progress('Baking Native UltraTex albedo and materials on the existing UVs', .17)
    stages = (('Verifying', .18), ('Loading', .2), ('Encoding', .28), ('Preparing albedo at 0', .34),
              ('Preparing albedo at 45', .48), ('Preparing mr', .65), ('Baking', .83))
    last_progress = .17

    def notify(label):
        nonlocal last_progress
        last_progress = max(last_progress, next((fraction for prefix, fraction in stages if label.startswith(prefix)), .17))
        progress(label, last_progress)

    receipt = texture_mesh(geometry, reference, mask, textured, work / 'ultratex', progress=notify)
    finished, finished_binary = read(textured)
    result_primitive = finished['meshes'][0]['primitives'][0]
    geometry_keys = ('attributes', 'indices', 'mode')
    if (not finished_binary.startswith(binary) or finished.get('accessors') != original.get('accessors')
            or finished['bufferViews'][:len(original['bufferViews'])] != original['bufferViews']
            or surface['scenes'] != finished['scenes'] or surface['nodes'] != finished['nodes']
            or any(primitive.get(key) != result_primitive.get(key) for key in geometry_keys)
            or receipt.get('output_sha256') != digest(textured) or receipt.get('atlas_size') != ATLAS_SIZE
            or digest(source) != source_hash or digest(reference) != reference_hash):
        raise ValueError('Native UltraTex could not verify the source geometry, UVs, or baked materials.')
    # Keep source texture sampler metadata, including any original normal map, intact.
    source_material = original['materials'][primitive['material']]
    result_material = finished['materials'][result_primitive['material']]
    if 'normalTexture' in source_material:
        result_material['normalTexture'] = source_material['normalTexture']
    finished['textures'][:len(original['textures'])] = original['textures']
    finished['nodes'] = original['nodes']
    _write_glb(target, finished, finished_binary)
    return {**receipt, 'state': 'applied', 'geometry_uv_equal': True, 'scene_equal': True,
            'source_sha256': source_hash, 'reference_sha256': reference_hash, 'output_sha256': digest(target),
            'atlas': [ATLAS_SIZE, ATLAS_SIZE], 'native_ultratex': True}
