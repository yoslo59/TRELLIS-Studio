"""Local CPU face-to-albedo reprojection for the static Pixal3D output."""
import io
import json
from pathlib import Path
import struct
from tempfile import NamedTemporaryFile

import cv2
import numpy as np
from PIL import Image, ImageOps

from glb_data import accessor, image, read


class ProjectionSkipped(ValueError):
    """The face or GLB cannot safely be projected."""


def _read_mesh(path):
    document, binary = read(path)
    meshes = document['meshes']
    nodes = document.get('nodes', [])
    # The Pixal3D exporter emits a single static mesh in canonical world coordinates.
    if (len(meshes) != 1 or len(meshes[0]['primitives']) != 1
        or sum('mesh' in node for node in nodes) != 1
        or any(any(key in node for key in ('skin', 'matrix', 'translation', 'rotation', 'scale')) for node in nodes)
        or len(document.get('buffers', [])) != 1 or document.get('animations')):
        raise ProjectionSkipped('Face reprojection needs a single static Pixal3D mesh.')
    primitive = meshes[0]['primitives'][0]
    if primitive.get('mode', 4) != 4 or primitive.get('targets'):
        raise ProjectionSkipped('Face reprojection needs a static triangular mesh.')
    attrs = primitive['attributes']
    vertices = accessor(document, binary, attrs['POSITION'])
    indices = accessor(document, binary, primitive['indices']).reshape(-1, 3)
    uv = accessor(document, binary, attrs['TEXCOORD_0'])
    material = document['materials'][primitive['material']]['pbrMetallicRoughness']
    color = material['baseColorTexture']
    if color.get('texCoord', 0) != 0 or color.get('extensions'):
        raise ProjectionSkipped('Face reprojection needs the original Pixal3D UV layout.')
    texture = document['textures'][color['index']]
    if texture.get('extensions'):
        raise ProjectionSkipped('Face reprojection needs an embedded base-color image.')
    atlas = np.array(image(document, binary, texture['source']).convert('RGBA'))
    if (len(vertices) == 0 or not len(indices) or len(uv) != len(vertices)
        or np.min(indices) < 0 or np.max(indices) >= len(vertices)
        or not np.isfinite(vertices).all() or not np.isfinite(uv).all()):
        raise ProjectionSkipped('The geometry or UV coordinates could not be read.')
    return document, binary, color, texture, atlas, vertices, indices, uv


def _triangle_grid(points, width, height):
    origin, a, b = points[0], points[1] - points[0], points[2] - points[0]
    determinant = a[0] * b[1] - a[1] * b[0]
    if abs(determinant) < 1e-8:
        return None
    x0, y0 = np.maximum(np.floor(points.min(axis=0)).astype(int), 0)
    x1, y1 = np.minimum(np.ceil(points.max(axis=0)).astype(int) + 1, (width, height))
    if x1 <= x0 or y1 <= y0:
        return None
    dy, dx = np.ogrid[y0:y1, x0:x1]
    dx, dy = dx - origin[0], dy - origin[1]
    u = (dx * b[1] - dy * b[0]) / determinant
    v = (a[0] * dy - a[1] * dx) / determinant
    inside = (u >= -.001) & (v >= -.001) & (u + v <= 1.001)
    return (slice(y0, y1), slice(x0, x1)), u, v, inside


def _interpolate(points, u, v):
    return points[0] + u[..., None] * (points[1] - points[0]) + v[..., None] * (points[2] - points[0])


def _sample(pixels, x, y):
    return cv2.remap(pixels, x.astype(np.float32), y.astype(np.float32), cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def _render_face(vertices, indices, uv, atlas):
    extent = vertices.max(axis=0) - vertices.min(axis=0)
    if extent[1] < 1e-5:
        raise ProjectionSkipped('The mesh is too flat to locate a face.')
    screen = vertices.copy()
    screen[:, 0] = 511.5 + (vertices[:, 0] - (vertices[:, 0].max() + vertices[:, 0].min()) / 2) * 920 / extent[1]
    screen[:, 1] = 51.5 + (vertices[:, 1].max() - vertices[:, 1]) * 920 / extent[1]
    world = screen[indices]
    tex = (uv * [atlas.shape[1] - 1, atlas.shape[0] - 1])[indices]
    # The upper body is enough for recognition. Crop before rasterizing, and keep
    # its origin to address the same z-buffer during the atlas projection.
    offset = np.array([250., 28.])
    positions = world.copy()
    positions[..., :2] -= offset
    canvas = np.full((352, 524, 3), 42, dtype=np.uint8)
    depth = np.full(canvas.shape[:2], -np.inf, dtype=np.float32)
    minimum, maximum = positions[..., :2].min(axis=1), positions[..., :2].max(axis=1)
    visible = np.all(maximum >= 0, axis=1) & np.all(minimum < (524, 352), axis=1)
    for index in np.flatnonzero(visible):
        triangle = positions[index]
        raster = _triangle_grid(triangle[:, :2], 524, 352)
        if raster is None:
            continue
        region, u, v, mask = raster
        z = _interpolate(triangle[:, 2:3], u, v)[..., 0]
        mask &= z > depth[region]
        if mask.any():
            coords = _interpolate(tex[index], u, v)
            colors = _sample(atlas, coords[..., 0], coords[..., 1])
            canvas[region][mask] = colors[..., :3][mask]
            depth[region][mask] = z[mask]
    return canvas, depth, offset, world, tex, float(extent[1])


def _find_face(detector, pixels, label, max_size):
    height, width = pixels.shape[:2]
    scale = max_size / max(height, width)
    if scale != 1:
        pixels = cv2.resize(pixels, (max(1, round(width * scale)), max(1, round(height * scale))),
                            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    detector.setInputSize((pixels.shape[1], pixels.shape[0]))
    _, detected = detector.detect(cv2.cvtColor(pixels[..., :3], cv2.COLOR_RGB2BGR))
    if detected is None or len(detected) == 0 or detected[0, 14] < .80:
        raise ProjectionSkipped(f'No clear frontal face was found in the {label}.')
    if len(detected) != 1:
        raise ProjectionSkipped(f'More than one face was found in the {label}.')
    entry = detected[0].astype(float)
    entry[:14] /= scale
    if not np.isfinite(entry).all():
        raise ProjectionSkipped(f'Unreliable facial landmarks in the {label}.')
    box = entry[:4]
    landmarks = entry[4:14].reshape(5, 2)
    eyes = landmarks[:2]
    mouth = landmarks[3:5]
    eyes = eyes[np.argsort(eyes[:, 0])]
    mouth = mouth[np.argsort(mouth[:, 0])]
    points = np.vstack([eyes, landmarks[2], mouth])
    eye_axis = eyes[1] - eyes[0]
    eye_span = np.linalg.norm(eye_axis)
    if eye_span < 8 or not .24 * box[2] <= eye_span <= .65 * box[2]:
        raise ProjectionSkipped(f'The face is too small or too oblique in the {label}.')
    roll = np.arctan2(eye_axis[1], eye_axis[0])
    eye_center = eyes.mean(axis=0)
    facial_down = np.array([-np.sin(roll), np.cos(roll)])
    horizontal = eye_axis / eye_span
    nose = (points[2] - eye_center) / eye_span
    mouth_down = (mouth.mean(axis=0) - eye_center) @ facial_down / eye_span
    if (abs(roll) > np.deg2rad(20) or abs(nose @ horizontal) > .25
        or not .18 <= nose @ facial_down <= .95 or not .48 <= mouth_down <= 1.65):
        raise ProjectionSkipped(f'The face is too small or too oblique in the {label}.')
    return box, points, float(entry[14])


def _alignment(target, source):
    # Solve one similarity transform. A per-feature warp would transfer camera
    # and detector errors directly into the eyes and mouth.
    a = target - target.mean(axis=0)
    b = source - source.mean(axis=0)
    left, singular, right = np.linalg.svd(a.T @ b)
    if np.linalg.det(left @ right) <= 0:
        raise ProjectionSkipped('The face views do not align reliably.')
    linear = (left @ right) * (singular.sum() / (a * a).sum())
    translation = source.mean(axis=0) - target.mean(axis=0) @ linear
    difference = target @ linear + translation - source
    eye_span = np.linalg.norm(source[1] - source[0])
    error = np.linalg.norm(difference, axis=1) / eye_span
    if np.sqrt(np.mean(error ** 2)) > .13 or np.max(error) > .21:
        raise ProjectionSkipped('The source and generated facial landmarks do not align reliably.')
    return linear, translation, round(float(np.sqrt(np.mean(error ** 2))), 4)


def _smooth(low, high, values):
    fraction = np.clip((values - low) / (high - low), 0, 1)
    return fraction * fraction * (3 - 2 * fraction)


def _blend(atlas, ref, vertices, indices, world, tex, depth, offset, height_m, face, linear, translation):
    box, points, _ = face
    eye_center = points[:2].mean(axis=0)
    mouth_center = points[3:].mean(axis=0)
    center = .38 * eye_center + .62 * mouth_center
    span = np.linalg.norm(points[1] - points[0])
    radius_x = min(box[2] * .53, span * 1.20)
    radius_y = min(box[3] * .45, (mouth_center[1] - eye_center[1]) * 1.24)
    top, bottom = box[1] + box[3] * .13, box[1] + box[3] * .99
    normal = np.cross(vertices[indices[:, 1]] - vertices[indices[:, 0]],
                      vertices[indices[:, 2]] - vertices[indices[:, 0]])
    facing = normal[:, 2] / np.maximum(np.linalg.norm(normal, axis=1), 1e-12)
    low = world[..., :2].min(axis=1)
    high = world[..., :2].max(axis=1)
    candidates = (facing > .12) & np.all(high >= (center[0] - radius_x * 1.05, top), axis=1)
    candidates &= np.all(low <= (center[0] + radius_x * 1.05, bottom), axis=1)
    result = atlas.copy()
    coverage = np.zeros(atlas.shape[:2], dtype=bool)
    for index in np.flatnonzero(candidates):
        raster = _triangle_grid(tex[index], atlas.shape[1], atlas.shape[0])
        if raster is None:
            continue
        region, u, v, inside = raster
        pos = _interpolate(world[index], u, v)
        radius = np.hypot((pos[..., 0] - center[0]) / radius_x, (pos[..., 1] - center[1]) / radius_y)
        opacity = .88 * (1 - _smooth(.65, 1.02, radius)) * _smooth(.12, .53, facing[index])
        opacity *= _smooth(top, top + .10 * box[3], pos[..., 1])
        opacity *= 1 - _smooth(bottom - .12 * box[3], bottom, pos[..., 1])
        coords = np.rint(pos[..., :2] - offset).astype(int)
        col = np.clip(coords[..., 0], 0, depth.shape[1] - 1)
        row = np.clip(coords[..., 1], 0, depth.shape[0] - 1)
        front = depth[row, col]
        inside &= np.isfinite(front) & (pos[..., 2] >= front - .0038 * height_m) & (opacity > 1 / 255)
        if not inside.any():
            continue
        source = pos[..., :2] @ linear + translation
        inside &= ((source[..., 0] >= 0) & (source[..., 0] <= ref.shape[1] - 1)
                   & (source[..., 1] >= 0) & (source[..., 1] <= ref.shape[0] - 1))
        pixels = _sample(ref, source[..., 0], source[..., 1])
        weights = (opacity * pixels[..., 3] / 255)[..., None]
        inside &= weights[..., 0] > 1 / 255
        original = atlas[region][..., :3]
        mixed = np.rint(original * (1 - weights) + pixels[..., :3] * weights).astype(np.uint8)
        result[region][..., :3][inside] = mixed[inside]
        coverage[region][inside] = True
    changed = int(np.count_nonzero(np.any(result[..., :3] != atlas[..., :3], axis=2) & coverage))
    if not .000025 * coverage.size <= changed <= .035 * coverage.size:
        raise ProjectionSkipped('The projected face area was outside the safety bounds.')
    return result, changed


def _save(destination, document, binary, color, texture, atlas):
    with io.BytesIO() as stream:
        Image.fromarray(atlas).save(stream, format='PNG')
        pixels = stream.getvalue()
    binary += b'\0' * (-len(binary) % 4)
    document['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(pixels)})
    document['images'].append({'bufferView': len(document['bufferViews']) - 1, 'mimeType': 'image/png'})
    document['textures'].append({**texture, 'source': len(document['images']) - 1})
    color['index'] = len(document['textures']) - 1
    binary += pixels + b'\0' * (-len(pixels) % 4)
    document['buffers'][0]['byteLength'] = len(binary)
    header = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    header += b' ' * (-len(header) % 4)
    temporary = None
    try:
        with NamedTemporaryFile(dir=destination.parent, prefix='.face-', suffix='.glb', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(struct.pack('<4sII', b'glTF', 2, 28 + len(header) + len(binary)))
            stream.write(struct.pack('<I4s', len(header), b'JSON'))
            stream.write(header)
            stream.write(struct.pack('<I4s', len(binary), b'BIN\0'))
            stream.write(binary)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def reproject_face(model_path, reference_path, detector_path, output_path=None):
    """Project the uploaded front photo, retaining the original model on a skip."""
    model_path = Path(model_path)
    destination = Path(output_path) if output_path is not None else model_path
    threads = cv2.getNumThreads()
    cv2.setNumThreads(min(threads, 4))
    try:
        if not Path(detector_path).is_file():
            raise ProjectionSkipped('The local facial landmark model is missing.')
        detector = cv2.FaceDetectorYN.create(str(detector_path), '', (320, 320), .65, .3, 20)
        with Image.open(reference_path) as uploaded:
            ref = np.array(ImageOps.exif_transpose(uploaded).convert('RGBA'))
        _, source_points, source_score = _find_face(detector, ref, 'original image', 1024)
        document, binary, color, texture, atlas, vertices, indices, uv = _read_mesh(model_path)
        canvas, depth, offset, world, tex, height_m = _render_face(vertices, indices, uv, atlas)
        box, target_points, target_score = _find_face(detector, canvas, 'generated model', 736)
        box[:2] += offset
        target_points += offset
        linear, translation, alignment_error = _alignment(target_points, source_points)
        result, count = _blend(atlas, ref, vertices, indices, world, tex, depth, offset, height_m,
                               (box, target_points, target_score), linear, translation)
        _save(destination, document, binary, color, texture, result)
        return {'state': 'applied', 'modified_texels': count, 'source_confidence': round(source_score, 3),
                'model_confidence': round(target_score, 3), 'alignment_error': alignment_error}
    except (OSError, ValueError, cv2.error, KeyError, IndexError, TypeError, np.linalg.LinAlgError) as error:
        return {'state': 'skipped', 'reason': str(error)}
    finally:
        cv2.setNumThreads(threads)
