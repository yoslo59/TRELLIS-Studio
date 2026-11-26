"""Sparse face-relief patches. Only the local Z coordinates and shading normals change."""
from pathlib import Path
import shutil
import struct
from tempfile import NamedTemporaryFile

import numpy as np

from glb_data import accessor, image, read, static_placement
from target_export import write_glb


def supported_surface(doc):
    meshes = doc.get('meshes', [])
    if (len(meshes) != 1 or len(meshes[0].get('primitives', [])) != 1
            or sum('mesh' in n for n in doc.get('nodes', [])) != 1
            or doc.get('skins') or doc.get('animations') or any('skin' in n for n in doc.get('nodes', []))):
        raise ValueError('Face refinement needs one static textured mesh. Choose the version before rigging.')
    static_placement(doc, allow_identity=True)
    prim = meshes[0]['primitives'][0]
    attrs = prim.get('attributes', {})
    if (prim.get('mode', 4) != 4 or prim.get('targets') or 'indices' not in prim
            or not {'POSITION', 'NORMAL', 'TEXCOORD_0'}.issubset(attrs)
            or 'JOINTS_0' in attrs or 'KHR_draco_mesh_compression' in prim.get('extensions', {})):
        raise ValueError('The character needs a static indexed mesh with normals and a UV texture.')
    if len(doc.get('buffers', [])) != 1 or 'uri' in doc['buffers'][0]:
        raise ValueError('Use a GLB with an embedded mesh.')
    for key in ('POSITION', 'NORMAL'):
        attr = doc['accessors'][attrs[key]]
        if attr['type'] != 'VEC3' or attr['componentType'] != 5126 or 'sparse' in attr:
            raise ValueError('The mesh needs floating-point positions and normals.')
    return prim


def build_patch(source_glb, head_obj, patch_path, *, detector_path=None):
    from face_refine_transfer import fit_relief

    doc, binary = read(source_glb)
    prim = supported_surface(doc)
    attrs = prim['attributes']
    vertices, normals, uv = (accessor(doc, binary, attrs[key]) for key in ('POSITION', 'NORMAL', 'TEXCOORD_0'))
    indices = accessor(doc, binary, prim['indices']).reshape(-1, 3)
    if (not len(vertices) or len(normals) != len(vertices) or uv.shape != (len(vertices), 2)
            or indices.dtype.kind not in ('u', 'i') or not len(indices) or len(indices) > 5_000_000
            or indices.min() < 0 or indices.max() >= len(vertices)
            or not all(np.isfinite(a).all() for a in (vertices, normals, uv))):
        raise ValueError('The model needs valid geometry and UV coordinates to refine its face.')
    try:
        base = doc['materials'][prim['material']]['pbrMetallicRoughness']['baseColorTexture']
        texture = doc['textures'][base['index']]
        if base.get('texCoord', 0) != 0 or base.get('extensions') or texture.get('extensions'):
            raise ValueError('Use a base-color image with standard UV coordinates.')
        atlas = np.asarray(image(doc, binary, texture['source']).convert('RGB'))
    except (KeyError, IndexError) as error:
        raise ValueError('The character needs an embedded base-color texture for face detection.') from error
    del binary
    if detector_path is None:
        from face_refine_engine import detector_path as locate_detector
        detector_path = locate_detector()
    rows, dz, shading, info = fit_relief(vertices, normals, uv, indices, atlas, head_obj, detector_path)
    # Layout is shared with the Three.js preview. The row index addresses the
    # original GLB accessor, including individual copies at UV seams.
    with Path(patch_path).open('wb') as out:
        out.write(struct.pack('<4sI', b'FRP1', len(rows)))
        for values, dtype in ((rows, '<u4'), (dz, '<f4'), (shading, '<f4')):
            np.asarray(values, dtype=dtype).tofile(out)
    return dict(info, vertex_count=len(vertices), count=len(rows))


def _load_patch(path, vertex_count):
    with Path(path).open('rb') as file:
        header = file.read(8)
        if len(header) != 8:
            raise ValueError('The face preview is incomplete.')
        magic, count = struct.unpack('<4sI', header)
        if magic != b'FRP1' or not 0 < count <= vertex_count or Path(path).stat().st_size != 8 + 20*count:
            raise ValueError('The face preview does not match the model.')
        rows = np.fromfile(file, dtype='<u4', count=count)
        dz = np.fromfile(file, dtype='<f4', count=count)
        normal_delta = np.fromfile(file, dtype='<f4', count=3*count).reshape(-1, 3)
    if (rows[-1] >= vertex_count or np.any(rows[1:] <= rows[:-1])
            or not np.isfinite(dz).all() or not np.isfinite(normal_delta).all()):
        raise ValueError('The face preview contains invalid vertex changes.')
    return rows, dz, normal_delta


def _writable_attribute(doc, binary, index):
    attribute = doc['accessors'][index]
    view = doc['bufferViews'][attribute['bufferView']]
    offset = view.get('byteOffset', 0) + attribute.get('byteOffset', 0)
    stride = view.get('byteStride', 12)
    return np.ndarray((attribute['count'], 3), dtype='<f4', buffer=binary, offset=offset, strides=(stride, 4))


def apply_patch(source_glb, patch_path, destination_glb, strength):
    source, destination = Path(source_glb), Path(destination_glb)
    if source.resolve() == destination.resolve():
        raise ValueError('Save the refined mesh as a separate copy.')
    if isinstance(strength, bool) or not isinstance(strength, (float, int)) or not 0 <= strength <= 1:
        raise ValueError('Face intensity must be between zero and one.')
    doc, binary = read(source)
    prim = supported_surface(doc)
    positions = prim['attributes']['POSITION']
    normals = prim['attributes']['NORMAL']
    count = doc['accessors'][positions]['count']
    rows, dz, normal_delta = _load_patch(patch_path, count)
    with NamedTemporaryFile(dir=destination.parent, prefix='face-', suffix='.part', delete=False) as file:
        temp = Path(file.name)
    try:
        if strength == 0:
            shutil.copyfile(source, temp)
        else:
            mutable = bytearray(binary)
            del binary
            xyz = _writable_attribute(doc, mutable, positions)
            shading = _writable_attribute(doc, mutable, normals)
            xyz[rows, 2] += dz * strength
            blended = shading[rows] + normal_delta * strength
            shading[rows] = blended / np.maximum(np.linalg.norm(blended, axis=1, keepdims=True), 1.e-10)
            for index, values in ((positions, xyz), (normals, shading)):
                attr = doc['accessors'][index]
                if 'min' in attr or index == positions:
                    attr['min'] = values.min(axis=0).tolist()
                if 'max' in attr or index == positions:
                    attr['max'] = values.max(axis=0).tolist()
            write_glb(doc, mutable, temp)
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)
    return dict(count=len(rows), vertex_count=count, strength=float(strength),
                triangles=doc['accessors'][prim['indices']]['count']//3)
