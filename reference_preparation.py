"""CPU-only reference preparation shared by previews and generation jobs."""
from collections import OrderedDict
import hashlib
import json
import math
from threading import Lock

import numpy as np
from PIL import Image, ImageDraw


PREPARED_VIEWS = {'front': 'prepared.png', 'left': 'prepared-left.png',
                  'back': 'prepared-back.png', 'right': 'prepared-right.png'}
_MASK_CACHE = OrderedDict()
_PREPARATION_LOCK = Lock()


class BackgroundRemovalError(RuntimeError):
    pass


def parse_reference_edits(raw, roles):
    if len(raw) > 128 * 1024:
        raise ValueError('Reference corrections are too large.')
    try:
        edits = json.loads(raw) if raw else {}
    except (ValueError, RecursionError) as error:
        raise ValueError('Invalid reference corrections.') from error
    if not isinstance(edits, dict) or any(role not in roles for role in edits):
        raise ValueError('Reference corrections must match the supplied views.')

    def normalized(values, count):
        return isinstance(values, list) and len(values) == count and all(
            type(value) in (int, float) and 0 <= value <= 1 for value in values)

    for edit in edits.values():
        if not isinstance(edit, dict) or edit.keys() - {'crop', 'strokes'}:
            raise ValueError('Invalid reference corrections.')
        if 'crop' in edit:
            crop = edit['crop']
            if not normalized(crop, 4) or crop[0] >= crop[2] or crop[1] >= crop[3]:
                raise ValueError('Invalid reference crop.')
        strokes = edit.get('strokes', [])
        if not isinstance(strokes, list) or len(strokes) > 48:
            raise ValueError('Use at most 48 brush strokes per view.')
        for stroke in strokes:
            if (not isinstance(stroke, dict) or stroke.keys() != {'mode', 'radius', 'points'}
                or stroke['mode'] not in ('keep', 'erase')
                or not normalized([stroke['radius']], 1) or not 0.001 <= stroke['radius'] <= 0.5
                or not isinstance(stroke['points'], list) or not 1 <= len(stroke['points']) <= 128
                or not all(normalized(point, 2) for point in stroke['points'])):
                raise ValueError('Invalid reference brush stroke.')
    return edits


def _new_background_session(weights):
    if not weights.is_file():
        raise BackgroundRemovalError('Background removal needs the Studio U2Net model. Use transparent images or reinstall Studio.')
    import onnxruntime as ort
    from rembg.sessions.u2net import U2netSession

    class LocalU2netSession(U2netSession):
        @classmethod
        def download_models(cls, *args, **kwargs):
            return weights

    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    return LocalU2netSession('u2net', options, providers=['CPUExecutionProvider'])


def _automatic_cutouts(sources, weights, alpha_threshold):
    cutouts = {}
    session = None
    for role, source in sources.items():
        if source.getchannel('A').getextrema()[0] < alpha_threshold:
            cutouts[role] = source.copy()
            continue
        try:
            key = (weights, weights.stat().st_mtime_ns, source.size,
                   hashlib.sha256(source.tobytes()).digest())
            mask = _MASK_CACHE.get(key)
            if mask is None:
                from onnxruntime.capi.onnxruntime_pybind11_state import (
                    EngineError, EPFail, Fail, InvalidArgument, InvalidGraph, InvalidProtobuf,
                    NoSuchFile, RuntimeException,
                )
                from rembg import remove
                try:
                    if session is None:
                        session = _new_background_session(weights)
                    mask = remove(source, session=session, only_mask=True)
                except (EngineError, EPFail, Fail, InvalidArgument, InvalidGraph, InvalidProtobuf,
                        NoSuchFile, RuntimeException) as error:
                    raise BackgroundRemovalError(
                        'The local U2Net model could not run. Reinstall the Studio model or use a transparent reference.'
                    ) from error
                _MASK_CACHE[key] = mask
                if len(_MASK_CACHE) > 8:
                    _MASK_CACHE.popitem(last=False)
            else:
                _MASK_CACHE.move_to_end(key)
        except FileNotFoundError as error:
            raise BackgroundRemovalError('Background removal needs the Studio U2Net model. Use transparent images or reinstall Studio.') from error
        except (ImportError, OSError) as error:
            raise BackgroundRemovalError(f'Unable to run the local background remover: {error}') from error
        cutouts[role] = Image.composite(source, Image.new('RGBA', source.size), mask)
    return cutouts


def _paint(cutout, source, strokes):
    if not strokes:
        return
    width, height = source.size
    opaque = source.copy()
    opaque.putalpha(255)
    for stroke in strokes:
        radius = max(1, round(stroke['radius'] * min(source.size)))
        points = [(round(x * (width - 1)), round(y * (height - 1))) for x, y in stroke['points']]
        brush = Image.new('L', source.size)
        draw = ImageDraw.Draw(brush)
        draw.line(points, fill=255, width=2 * radius + 1, joint='curve')
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
        if stroke['mode'] == 'keep':
            cutout.paste(opaque, mask=brush)
        else:
            cutout.paste((0, 0, 0, 0), (0, 0, width, height), brush)


def _cropped(image, crop, role):
    if crop is None:
        return image
    width, height = image.size
    box = (math.floor(crop[0] * width), math.floor(crop[1] * height),
           math.ceil(crop[2] * width), math.ceil(crop[3] * height))
    if box[2] - box[0] < 16 or box[3] - box[1] < 16:
        raise ValueError(f'The {role} view crop must be at least 16 × 16 pixels.')
    return image.crop(box)


def _trellis_frame(image):
    # Match the pinned TRELLIS.2 preprocessing used by unreviewed jobs.
    scale = min(1, 1024 / max(image.size))
    if scale < 1:
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                             Image.Resampling.LANCZOS)
    bounds = image.getchannel('A').point(lambda value: 255 if value > 204 else 0).getbbox()
    if bounds is None:
        raise ValueError('No usable silhouette in the front view. Restore the foreground or change the crop.')
    left, top, right, bottom = bounds
    right -= 1
    bottom -= 1
    half = max(right - left, bottom - top) // 2
    if half == 0:
        raise ValueError('The front silhouette is too small.')
    x, y = (left + right) / 2, (top + bottom) / 2
    return Image.fromarray((premultiplied_rgb(image.crop((x - half, y - half, x + half, y + half))) * 255).astype(np.uint8))


def pixal_silhouette(image, role):
    bounds = image.getchannel('A').point(lambda value: 255 if value >= 128 else 0).getbbox()
    if bounds is None or bounds[2] - bounds[0] < 16 or bounds[3] - bounds[1] < 16:
        raise ValueError(f'No usable silhouette in the {role} view.')
    return image.crop(bounds)


def frame_pixal(silhouettes):
    # All views share body height even when one silhouette is wider.
    height = min(920, min(944 * image.height // image.width for image in silhouettes.values()))
    if height < 128:
        raise ValueError('The reference silhouette is too wide. Use a full-body character image.')
    frames = {}
    for role, image in silhouettes.items():
        width = max(1, round(image.width * height / image.height))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', (1024, 1024))
        canvas.paste(image, ((1024 - width) // 2, (1024 - height) // 2))
        frames[role] = canvas
    return frames


def premultiplied_rgb(image):
    rgba = np.asarray(image, dtype=np.float32) / 255
    return rgba[..., :3] * rgba[..., 3:4]


def prepare_references(sources, edits, engine, remove_bg, weights):
    with _PREPARATION_LOCK:
        threshold = 128 if engine == 'pixal3d' else (255 if remove_bg else 256)
        cutouts = _automatic_cutouts(sources, weights, threshold)
        cropped = {}
        for role, cutout in cutouts.items():
            edit = edits.get(role, {})
            _paint(cutout, sources[role], edit.get('strokes', []))
            cropped[role] = _cropped(cutout, edit.get('crop'), role)
        if engine == 'pixal3d':
            frames = frame_pixal({role: pixal_silhouette(image, role) for role, image in cropped.items()})
        else:
            frames = {'front': _trellis_frame(cropped['front'])}
        return cutouts, frames
