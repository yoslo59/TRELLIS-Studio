"""Explicit CPU reference-to-base-color projection for static GLBs.

Coordinates supplied by callers are normalized image coordinates (top-left origin).
An orthographic frame uses signed model-plane coordinates. A perspective frame
uses the plane one unit forward from the camera. The front view looks from +Z.
"""
from copy import deepcopy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
from tempfile import NamedTemporaryFile
import time

import cv2
import numpy as np
from PIL import Image, ImageOps
from scipy.ndimage import distance_transform_edt, map_coordinates

from glb_data import accessor, image, placed_vertices, read, static_placement


class TextureProjectionError(ValueError):
    """The requested projection cannot be performed safely."""


class TextureProjectionCancelled(RuntimeError):
    """The caller cancelled this operation before publication."""


VIEWS = {
    'front': ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    'back': ((-1, 0, 0), (0, 1, 0), (0, 0, -1)),
    'right': ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
    'left': ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
    'top': ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    'bottom': ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
}
MAX_ATLAS = 4096
MAX_REFERENCE_PIXELS = 16_777_216
MAX_TRIANGLES = 5_000_000
POLYGON_EPS = 1e-10
MIN_POLYGON_AREA = 1e-8
MIN_SAMPLE_ALPHA = 1 / 255
FUSION_EDGE_PIXELS = 24
PRIMARY_VIEW_WEIGHT = 1.33
LINEAR_RGB_MAX = np.iinfo(np.uint16).max


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TextureProjectionError(f'{label} must contain finite numbers.')
    return float(value)


def _rect(value, label, normalized=True):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise TextureProjectionError(f'{label} must contain four coordinates.')
    result = [_number(v, label) for v in value]
    if max(abs(v) for v in result) > 1e12:
        raise TextureProjectionError(f'{label} exceeds the supported coordinate range.')
    if result[2] - result[0] <= 1e-7 or result[3] - result[1] <= 1e-7:
        raise TextureProjectionError(f'{label} must have positive width and height.')
    if normalized and (min(result) < 0 or max(result) > 1):
        raise TextureProjectionError(f'{label} must be inside the image (0 to 1).')
    return result


def _camera(value):
    if not isinstance(value, dict) or set(value) - {'view', 'frame', 'projection', 'basis', 'origin'}:
        raise TextureProjectionError('Camera must specify a supported view and frame.')
    view = value.get('view', 'front')
    if not isinstance(view, str) or (view not in VIEWS and view != 'custom'):
        raise TextureProjectionError('Choose a preset or custom view.')
    projection = value.get('projection', 'orthographic')
    if projection not in ('orthographic', 'perspective'):
        raise TextureProjectionError('Choose orthographic or perspective projection.')
    result = {'view': view, 'frame': _rect(value.get('frame'), 'Camera frame', False)}
    if view == 'custom':
        basis = value.get('basis')
        if not isinstance(basis, (list, tuple)) or len(basis) != 3:
            raise TextureProjectionError('A custom camera needs three basis vectors.')
        basis = np.asarray([_camera_vector(axis, 'Camera basis', limit=1.000001) for axis in basis])
        if not np.allclose(basis @ basis.T, np.eye(3), rtol=0, atol=1e-6) or np.linalg.det(basis) <= 0:
            raise TextureProjectionError('The camera basis must be right-handed and orthonormal.')
        result['basis'] = basis.tolist()
        result['projection'] = projection
    elif 'basis' in value:
        raise TextureProjectionError('Supply a camera basis only for a custom view.')
    if projection == 'perspective':
        result['projection'] = projection
        result['origin'] = _camera_vector(value.get('origin'), 'Camera origin', limit=1e12)
    elif 'origin' in value:
        raise TextureProjectionError('Supply a camera origin only for perspective projection.')
    return result


def _camera_vector(value, label, *, limit):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise TextureProjectionError(f'{label} must contain three coordinates.')
    result = [_number(v, label) for v in value]
    if max(abs(v) for v in result) > limit:
        raise TextureProjectionError(f'{label} exceeds the supported coordinate range.')
    return result


def _points(value, rect, label):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise TextureProjectionError(f'{label} must contain four matching points.')
    result = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise TextureProjectionError(f'{label} must contain coordinate pairs.')
        x, y = (_number(v, label) for v in point)
        if not rect[0] <= x <= rect[2] or not rect[1] <= y <= rect[3]:
            raise TextureProjectionError(f'{label} must stay inside the selected rectangle.')
        result.append([x, y])
    return result


def _polygon(value):
    if not isinstance(value, (list, tuple)) or not 3 <= len(value) <= 128:
        raise TextureProjectionError('The reference polygon must contain 3 to 128 vertices.')
    result = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise TextureProjectionError('Polygon vertices must contain coordinate pairs.')
        pair = [_number(v, 'Reference polygon') for v in point]
        if min(pair) < 0 or max(pair) > 1:
            raise TextureProjectionError('The reference polygon must stay inside the image (0 to 1).')
        result.append(pair)
    if result[-1] == result[0]:
        result.pop()
    if len(result) < 3:
        raise TextureProjectionError('The reference polygon needs three distinct vertices.')
    p = np.asarray(result, dtype=float)
    following = np.roll(p, -1, axis=0)
    if np.any(np.linalg.norm(following - p, axis=1) <= POLYGON_EPS):
        raise TextureProjectionError('Remove duplicate adjacent vertices from the reference polygon.')
    area = .5 * np.sum(p[:, 0] * following[:, 1] - following[:, 0] * p[:, 1])
    if abs(area) <= MIN_POLYGON_AREA:
        raise TextureProjectionError('The reference polygon must enclose a usable area.')

    def cross(a, b, c):
        d, e = b - a, c - a
        return d[0] * e[1] - d[1] * e[0]

    def on_segment(a, b, c):
        return (abs(cross(a, b, c)) <= POLYGON_EPS
                and np.all(c >= np.minimum(a, b) - POLYGON_EPS)
                and np.all(c <= np.maximum(a, b) + POLYGON_EPS))

    for i, (a, b) in enumerate(zip(p, following)):
        for j in range(i + 1, len(p)):
            if j == i + 1 or (i == 0 and j == len(p) - 1):
                continue
            c, d = p[j], following[j]
            ab_c, ab_d, cd_a, cd_b = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
            crossing = ((ab_c > POLYGON_EPS and ab_d < -POLYGON_EPS)
                        or (ab_c < -POLYGON_EPS and ab_d > POLYGON_EPS))
            crossing &= ((cd_a > POLYGON_EPS and cd_b < -POLYGON_EPS)
                         or (cd_a < -POLYGON_EPS and cd_b > POLYGON_EPS))
            if crossing or any((on_segment(a, b, c), on_segment(a, b, d),
                                on_segment(c, d, a), on_segment(c, d, b))):
                raise TextureProjectionError('The reference polygon must not intersect or touch itself.')
    return result


def _linked(options):
    return options.get('linked_selection', False) or options['mask_shape'] == 'polygon'


def _alignment(options):
    source = np.asarray(options['source_rect'], dtype=float)
    target = np.asarray(options['target_rect'], dtype=float)
    if 'source_points' not in options:
        scale = (source[2:] - source[:2]) / (target[2:] - target[:2])
        return np.vstack((np.diag(scale), source[:2] - target[:2] * scale)), np.zeros((0, 2))
    a = np.asarray(options['target_points'], dtype=float)
    b = np.asarray(options['source_points'], dtype=float)
    linked = _linked(options)
    if linked:
        center_a, center_b = a.mean(axis=0), b.mean(axis=0)
        span_a, span_b = np.maximum(np.ptp(a, axis=0), 1e-8), np.maximum(np.ptp(b, axis=0), 1e-8)
        design = np.column_stack(((a - center_a) / span_a, np.ones(len(a))))
    else:
        design = np.column_stack((a, np.ones(len(a))))
    if np.linalg.matrix_rank(design) != 3 or np.linalg.cond(design) > 1e6:
        raise TextureProjectionError('Spread the target landmarks across the selected area.')
    if linked:
        fitted = np.linalg.lstsq(design, (b - center_b) / span_b, rcond=None)[0]
        linear = fitted[:2] * span_b[None, :] / span_a[:, None]
        transform = np.vstack((linear, center_b + fitted[2] * span_b - center_a @ linear))
    else:
        transform = np.linalg.lstsq(design, b, rcond=None)[0]
    if np.linalg.det(transform[:2]) <= 1e-10 or np.linalg.cond(transform[:2]) > 50:
        raise TextureProjectionError('Landmarks must not mirror or collapse the reference.')
    residual = np.column_stack((a, np.ones(len(a)))) @ transform - b
    relative = residual / (span_b if linked else source[2:] - source[:2])
    if np.max(np.linalg.norm(relative, axis=1)) > .12:
        raise TextureProjectionError('The landmarks do not fit one affine projection. Adjust them or use rectangles.')
    return transform, residual


def normalize_projection_options(options):
    """Validate a plain projection dictionary; reject unknown fields and ambiguous mappings."""
    allowed = {'camera', 'source_rect', 'target_rect', 'source_points', 'target_points',
               'mask_shape', 'opacity', 'feather', 'source_polygon', 'linked_selection'}
    if not isinstance(options, dict) or set(options) - allowed:
        raise TextureProjectionError('Unknown texture projection options.')
    result = {'camera': _camera(options.get('camera')),
              'source_rect': _rect(options.get('source_rect'), 'Reference rectangle'),
              'target_rect': _rect(options.get('target_rect'), 'Model rectangle'),
              'mask_shape': options.get('mask_shape', 'ellipse'),
              'linked_selection': options.get('linked_selection', False),
              'opacity': _number(options.get('opacity', 1), 'Opacity'),
              'feather': _number(options.get('feather', .06), 'Feather')}
    if result['mask_shape'] not in ('ellipse', 'rectangle', 'polygon'):
        raise TextureProjectionError('Mask shape must be ellipse, rectangle, or polygon.')
    if type(result['linked_selection']) is not bool:
        raise TextureProjectionError('Linked selection must be a boolean.')
    if result['mask_shape'] == 'polygon':
        result['source_polygon'] = _polygon(options.get('source_polygon'))
    elif 'source_polygon' in options:
        raise TextureProjectionError('Select the polygon mask shape when supplying a reference polygon.')
    if not 0 < result['opacity'] <= 1 or not 0 <= result['feather'] <= .5:
        raise TextureProjectionError('Opacity must be above 0 and at most 1; feather must be between 0 and 0.5.')
    if ('source_points' in options) != ('target_points' in options):
        raise TextureProjectionError('Provide the four reference and model landmarks together.')
    for key, rect in (('source_points', 'source_rect'), ('target_points', 'target_rect')):
        if key in options:
            limits = [0, 0, 1, 1] if _linked(result) else result[rect]
            result[key] = _points(options[key], limits, key.replace('_', ' ').capitalize())
    _alignment(result)
    return result


def _projection_normals(vertices, normals, indices):
    """Fill zero vectors for projection weights without rewriting the GLB."""
    missing = np.linalg.norm(normals, axis=1) < 1e-8
    if not missing.any():
        return normals
    faces = indices[missing[indices].any(axis=1)]
    if len(faces) == 0:
        return normals
    corners = vertices[faces].astype(np.float64)
    face_normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    areas = np.linalg.norm(face_normals, axis=1)
    estimates = np.zeros(vertices.shape, dtype=np.float64)
    for corner in faces.T:
        np.add.at(estimates, corner, face_normals)
    lengths = np.linalg.norm(estimates, axis=1)
    unresolved = missing & (lengths == 0)
    if unresolved.any():
        # Coincident faces with opposite winding can cancel exactly.
        order = np.argsort(-areas, kind='stable')
        order = order[areas[order] > 0]
        best = np.full(len(vertices), len(order), dtype=np.int64)
        ranks = np.arange(len(order))
        for corner in faces[order].T:
            np.minimum.at(best, corner, ranks)
        fallback = unresolved & (best < len(order))
        face = order[best[fallback]]
        estimates[fallback] = face_normals[face]
        lengths[fallback] = areas[face]
    repairable = missing & (lengths > 0)
    complete = normals.copy()
    complete[repairable] = estimates[repairable] / lengths[repairable, None]
    return complete


def _load_mesh(path):
    try:
        document, binary = read(path)
        meshes, nodes = document.get('meshes', []), document.get('nodes', [])
        if (len(meshes) != 1 or len(meshes[0].get('primitives', [])) != 1
                or sum('mesh' in node for node in nodes) != 1
                or any(node.get('mesh', 0) != 0 for node in nodes if 'mesh' in node)
                or document.get('animations') or document.get('skins')):
            raise TextureProjectionError('Use a single static mesh. Rigged or animated models are not supported yet.')
        if len(document.get('buffers', [])) != 1 or document['buffers'][0].get('uri'):
            raise TextureProjectionError('Export a self-contained GLB with one embedded binary buffer.')
        if any('bufferView' not in entry for entry in document.get('images', [])):
            raise TextureProjectionError('Embed all textures in the GLB before projecting.')
        primitive = meshes[0]['primitives'][0]
        if primitive.get('mode', 4) != 4 or primitive.get('targets') or 'indices' not in primitive:
            raise TextureProjectionError('Export an indexed triangular mesh without morph targets.')
        attrs = primitive.get('attributes', {})
        if not all(key in attrs for key in ('POSITION', 'NORMAL', 'TEXCOORD_0')):
            raise TextureProjectionError('The mesh needs positions, vertex normals, and an unwrapped UV0 map.')
        vertices = accessor(document, binary, attrs['POSITION'])
        normals = accessor(document, binary, attrs['NORMAL'])
        uv = accessor(document, binary, attrs['TEXCOORD_0'])
        raw_indices = accessor(document, binary, primitive['indices']).ravel()
        if raw_indices.dtype.kind not in ('i', 'u'):
            raise TextureProjectionError('Triangle indices must be stored as integers.')
        if len(raw_indices) % 3 or not 0 < len(raw_indices) <= 3 * MAX_TRIANGLES:
            raise TextureProjectionError('Projection supports nonempty meshes with at most five million triangles.')
        indices = raw_indices.reshape(-1, 3).astype(np.int64)
        if (vertices.ndim != 2 or vertices.shape[1] != 3 or normals.shape != vertices.shape
                or uv.shape != (len(vertices), 2) or not len(vertices)
                or indices.min() < 0 or indices.max() >= len(vertices)
                or not all(np.isfinite(a).all() for a in (vertices, normals, uv))):
            raise TextureProjectionError('Repair invalid geometry, normals, or UV coordinates before projecting.')
        if uv.min() < -1e-6 or uv.max() > 1 + 1e-6:
            raise TextureProjectionError('Use a non-repeating UV atlas inside the 0 to 1 range.')
        try:
            vertices = placed_vertices(vertices, static_placement(document))
        except ValueError as error:
            raise TextureProjectionError(str(error)) from error
        material_index = primitive.get('material')
        material = document['materials'][material_index]
        if material.get('alphaMode', 'OPAQUE') != 'OPAQUE':
            raise TextureProjectionError('Projection currently supports opaque materials only.')
        pbr = material['pbrMetallicRoughness']
        factor = np.asarray(pbr.get('baseColorFactor', [1, 1, 1, 1]), dtype=float)
        if factor.shape != (4,) or not np.isfinite(factor).all() or not np.allclose(factor[:3], 1, atol=1e-6):
            raise TextureProjectionError('Apply the base-color tint to its texture before projecting.')
        color = pbr['baseColorTexture']
        if color.get('texCoord', 0) != 0 or color.get('extensions'):
            raise TextureProjectionError('Use UV0 without a texture transform for the base-color image.')
        texture = document['textures'][color['index']]
        if texture.get('extensions'):
            raise TextureProjectionError('Use an embedded PNG or JPEG base-color texture.')
        atlas_image = image(document, binary, texture['source'])
        if max(atlas_image.size) > MAX_ATLAS or min(atlas_image.size) < 1:
            raise TextureProjectionError('Use a base-color atlas no larger than 4096 by 4096 for this operation.')
        atlas = np.asarray(atlas_image.convert('RGBA')).copy()
        normals = _projection_normals(vertices, normals, indices)
        return document, binary, primitive, material_index, atlas, vertices, normals, uv, indices
    except TextureProjectionError:
        raise
    except (OSError, ValueError, KeyError, IndexError, TypeError, OverflowError, Image.DecompressionBombError) as error:
        raise TextureProjectionError('The static GLB or its embedded texture could not be read. Re-export it from Studio.') from error


def _default_camera(vertices, view):
    projected = vertices @ np.asarray(VIEWS[view], dtype=float).T
    low, high = projected[:, :2].min(axis=0), projected[:, :2].max(axis=0)
    center = (low + high) / 2
    extent = float(max(high - low)) * 1.04
    if extent < 1e-8:
        raise TextureProjectionError('The selected view has no usable surface extent.')
    return {'view': view, 'frame': [*(center - extent / 2), *(center + extent / 2)]}


def _camera_plane(vertices, camera):
    basis = np.asarray(camera['basis'] if camera['view'] == 'custom' else VIEWS[camera['view']], dtype=float)
    perspective = camera.get('projection') == 'perspective'
    if perspective:
        plane = (vertices - np.asarray(camera['origin'])) @ basis.T
        minimum_distance = max(1e-9, float(np.ptp(vertices, axis=0).max()) * 1e-6)
        if np.any(-plane[:, 2] <= minimum_distance):
            raise TextureProjectionError('Move the perspective camera back so the entire model is in front of it.')
    else:
        plane = vertices @ basis.T
    return plane, basis, perspective


def _project_camera_plane(plane, perspective):
    if not perspective:
        return plane
    inverse_distance = -1 / plane[..., 2:3]
    # Reciprocal distance increases toward the camera and is affine in screen space.
    return np.concatenate((plane[..., :2] * inverse_distance, inverse_distance), axis=-1)


def inspect_texture_projection(model_path):
    """Return capability metadata without rasterization or publication."""
    try:
        doc, blob, primitive, material, atlas, vertices, normals, uv, indices = _load_mesh(Path(model_path))
        return {'supported': True, 'reason': '', 'triangles': len(indices),
                'atlas': [atlas.shape[1], atlas.shape[0]], 'views': list(VIEWS),
                'bounds': [vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()],
                'default_camera': _default_camera(vertices, 'front')}
    except TextureProjectionError as error:
        return {'supported': False, 'reason': str(error)}


def _raster(points, width, height):
    origin = points[0]
    a, b = points[1] - origin, points[2] - origin
    det = a[0] * b[1] - a[1] * b[0]
    if abs(det) < 1e-12:
        return
    lower = np.clip(np.ceil(points.min(axis=0) - 1e-7), 0, (width, height)).astype(np.int64)
    upper = np.clip(np.floor(points.max(axis=0) + 1e-7) + 1, 0, (width, height)).astype(np.int64)
    x0, y0 = lower
    x1, y1 = upper
    if x1 <= x0 or y1 <= y0:
        return
    for row in range(y0, y1, 64):
        yy, xx = np.mgrid[row:min(row + 64, y1), x0:x1]
        dx, dy = xx - origin[0], yy - origin[1]
        u = (dx * b[1] - dy * b[0]) / det
        v = (a[0] * dy - a[1] * dx) / det
        inside = (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7)
        yield (slice(row, min(row + 64, y1)), slice(x0, x1)), u, v, inside


def _interp(values, u, v):
    return values[0] + u[..., None] * (values[1] - values[0]) + v[..., None] * (values[2] - values[0])


def _smooth(low, high, values):
    t = np.clip((values - low) / max(high - low, 1e-12), 0, 1)
    return t * t * (3 - 2 * t)


def _selection_bounds(options):
    if options['mask_shape'] == 'polygon':
        points = np.asarray(options['source_polygon'], dtype=float)
        return np.concatenate((points.min(axis=0), points.max(axis=0)))
    return np.asarray(options['source_rect'], dtype=float)


def _processing_rect(options, transform):
    """Derive a conservative camera-image guard without changing the source mask."""
    if not _linked(options):
        return np.asarray(options['target_rect'], dtype=float)
    x0, y0, x1, y1 = _selection_bounds(options)
    corners = np.asarray(((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
    projected = (corners - transform[2]) @ np.linalg.inv(transform[:2])
    low, high = np.maximum(projected.min(axis=0), 0), np.minimum(projected.max(axis=0), 1)
    if np.any(high - low <= 1e-7):
        raise TextureProjectionError('The linked reference selection falls outside the model image. Move it into the frame.')
    return np.concatenate((low, high))


def _source_alpha(sample, options, image_size):
    """Evaluate the linked mask in source coordinates, with strictly inward feather."""
    bounds = _selection_bounds(options)
    selected = ((sample >= bounds[:2]) & (sample <= bounds[2:])).all(axis=-1)
    alpha = np.zeros(sample.shape[:-1], dtype=float)
    if not selected.any():
        return alpha
    size = np.asarray(image_size, dtype=float)
    span = (bounds[2:] - bounds[:2]) * size
    feather = options['feather'] * float(span.min())
    points = sample[selected] * size
    if options['mask_shape'] == 'polygon':
        polygon = np.asarray(options['source_polygon'], dtype=float) * size
        inside = np.zeros(len(points), dtype=bool)
        distance_squared = np.full(len(points), np.inf)
        for a, b in zip(polygon, np.roll(polygon, -1, axis=0)):
            edge = b - a
            t = np.clip(((points - a) @ edge) / (edge @ edge), 0, 1)
            distance_squared = np.minimum(distance_squared, ((points - a - t[:, None] * edge) ** 2).sum(axis=1))
            if b[1] != a[1]:
                crossings = ((a[1] > points[:, 1]) != (b[1] > points[:, 1]))
                crossing_x = a[0] + (points[:, 1] - a[1]) * edge[0] / edge[1]
                inside ^= crossings & (points[:, 0] < crossing_x)
        distance = np.sqrt(distance_squared)
        inside |= distance <= 1e-8
    elif options['mask_shape'] == 'rectangle':
        distance = np.minimum(points - bounds[:2] * size, bounds[2:] * size - points).min(axis=1)
        inside = distance >= 0
    else:
        local = (sample[selected] - bounds[:2]) / (bounds[2:] - bounds[:2])
        distance = (1 - np.linalg.norm((local - .5) * 2, axis=1)) * float(span.min()) / 2
        inside = distance >= 0
    values = _smooth(0, feather, distance) if feather else inside.astype(float)
    alpha[selected] = values * inside
    return alpha


def _linear(values):
    values = values / 255.
    return np.where(values <= .04045, values / 12.92, ((values + .055) / 1.055) ** 2.4)


def _srgb(values):
    return np.rint(np.clip(np.where(values <= .0031308, values * 12.92,
                                   1.055 * np.maximum(values, 0) ** (1 / 2.4) - .055), 0, 1) * 255).astype(np.uint8)


def _check(cancelled):
    if cancelled is not None and cancelled():
        raise TextureProjectionCancelled('Texture projection cancelled.')


def _progress(callback, stage, fraction):
    if callback is not None:
        callback(stage, float(fraction))


def _image_coordinates(plane, bounds):
    low = np.asarray(bounds[:2])
    high = np.asarray(bounds[2:])
    return np.stack(((plane[..., 0] - low[0]) / (high[0] - low[0]),
                     (high[1] - plane[..., 1]) / (high[1] - low[1])), axis=-1)


def _screen(plane, bounds, width, height):
    return _image_coordinates(plane, bounds) * [width, height] - .5


def _depth(plane, indices, bounds, size, cancelled=None, with_uv=None, *, perspective=False):
    spans = np.asarray(bounds[2:]) - bounds[:2]
    width, height = np.maximum(16, np.rint(size * spans / spans.max()).astype(int))
    screen = _screen(plane, bounds, width, height)
    projected = screen[indices]
    eligible = (projected.max(axis=1) >= 0).all(axis=1) & (projected.min(axis=1) < (width, height)).all(axis=1)
    depth = np.full((height, width), -np.inf, dtype=np.float64 if perspective else np.float32)
    texcoords = np.zeros((height, width, 2), dtype=np.float32) if with_uv is not None else None
    for count, index in enumerate(np.flatnonzero(eligible)):
        if count % 128 == 0:
            _check(cancelled)
        triangle = indices[index]
        values = plane[triangle, 2:3]
        coordinates = with_uv[triangle] if with_uv is not None else None
        if coordinates is not None and perspective:
            coordinates = coordinates * values
        for region, u, v, mask in _raster(screen[triangle], width, height):
            z = _interp(values, u, v)[..., 0]
            good = mask & (z > depth[region])
            depth[region][good] = z[good]
            if texcoords is not None and good.any():
                interpolated = _interp(coordinates, u[good], v[good])
                if perspective:
                    interpolated /= z[good, None]
                texcoords[region][good] = interpolated
    return depth, texcoords


def render_projection_preview(model_path, output_png, *, view='front', frame=None, camera=None, size=1024):
    """Write an unlit preview with explicit reusable camera coordinates."""
    if type(size) is not int or not 64 <= size <= 1024:
        raise TextureProjectionError('Preview size must be 64 to 1024.')
    if camera is not None:
        if view != 'front' or frame is not None:
            raise TextureProjectionError('Choose a camera or a preset view and frame.')
        camera = _camera(camera)
    elif not isinstance(view, str) or view not in VIEWS:
        raise TextureProjectionError('Choose a preset camera view or supply a camera.')
    mesh = _load_mesh(Path(model_path))
    doc, blob, primitive, material, atlas, vertices, normals, uv, indices = mesh
    if camera is None:
        camera = _camera({'view': view, 'frame': frame}) if frame is not None else _default_camera(vertices, view)
    plane, _, perspective = _camera_plane(vertices, camera)
    projected = _project_camera_plane(plane, perspective)
    depth, coordinates = _depth(projected, indices, camera['frame'], size, with_uv=uv, perspective=perspective)
    picture = cv2.remap(atlas, coordinates[..., 0] * atlas.shape[1] - .5,
                        coordinates[..., 1] * atlas.shape[0] - .5, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE)
    picture[..., 3] = np.where(np.isfinite(depth), 255, 0)
    destination = Path(output_png)
    if destination.exists() or destination.resolve() == Path(model_path).resolve():
        raise TextureProjectionError('Choose a new file for the projection preview.')
    with destination.open('xb') as stream:
        Image.fromarray(picture).save(stream, format='PNG')
    return {'camera': camera, 'image_size': [picture.shape[1], picture.shape[0]],
            'triangles': len(indices), 'atlas': [atlas.shape[1], atlas.shape[0]]}


def _publish(destination, document, binary, material_index, picture, cancelled):
    doc = deepcopy(document)
    with io.BytesIO() as stream:
        Image.fromarray(picture).save(stream, format='PNG')
        payload = stream.getvalue()
    blob = bytearray(binary)
    blob.extend(b'\0' * (-len(blob) % 4))
    doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(blob), 'byteLength': len(payload)})
    doc['images'].append({'bufferView': len(doc['bufferViews']) - 1, 'mimeType': 'image/png'})
    base = doc['materials'][material_index]['pbrMetallicRoughness']['baseColorTexture']
    texture = deepcopy(doc['textures'][base['index']])
    texture['source'] = len(doc['images']) - 1
    doc['textures'].append(texture)
    base['index'] = len(doc['textures']) - 1
    blob.extend(payload)
    blob.extend(b'\0' * (-len(blob) % 4))
    doc['buffers'][0]['byteLength'] = len(blob)
    descriptor = json.dumps(doc, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    descriptor += b' ' * (-len(descriptor) % 4)
    temporary = None
    try:
        with NamedTemporaryFile(dir=destination.parent, prefix='.projection-', suffix='.glb', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(struct.pack('<4sII', b'glTF', 2, 28 + len(descriptor) + len(blob)))
            stream.write(struct.pack('<I4s', len(descriptor), b'JSON'))
            stream.write(descriptor)
            stream.write(struct.pack('<I4s', len(blob), b'BIN\0'))
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        check, saved = read(temporary)
        if saved[:len(binary)] != binary or any(check.get(k) != document.get(k)
                for k in ('meshes', 'nodes', 'accessors', 'scenes', 'skins', 'animations', 'samplers')):
            raise TextureProjectionError('The texture copy failed its preservation check.')
        _check(cancelled)
        # Exclusive publication: unlike replace(), a concurrent destination is never overwritten.
        os.link(temporary, destination)
    except FileExistsError as error:
        raise TextureProjectionError('The output file already exists. Choose a new destination.') from error
    except OSError as error:
        raise TextureProjectionError('The texture copy could not be published. Choose a writable local output directory.') from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _load_reference(reference):
    try:
        with Image.open(reference) as opened:
            if opened.width * opened.height > MAX_REFERENCE_PIXELS:
                raise TextureProjectionError('Resize the reference to at most 16 megapixels before projecting.')
            ref = np.asarray(ImageOps.exif_transpose(opened).convert('RGBA'))
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        if isinstance(error, TextureProjectionError):
            raise
        raise TextureProjectionError('The reference image could not be read.') from error
    if min(ref.shape[:2]) < 2:
        raise TextureProjectionError('The reference image is too small.')
    return ref


def _uv_coverage(vertices, normals, uv_pixels, indices, width, height, progress, cancelled):
    owner = np.full((height, width), -1, dtype=np.int32)
    ambiguous = np.zeros((height, width), dtype=bool)
    overlap_tolerance = max(1e-9, float(np.ptp(vertices, axis=0).max()) * 1e-6)
    _progress(progress, 'Checking UV coverage', 0)
    for index, triangle in enumerate(indices):
        if index % 256 == 0:
            _check(cancelled)
            if index % 4096 == 0:
                _progress(progress, 'Checking UV coverage', index / len(indices))
        for region, u, v, inside in _raster(uv_pixels[triangle], width, height):
            previous = owner[region]
            overlapping = inside & (previous >= 0)
            if overlapping.any():
                # Shared triangle edges address the same surface point and are safe.
                # Reused UVs may address a different or reversed surface.
                yy, xx = np.nonzero(overlapping)
                coordinates = np.column_stack((xx + region[1].start, yy + region[0].start))
                previous_triangles = indices[previous[overlapping]]
                old_uv = uv_pixels[previous_triangles]
                a, b = old_uv[:, 1] - old_uv[:, 0], old_uv[:, 2] - old_uv[:, 0]
                delta = coordinates - old_uv[:, 0]
                determinant = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
                old_u = (delta[:, 0] * b[:, 1] - delta[:, 1] * b[:, 0]) / determinant
                old_v = (a[:, 0] * delta[:, 1] - a[:, 1] * delta[:, 0]) / determinant
                bary = np.column_stack((1 - old_u - old_v, old_u, old_v))
                old_point = np.einsum('ni,nij->nj', bary, vertices[previous_triangles])
                new_point = _interp(vertices[triangle], u, v)[overlapping]
                old_normal = np.einsum('ni,nij->nj', bary, normals[previous_triangles])
                new_normal = _interp(normals[triangle], u, v)[overlapping]
                old_normal /= np.maximum(np.linalg.norm(old_normal, axis=1, keepdims=True), 1e-12)
                new_normal /= np.maximum(np.linalg.norm(new_normal, axis=1, keepdims=True), 1e-12)
                different = (np.linalg.norm(old_point - new_point, axis=1) > overlap_tolerance)
                different |= np.einsum('ni,ni->n', old_normal, new_normal) < .5
                ambiguous[region][overlapping] |= different
            previous[inside & (previous < 0)] = index
    return owner, ambiguous


def _project_view(reference, options, atlas, geometry, coverage, *, capture, progress, cancelled):
    vertices, normals, uv_pixels, indices = geometry
    owner, ambiguous = coverage
    height, width = owner.shape
    _check(cancelled)
    ref = _load_reference(reference)
    transform, residual = _alignment(options)
    camera = options['camera']
    plane, basis, perspective = _camera_plane(vertices, camera)
    normal_plane = normals @ basis.T
    normal_plane /= np.maximum(np.linalg.norm(normal_plane, axis=1, keepdims=True), 1e-12)
    projected = _project_camera_plane(plane, perspective)
    frame = np.asarray(camera['frame'])
    extent = frame[2:] - frame[:2]
    normalized = _image_coordinates(projected, frame)
    target = _processing_rect(options, transform)
    source_rect = np.asarray(options['source_rect'])
    local_low = frame[:2] + np.array([target[0], 1 - target[3]]) * extent
    local_high = frame[:2] + np.array([target[2], 1 - target[1]]) * extent
    margin = max(local_high - local_low) * .02
    if np.any(local_low - margin >= local_high + margin):
        raise TextureProjectionError('The selected region is too small for this camera frame.')
    depth_frame = [*(local_low - margin), *(local_high + margin)]
    _progress(progress, 'Checking visible surfaces', 0)
    depth, _ = _depth(projected, indices, depth_frame, 1024, cancelled)
    if not np.isfinite(depth).any():
        raise TextureProjectionError('The model rectangle does not contain a visible surface. Adjust the view or rectangle.')
    depth_tolerance = max(1e-7, float(np.ptp(vertices, axis=0).max()) * .00085)
    # Apply the same world-space visibility tolerance to both depth encodings.
    if perspective:
        valid = np.isfinite(depth)
        depth[valid] /= 1 + depth[valid] * depth_tolerance
    else:
        depth -= depth_tolerance
    ref_linear = _linear(ref[..., :3])
    ref_alpha = ref[..., 3].astype(np.float32) / 255
    if capture:
        # Compact samples keep two 4K views in memory. Work in float32 when blending.
        picture = np.zeros((height, width, 3), dtype=np.uint16)
        angle_weight = np.zeros((height, width), dtype=np.float16)
    else:
        picture = atlas.copy()
        angle_weight = None
    weights = np.zeros((height, width), dtype=np.float32)
    projected_xy = normalized[indices]
    candidates = np.flatnonzero((projected_xy.max(axis=1) >= target[:2]).all(axis=1)
                                & (projected_xy.min(axis=1) <= target[2:]).all(axis=1))
    occluded = overlap = 0
    _progress(progress, 'Projecting reference colors', .2)
    for step, index in enumerate(candidates):
        if step % 128 == 0:
            _check(cancelled)
            if step % 1024 == 0:
                _progress(progress, 'Projecting reference colors', .2 + .8 * step / max(1, len(candidates)))
        triangle = indices[index]
        for region, u, v, inside in _raster(uv_pixels[triangle], width, height):
            point = _interp(plane[triangle], u, v)
            projected_point = _project_camera_plane(point, perspective)
            xy = _image_coordinates(projected_point, frame) if perspective else _interp(normalized[triangle], u, v)
            sample = np.concatenate((xy, np.ones((*xy.shape[:-1], 1))), axis=-1) @ transform
            if _linked(options):
                alpha = _source_alpha(sample, options, [ref.shape[1], ref.shape[0]])
                mask = ((sample >= 0) & (sample <= 1)).all(axis=-1)
            else:
                local = (xy - target[:2]) / (target[2:] - target[:2])
                mask = ((local >= 0) & (local <= 1)).all(axis=-1)
                if options['mask_shape'] == 'ellipse':
                    edge = (1 - np.linalg.norm((local - .5) * 2, axis=-1)) / 2
                else:
                    edge = np.minimum(local, 1 - local).min(axis=-1)
                alpha = _smooth(0, options['feather'], edge) if options['feather'] else (edge >= 0).astype(float)
                mask &= ((sample >= source_rect[:2]) & (sample <= source_rect[2:])).all(axis=-1)
            normal = _interp(normal_plane[triangle], u, v)
            normal /= np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-12)
            facing = normal[..., 2]
            if perspective:
                # Rays diverge in perspective, so facing depends on the sampled point.
                facing = -np.einsum('...i,...i->...', normal, point) / np.linalg.norm(point, axis=-1)
            alpha *= _smooth(.15, .65, facing) * options['opacity']
            sx, sy = sample[..., 0] * ref.shape[1] - .5, sample[..., 1] * ref.shape[0] - .5
            alpha *= map_coordinates(ref_alpha, [sy, sx], order=1, mode='nearest')
            # Match the stored weights for fusion and keep the original single-view cutoff.
            mask_alpha = alpha.astype(np.float32) if capture else alpha
            eligible = inside & mask & (mask_alpha > MIN_SAMPLE_ALPHA)
            overlap += int((eligible & ambiguous[region]).sum())
            eligible &= (owner[region] == index) & ~ambiguous[region]
            screen = _screen(projected_point, depth_frame, depth.shape[1], depth.shape[0])
            rows = np.clip(np.rint(screen[..., 1]), 0, depth.shape[0] - 1).astype(int)
            columns = np.clip(np.rint(screen[..., 0]), 0, depth.shape[1] - 1).astype(int)
            visible_depth = depth[rows, columns]
            visible = np.isfinite(visible_depth) & (projected_point[..., 2] >= visible_depth)
            occluded += int((eligible & ~visible).sum())
            eligible &= visible
            if not eligible.any():
                continue
            colors = np.stack([map_coordinates(ref_linear[..., c], [sy, sx], order=1, mode='nearest') for c in range(3)], axis=-1)
            if capture:
                picture[region][eligible] = np.rint(colors[eligible] * LINEAR_RGB_MAX)
                angle_weight[region][eligible] = .2 + .8 * facing[eligible] ** 2
            else:
                mixed = _srgb(_linear(atlas[region][..., :3]) * (1 - alpha[..., None]) + colors * alpha[..., None])
                picture[region][..., :3][eligible] = mixed[eligible]
            weights[region][eligible] = alpha[eligible]
    projected_count = int(np.count_nonzero(weights))
    if not projected_count:
        reason = 'The selected area has overlapping UVs. Unwrap it into separate islands first.' if overlap else 'No visible surface could receive the reference. Adjust the view, rectangles, or landmarks.'
        raise TextureProjectionError(reason)
    reference_size = [ref.shape[1], ref.shape[0]]
    report = {'projected_texels': projected_count, 'occluded_samples': occluded,
              'overlapping_uv_samples': overlap, 'reference_size': reference_size,
              'alignment_error_pixels': float(np.linalg.norm(residual * reference_size, axis=1).max()) if len(residual) else 0.,
              'options': options, 'effective_target_rect': target.tolist()}
    return picture, weights, angle_weight, report


def _edge_confidence(weights, angle_weight, cancelled):
    # Feather a contribution near its visibility boundary without changing its color.
    _check(cancelled)
    distance = cv2.distanceTransform((weights > 0).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    distance /= FUSION_EDGE_PIXELS
    np.minimum(distance, 1, out=distance)
    for start in range(0, len(weights), 128):
        _check(cancelled)
        rows = slice(start, start + 128)
        t = distance[rows]
        angle_weight[rows] *= weights[rows] * (.25 + .75 * t * t * (3 - 2 * t))


def _fuse_views(atlas, samples, cancelled):
    (a, alpha_a, confidence_a, _), (b, alpha_b, confidence_b, _) = samples
    _edge_confidence(alpha_a, confidence_a, cancelled)
    _edge_confidence(alpha_b, confidence_b, cancelled)
    result = atlas.copy()
    for start in range(0, len(atlas), 128):
        _check(cancelled)
        rows = slice(start, start + 128)
        opacity = np.maximum(alpha_a[rows], alpha_b[rows])
        visible = opacity > 0
        if not visible.any():
            continue
        a_weight = confidence_a[rows][visible].astype(np.float32) * PRIMARY_VIEW_WEIGHT
        b_weight = confidence_b[rows][visible].astype(np.float32)
        b_share = (b_weight / (a_weight + b_weight))[:, None]
        base = _linear(atlas[rows, :, :3][visible])
        rgb_a = a[rows][visible].astype(np.float32) / LINEAR_RGB_MAX
        rgb_b = b[rows][visible].astype(np.float32) / LINEAR_RGB_MAX
        fused = rgb_a * (1 - b_share) + rgb_b * b_share
        alpha = opacity[visible, None]
        result[rows, :, :3][visible] = _srgb(base * (1 - alpha) + fused * alpha)
    return result


def _projection_copy(model_path, views, output_path, progress, cancelled):
    started = time.monotonic()
    source, destination = Path(model_path), Path(output_path)
    views = [(Path(reference), normalize_projection_options(options)) for reference, options in views]
    if destination.exists() or destination.resolve() in {source.resolve(), *(reference.resolve() for reference, _ in views)}:
        raise TextureProjectionError('Projection must write to a new file, preserving the original model and reference.')
    if not destination.parent.is_dir():
        raise TextureProjectionError('The projection output directory does not exist.')
    _check(cancelled)
    _progress(progress, 'Checking mesh and reference', 0)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    doc, binary, _, material_index, atlas, vertices, normals, uv, indices = _load_mesh(source)
    height, width = atlas.shape[:2]
    uv_pixels = np.clip(uv, 0, 1) * [width, height] - .5

    def coverage_progress(stage, fraction):
        _progress(progress, stage, .03 + .29 * fraction)

    owner, ambiguous = _uv_coverage(vertices, normals, uv_pixels, indices, width, height,
                                    coverage_progress, cancelled)
    geometry = vertices, normals, uv_pixels, indices
    multi_view = len(views) == 2
    samples = []
    for index, (reference, options) in enumerate(views):
        def view_progress(stage, fraction):
            if multi_view:
                stage = f'View {index + 1} of 2: {stage}'
            _progress(progress, stage, .32 + .52 * (index + fraction) / len(views))

        try:
            samples.append(_project_view(reference, options, atlas, geometry, (owner, ambiguous),
                                         capture=multi_view, progress=view_progress, cancelled=cancelled))
        except TextureProjectionError as error:
            if multi_view:
                raise TextureProjectionError(f'View {index + 1}: {error}') from error
            raise

    covered = owner >= 0
    del owner, ambiguous
    if multi_view:
        _progress(progress, 'Fusing the two views', .84)
        result = _fuse_views(atlas, samples, cancelled)
        projected = (samples[0][1] > 0) | (samples[1][1] > 0)
        view_reports = [sample[3] for sample in samples]
        overlap_texels = sum(view['projected_texels'] for view in view_reports) - int(projected.sum())
        details = {'view_count': 2, 'blend_mode': 'confidence_weighted', 'views': view_reports,
                   'overlap_texels': overlap_texels,
                   'occluded_samples': sum(view['occluded_samples'] for view in view_reports),
                   'overlapping_uv_samples': sum(view['overlapping_uv_samples'] for view in view_reports)}
    else:
        result, weights, _, details = samples[0]
        projected = weights > 0
        del weights
    del samples
    _check(cancelled)
    _progress(progress, 'Extending texture island borders', .88)
    distance, nearest = distance_transform_edt(~covered, return_indices=True)
    padding = (~covered) & (distance <= 8) & projected[nearest[0], nearest[1]]
    result[..., :3][padding] = result[nearest[0][padding], nearest[1][padding], :3]
    changed = np.any(result[..., :3] != atlas[..., :3], axis=2)
    if not changed.any():
        raise TextureProjectionError('The selected reference produced no color change.')
    if np.any(changed & covered & ~projected) or not np.array_equal(atlas[..., 3], result[..., 3]):
        raise TextureProjectionError('Projection escaped its selected surface or changed opacity.')
    _check(cancelled)
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise TextureProjectionError('The source model changed during projection. Reload it and try again.')
    _progress(progress, 'Saving the texture copy', .96)
    _publish(destination, doc, binary, material_index, result, cancelled)
    warnings = ['Overlapping UV samples were excluded.'] if details['overlapping_uv_samples'] else []
    if multi_view and not details['overlap_texels']:
        warnings.append('The projected areas do not overlap. Both areas were projected separately.')
    report = {'state': 'applied', 'modified_texels': int(changed.sum()),
              'projected_texels': int(projected.sum()), 'padding_texels': int(padding.sum()),
              'triangles': len(indices), 'atlas': [width, height],
              'source_sha256': source_hash,
              'geometry_binary_unchanged': True, 'alpha_unchanged': True,
              'warnings': warnings, 'seconds': round(time.monotonic() - started, 3), **details}
    _progress(progress, 'Texture copy ready', 1)
    return report


def project_texture(model_path, reference_path, output_path, *, options, progress=None, cancelled=None):
    """Project one explicitly aligned reference onto a new GLB, preserving all geometry.

    progress receives (English stage, fraction 0..1). cancelled returns a boolean.
    Overlapping UV samples are excluded so hidden/reused surfaces are not repainted.
    """
    return _projection_copy(model_path, [(reference_path, options)], output_path, progress, cancelled)


def project_texture_views(model_path, views, output_path, *, progress=None, cancelled=None):
    """Fuse two (reference path, options) pairs into the base color of a new GLB."""
    if len(views) != 2:
        raise TextureProjectionError('Texture projection requires exactly two views for fusion.')
    return _projection_copy(model_path, views, output_path, progress, cancelled)
