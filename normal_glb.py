"""Add a tangent-space bake to an existing Studio GLB without changing its surface."""
from copy import deepcopy
import io
import json
from pathlib import Path
import struct
from tempfile import NamedTemporaryFile

import cv2
import numpy as np
from PIL import Image

from studio_import import validate_glb


def unit(vectors):
    return vectors / np.maximum(np.linalg.norm(vectors, axis=-1, keepdims=True), 1e-8)


def perpendicular(normals):
    axes = np.eye(3, dtype=np.float32)[np.argmin(np.abs(normals), axis=1)]
    return unit(np.cross(normals, axes))


def vertex_tangents(normals, corners, loop_tangents):
    """Keep the MikkTSpace frame at each corner, splitting only conflicts."""
    flat = corners.ravel()
    if loop_tangents.shape != (len(flat), 4):
        raise ValueError('Normal bake tangents do not match the finished triangles.')
    valid_normals = np.isfinite(normals).all(axis=1) & (np.linalg.norm(normals, axis=1) > .1)
    basis_normals = normals.copy()
    basis_normals[~valid_normals] = (0, 0, 1)
    basis_normals = unit(basis_normals)
    corner_normals = basis_normals[flat]
    tangents = np.array(loop_tangents, dtype=np.float32, copy=True)
    finite = np.isfinite(tangents).all(axis=1)
    tangents[~finite] = 0
    axes = tangents[:, :3]
    axes -= corner_normals * np.einsum('ij,ij->i', corner_normals, axes)[:, None]
    invalid = ~finite | ~valid_normals[flat] | (np.linalg.norm(axes, axis=1) < .1) | (np.abs(tangents[:, 3]) != 1)
    axes[invalid] = perpendicular(corner_normals[invalid])
    axes[:] = unit(axes)
    signs = tangents[:, 3].reshape(-1, 3)
    signs[:] = np.where(signs < 0, -1, 1)
    mixed = (signs != signs[:, :1]).any(axis=1)
    signs[mixed] = signs[mixed, :1]
    neutral_faces = invalid.reshape(-1, 3).any(axis=1) | mixed

    count = len(normals)
    first = np.full(count, len(flat), dtype=np.int64)
    np.minimum.at(first, flat, np.arange(len(flat)))
    used = first < len(flat)
    result = np.empty((count, 4), dtype='<f4')
    result[used] = tangents[first[used]]
    result[~used, :3] = perpendicular(basis_normals[~used])
    result[~used, 3] = 1

    conflicts = np.flatnonzero(np.max(np.abs(tangents - result[flat]), axis=1) >= 1e-4)
    if not len(conflicts):
        return result, None, None, neutral_faces
    # Quantize the axes at the comparison tolerance so identical splits reuse an index.
    keys = np.empty((len(conflicts), 5), dtype=np.int32)
    keys[:, 0] = flat[conflicts]
    keys[:, 1:] = np.rint(tangents[conflicts] * 10_000)
    _, representative, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    new_vertices = conflicts[representative]
    copies = np.concatenate((np.arange(count), flat[new_vertices]))
    remapped = flat.astype('<u4', copy=True)
    remapped[conflicts] = count + inverse
    return np.concatenate((result, tangents[new_vertices])), remapped, copies, neutral_faces


def clean_normal_png(path, uv, corners, neutral_faces):
    """Fade unstable dense hits and remove isolated tessellation spikes."""
    with Image.open(path) as source:
        pixels = np.asarray(source.convert('RGB'), dtype=np.float32)
    height, width = pixels.shape[:2]
    vectors = pixels / 127.5 - 1
    length = np.linalg.norm(vectors, axis=2)
    valid = (length >= .95) & (length <= 1.05)
    vectors = unit(vectors)
    reverse = vectors[..., 2] < 0
    vectors[reverse] *= -1
    weight = np.clip((vectors[..., 2] - .24) / .48, 0, 1)
    weight = weight * weight * (3 - 2 * weight) * valid

    # Texture rows are in glTF order, so V=0 is the top row of the PNG.
    polygons = np.rint(uv[corners] * [16 * width, 16 * height] - 8).astype(np.int32)
    coverage = np.zeros((height, width), dtype=np.uint8)
    for polygon in polygons:
        cv2.fillConvexPoly(coverage, polygon, color=1, shift=4)
    covered = coverage.astype(bool)
    count = int(covered.sum())
    if not count:
        raise ValueError('Normal bake target has no covered UV texels.')
    neutral_mask = np.zeros_like(coverage)
    for polygon in polygons[neutral_faces]:
        cv2.fillConvexPoly(neutral_mask, polygon, color=1, shift=4)
    neutral_mask = neutral_mask.astype(bool)
    weight[neutral_mask] = 0
    neutral = np.array([0, 0, 1], dtype=np.float32)
    vectors = unit(neutral + (vectors - neutral) * weight[..., None])
    vectors = unit(cv2.bilateralFilter(vectors, d=5, sigmaColor=.14, sigmaSpace=1.1))
    # Do not allow the denoising pass to fill invalid tangent frames.
    vectors[neutral_mask] = neutral
    angles = np.rad2deg(np.arccos(np.clip(vectors[..., 2][covered], -1, 1)))
    p50, p90 = np.percentile(angles, [50, 90])
    rgb = np.clip(np.rint((vectors + 1) * 127.5), 0, 255).astype(np.uint8)
    png = io.BytesIO()
    Image.fromarray(rgb).save(png, format='PNG')
    stats = dict(width=width, height=height, convention='MikkTSpace OpenGL +Y',
                 coverage_percent=round(100 * count / covered.size, 2),
                 usable_percent=round(100 * np.count_nonzero(covered & (weight > 0)) / count, 2),
                 reverse_facing_percent=round(100 * np.count_nonzero(covered & reverse & valid) / count, 2),
                 tilt_degrees_p50=round(float(p50), 2), tilt_degrees_p90=round(float(p90), 2))
    return png.getbuffer(), stats


def embed_normal_map(destination, document, binary, primitive, attributes, corners,
                     loop_tangents, unfiltered_png):
    """Atomically replace a generated GLB with its normal-mapped equivalent."""
    destination = Path(destination)
    document, primitive = deepcopy((document, primitive))
    tangents, remapped, copies, neutral_faces = vertex_tangents(attributes['NORMAL'], corners, loop_tangents)
    png, stats = clean_normal_png(unfiltered_png, attributes['TEXCOORD_0'], corners, neutral_faces)
    blob = bytearray(binary)

    def append_view(data, target=None):
        blob.extend(b'\0' * (-len(blob) % 4))
        view = dict(buffer=0, byteOffset=len(blob), byteLength=len(data))
        if target is not None:
            view['target'] = target
        blob.extend(data)
        document['bufferViews'].append(view)
        return len(document['bufferViews']) - 1

    def append_accessor(entry, data, target, count, stride=None):
        view = append_view(data, target)
        if stride is not None:
            document['bufferViews'][view]['byteStride'] = stride
        document['accessors'].append(dict(entry, bufferView=view, count=count, byteOffset=0))
        return len(document['accessors']) - 1

    if copies is not None:
        if not np.array_equal(copies[remapped], corners.ravel()):
            raise ValueError('Normal bake changed the original triangle corners.')
        primitive['indices'] = append_accessor(
            dict(type='SCALAR', componentType=5125), remapped.tobytes(), 34963, len(remapped))
        component_sizes = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
        for attribute, index in list(primitive['attributes'].items()):
            if attribute == 'TANGENT':
                continue
            entry = document['accessors'][index]
            view = document['bufferViews'][entry['bufferView']]
            width = attributes[attribute].shape[1] * component_sizes[entry['componentType']]
            offset = view.get('byteOffset', 0) + entry.get('byteOffset', 0)
            rows = np.ndarray((entry['count'], width), dtype=np.uint8, buffer=binary,
                              offset=offset, strides=(view.get('byteStride', width), 1))
            # Copy the encoded attributes verbatim, including normalized integer data.
            stride = width + (-width % 4)
            expanded = np.zeros((len(copies), stride), dtype=np.uint8)
            expanded[:, :width] = rows[copies]
            primitive['attributes'][attribute] = append_accessor(
                entry, expanded.tobytes(), 34962, len(copies), stride if stride != width else None)
    primitive['attributes']['TANGENT'] = append_accessor(
        dict(type='VEC4', componentType=5126), tangents.tobytes(), 34962, len(tangents))
    images = document.setdefault('images', [])
    images.append(dict(bufferView=append_view(png), mimeType='image/png'))
    textures = document.setdefault('textures', [])
    textures.append(dict(source=len(images) - 1))
    document['materials'][primitive['material']]['normalTexture'] = dict(index=len(textures) - 1)

    blob.extend(b'\0' * (-len(blob) % 4))
    document['buffers'][0]['byteLength'] = len(blob)
    descriptor = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    descriptor += b' ' * (-len(descriptor) % 4)
    temporary = None
    try:
        with NamedTemporaryFile(dir=destination.parent, prefix='.normal-', suffix='.glb', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(struct.pack('<4sIIII', b'glTF', 2, 28 + len(descriptor) + len(blob),
                                     len(descriptor), 0x4E4F534A))
            stream.write(descriptor)
            stream.write(struct.pack('<II', len(blob), 0x004E4942))
            stream.write(blob)
        validate_glb(temporary)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return dict(stats, triangles=len(corners), tangent_vertex_splits=len(tangents) - len(attributes['POSITION']))
