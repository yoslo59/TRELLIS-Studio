"""Filesystem-only capability and calibrated-view rules for the optional Pixal3D runtime."""
import hashlib
import json
import math
from pathlib import Path

VIEW_FILES = {
    'front': 'input.png',
    'left': 'input-left.png',
    'back': 'input-back.png',
    'right': 'input-right.png',
}
CAMERA_RADIUS = 3.1192
CAMERA_FOV = math.radians(20)
FACE_SETUP_HINT = "Run Install Pixal3D.cmd --face-only to enable face reprojection."


def runtime_root(studio_root):
    return Path(studio_root) / 'models' / 'pixal3d'


def load_manifest(studio_root):
    path = Path(studio_root) / 'installer' / 'pixal3d-manifest.json'
    return json.loads(path.read_text(encoding='utf-8'))


def source_path(directory, manifest):
    return directory / ('ComfyUI-' + manifest['comfy']['commit'])


def verified_face_detector(studio_root):
    entry = load_manifest(studio_root)['face_detector']
    path = runtime_root(studio_root) / 'models' / entry['path']
    try:
        data = path.read_bytes()
    except OSError as error:
        raise RuntimeError(FACE_SETUP_HINT) from error
    if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise RuntimeError('Face detector checksum mismatch. Reinstall the verified face detector.')
    return path


def runtime_status(studio_root):
    directory = runtime_root(studio_root)
    face_ready = False
    try:
        manifest = load_manifest(studio_root)
        marker = json.loads((directory / 'verified.json').read_text(encoding='utf-8'))
        ready = (
            marker == {'schema': 1, 'source_sha256': manifest['comfy']['sha256'],
                       'model_revision': manifest['weights_revision']}
            and (source_path(directory, manifest) / 'comfy_extras' / 'nodes_trellis2.py').is_file()
            and (directory / 'deps' / 'comfy_kitchen').is_dir()
            and all((directory / 'models' / item['path']).stat().st_size == item['bytes']
                    for item in manifest['weights'])
        )
    except (OSError, ValueError, KeyError, TypeError):
        ready = False
    if ready:
        try:
            verified_face_detector(studio_root)
            face_ready = True
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            pass
    return {
        'ready': ready,
        'message': 'Pixal3D is ready.' if ready else 'Run Install Pixal3D.cmd to enable Pixal3D.',
        'face_projection': {
            'ready': face_ready,
            'message': 'Face reprojection is ready.' if face_ready else FACE_SETUP_HINT,
        },
    }


def validate_views(views):
    if not isinstance(views, list) or not 2 <= len(views) <= 4:
        raise ValueError('Add the front view and one to three complementary views.')
    indexed = {}
    for view in views:
        if not isinstance(view, dict):
            raise ValueError('Invalid Pixal3D view.')
        role = view.get('role')
        azimuth = view.get('azimuth')
        if not isinstance(role, str) or role not in VIEW_FILES or role in indexed:
            raise ValueError('Each Pixal3D view must use a different camera slot.')
        if view.get('file', VIEW_FILES[role]) != VIEW_FILES[role]:
            raise ValueError('Unexpected Pixal3D image filename.')
        if type(azimuth) not in (int, float) or not -180 <= azimuth <= 180:
            raise ValueError('Invalid Pixal3D camera angle.')
        valid = {
            'front': azimuth == 0,
            'left': 10 <= azimuth <= 170,
            'back': abs(azimuth) == 180,
            'right': -170 <= azimuth <= -10,
        }
        if not valid[role]:
            raise ValueError(f'The {role} view has the wrong camera angle.')
        indexed[role] = {'role': role, 'azimuth': azimuth, 'file': VIEW_FILES[role]}
    if 'front' not in indexed:
        raise ValueError('The front view is required.')
    return [indexed[role] for role in VIEW_FILES if role in indexed]


def camera_matrix(azimuth):
    angle = math.radians(azimuth)
    sine, cosine = math.sin(angle), math.cos(angle)
    return [
        [cosine, 0., sine, CAMERA_RADIUS * sine],
        [sine, 0., -cosine, -CAMERA_RADIUS * cosine],
        [0., 1., 0., 0.],
        [0., 0., 0., 1.],
    ]
