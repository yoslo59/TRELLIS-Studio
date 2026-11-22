"""Standalone Character 05 reference preparation for the native Game Asset pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
from types import ModuleType

import numpy as np
from PIL import Image, ImageOps


CONDITIONING_RESOLUTION = 1024
FOREGROUND_PADDING = 1.1
BIREFNET_REVISION = 'e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4'
BIREFNET_CHECKPOINT = '9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154'
BIREFNET_SOURCES = {
    'birefnet.py': '208771ae626f653d64128fbf2d6ac9f8e645c5cc5e286258a73ec3322bbfe5ef',
    'BiRefNet_config.py': 'e7b8c2a74f6cea6a59553d517f71d47f2c1d90e670a13416af17c25fe2f3dc52',
}


MOGE_REVISION = '74fbce054ebed49800de42d0ad0e83495065719a'
MOGE_CHECKPOINT = 'cb1a692d03235671e959e81360d7b4d9f44aefadb1f852d6ca6aa17799d5e31f'
MOGE_SOURCES = {
    'moge/model/v2.py': 'd70c1be8c7c62770a98bbf9e7c84e7c6d536eb1cf31ac78fcc136d36a0eaab3f',
    'configs/train/v2.json': 'e913609fd36fc3861a64964e2816686595cacfadeb14700d226c5008cc1f3e35',
}
@dataclass(frozen=True)
class EstimatedCamera:
    horizontal_fov_degrees: float
    focal_pixels: float

@dataclass(frozen=True)
class PreparedReference:
    conditioning_rgb: np.ndarray
    foreground_mask: np.ndarray
    crop_box: tuple[int, int, int, int]
    framing_size: tuple[int, int]
    source_size: tuple[int, int]


def read_reference(image_path):
    """Load the first EXIF-corrected RGB frame as used by Character 05."""
    with Image.open(Path(image_path)) as source:
        frame = ImageOps.exif_transpose(source).convert('RGB')
        return np.array(frame, dtype=np.uint8)


def _byte_image(values):
    return np.clip(np.asarray(values, dtype=np.float32) * 255, 0, 255).astype(np.uint8)


def _resize_bytes(values, width, height):
    return np.asarray(Image.fromarray(values).resize((width, height), Image.Resampling.LANCZOS))


def _foreground_shape(alpha):
    coordinates = np.nonzero(alpha > 204)
    if not coordinates[0].size:
        return None
    y, x = coordinates
    return int(x.min()), int(y.min()), int(x.max()), int(y.max())


def crop_reference(rgb, foreground_mask, *, width=CONDITIONING_RESOLUTION, height=CONDITIONING_RESOLUTION,
                   pad_factor=FOREGROUND_PADDING, grow_mask=0, background='#000000'):
    """Frame the inference crop using the soft BiRefNet mask."""
    rgb = np.asarray(rgb)
    foreground_mask = np.asarray(foreground_mask, dtype=np.float32)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('An 8-bit RGB reference is required.')
    if foreground_mask.shape != rgb.shape[:2] or not np.isfinite(foreground_mask).all():
        raise ValueError('The foreground mask must be finite and match the reference dimensions.')
    output_width, output_height = width, height
    aspect_ratio = output_width / output_height
    color = background.removeprefix('#')
    if len(color) != 6 or any(channel not in '0123456789abcdefABCDEF' for channel in color):
        raise ValueError('Use a six-digit RGB background color such as #000000.')
    background_rgb = np.array(tuple(bytes.fromhex(color)), dtype=np.uint8)
    height, width = rgb.shape[:2]
    if not width or not height:
        raise ValueError('The reference has no pixels.')
    source_size = (width, height)
    mask = np.clip(foreground_mask, 0, 1)

    border = np.concatenate((mask[0], mask[-1], mask[:, 0], mask[:, -1]))
    center = mask[height // 4:height - height // 4, width // 4:width - width // 4]
    if border.mean() > 0.5 and center.mean() < 0.5:
        mask = 1 - mask
    if grow_mask:
        from torch import from_numpy
        from torch.nn.functional import max_pool2d

        radius = abs(grow_mask)
        roots = from_numpy(mask if grow_mask > 0 else 1 - mask)
        expanded = max_pool2d(roots[None, None], 2 * radius + 1, stride=1, padding=radius)[0, 0].numpy()
        mask = expanded if grow_mask > 0 else 1 - expanded
    mask = np.where(mask < 0.05, np.float32(0), mask)

    resolution = max(output_width, output_height)
    if max(height, width) > resolution:
        reduction = resolution / max(height, width)
        width, height = int(width * reduction), int(height * reduction)
        if width < 1 or height < 1:
            raise ValueError('The reference is too narrow to prepare an inference crop.')
        rgb = _resize_bytes(rgb, width, height)
        mask = _resize_bytes(_byte_image(mask), width, height).astype(np.float32) / 255
    framing_size = (width, height)

    alpha = _byte_image(mask)
    bounds = _foreground_shape(alpha)
    if bounds is None:
        bounds = _foreground_shape(255 - alpha)
        if bounds is not None:
            mask = 1 - mask
    if bounds is None:
        box = (0, 0, width, height)
    else:
        x0, y0, x1, y1 = bounds
        bound_width, bound_height = x1 - x0 + 1, y1 - y0 + 1
        if bound_width / bound_height >= aspect_ratio:
            crop_width, crop_height = bound_width, bound_width / aspect_ratio
        else:
            crop_width, crop_height = bound_height * aspect_ratio, bound_height
        half_width = math.ceil(int(crop_width * pad_factor) / 2)
        half_height = math.ceil(int(crop_height * pad_factor) / 2)
        left = int((x0 + x1) / 2 - half_width)
        top = int((y0 + y1) / 2 - half_height)
        box = (left, top, left + 2 * half_width, top + 2 * half_height)

    # Compose only the selected region and fill out-of-bounds pixels with the selected background.
    left, top, right, bottom = box
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(width, right), min(height, bottom)
    crop = np.empty((bottom - top, right - left, 3), dtype=np.uint8)
    crop[:] = background_rgb
    if x0 < x1 and y0 < y1:
        pixels = rgb[y0:y1, x0:x1].astype(np.float32) / 255
        coverage = mask[y0:y1, x0:x1, None]
        composite = pixels * coverage + (background_rgb.astype(np.float32) / 255) * (1 - coverage)
        crop[y0 - top:y1 - top, x0 - left:x1 - left] = _byte_image(composite)
    framed = _resize_bytes(crop, output_width, output_height).copy()
    return PreparedReference(framed, foreground_mask, box, framing_size, source_size)


def birefnet_input(rgb, device):
    """Prepare exact float32 RGB with the graph's 8-bit rounding and zero/one normalization."""
    import torch
    from torch.nn import functional as F

    image = torch.as_tensor(np.asarray(rgb), device=device).float().div_(255)
    image = image.permute(2, 0, 1).unsqueeze(0)
    size = (CONDITIONING_RESOLUTION, CONDITIONING_RESOLUTION)
    if image.shape[-2:] != size:
        image = F.interpolate(image, size=size, mode='bicubic', antialias=True)
    return image.mul(255).clamp(0, 255).round().div_(255)


def birefnet_mask(logits, height, width):
    """Resize pre-sigmoid logits as the original two BiRefNet calls do."""
    from torch.nn import functional as F

    if logits.ndim != 4 or tuple(logits.shape[:2]) != (1, 1):
        raise ValueError('Expected one single-channel BiRefNet logit image.')
    logits = F.interpolate(logits, size=(height, width), mode='bicubic', antialias=False)
    return logits[0, 0].sigmoid().detach().float().cpu().numpy()


def _require_hash(path, expected, error):
    if not path.is_file():
        raise ValueError(error)
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError(error)


def _namespace_module(namespace, source, module):
    if namespace not in sys.modules:
        package = ModuleType(namespace)
        package.__path__ = [str(source)]
        sys.modules[namespace] = package
    return importlib.import_module(f'{namespace}.{module}')

def mask_reference(image_path, checkpoint, birefnet_source, *, device, progress=None):
    """Return the source RGB image and its soft full-resolution BiRefNet mask."""
    import torch
    from safetensors.torch import load_file

    checkpoint, source = Path(checkpoint), Path(birefnet_source).resolve()
    _require_hash(checkpoint, BIREFNET_CHECKPOINT,
                  'The native Game Asset pipeline needs the pinned BiRefNet weights.')
    for filename, digest in BIREFNET_SOURCES.items():
        _require_hash(source / filename, digest,
                      'The native Game Asset pipeline needs its matching standalone BiRefNet source.')

    package_name = f'_studio_birefnet_{BIREFNET_REVISION[:7]}'
    config = _namespace_module(package_name, source, 'BiRefNet_config')
    upstream = _namespace_module(package_name, source, 'birefnet')
    rgb = read_reference(image_path)
    model = upstream.BiRefNet(config=config.BiRefNetConfig()).eval()
    model.load_state_dict(load_file(str(checkpoint), device='cpu'), strict=True)
    model = model.to(device=device, dtype=torch.float32)
    if progress is not None:
        progress('Removing the reference background with BiRefNet', 12)
    with torch.inference_mode():
        outputs = model(birefnet_input(rgb, device))
        mask = birefnet_mask(outputs[-1], *rgb.shape[:2])
    return rgb, mask


def prepare_reference(image_path, checkpoint, birefnet_source, *, device, progress=None, crop_options=None):
    """Run the pinned BiRefNet checkpoint and prepare the geometry conditioning image."""
    rgb, mask = mask_reference(image_path, checkpoint, birefnet_source, device=device, progress=progress)
    return crop_reference(rgb, mask, **(crop_options or {}))

def estimate_camera(conditioning_rgb, checkpoint, moge_source, *, device, progress=None, resolution_level=9):
    """Estimate the horizontal lens field of view with Character 05's MoGe-2 checkpoint."""
    import torch
    from safetensors.torch import load_file

    checkpoint, source = Path(checkpoint), Path(moge_source).resolve()
    _require_hash(checkpoint, MOGE_CHECKPOINT, 'The native Game Asset pipeline needs the pinned MoGe-2 weights.')
    for filename, digest in MOGE_SOURCES.items():
        _require_hash(source / filename, digest,
                      'The native Game Asset pipeline needs its matching standalone MoGe-2 source.')

    rgb = np.asarray(conditioning_rgb)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or not min(rgb.shape[:2]):
        raise ValueError('MoGe-2 requires a prepared 8-bit RGB reference.')
    height, width = rgb.shape[:2]
    aspect_ratio = width / height
    namespace = f'_studio_moge_{MOGE_REVISION[:7]}'
    network = _namespace_module(namespace, source / 'moge', 'model.v2')
    geometry = _namespace_module(namespace, source / 'moge', 'utils.geometry_torch')
    config = json.loads((source / 'configs/train/v2.json').read_text(encoding='utf-8'))['model']
    model = network.MoGeModel(**config).eval()
    model.load_state_dict(load_file(str(checkpoint), device='cpu'), strict=True)
    model = model.to(device=device, dtype=torch.float32)
    minimum, maximum = model.num_tokens_range
    num_tokens = int(minimum + (resolution_level / 9) * (maximum - minimum))
    image = torch.as_tensor(rgb, device=device).float().div_(255).permute(2, 0, 1).unsqueeze(0)
    if progress is not None:
        progress('Estimating the reference camera with MoGe-2', 19)
    with torch.inference_mode():
        output = model(image, num_tokens=num_tokens)
        focal_diagonal, _ = geometry.recover_focal_shift(output['points'].float(), output['mask'] > 0.5)
        focal_x = float((focal_diagonal[0] / 2 * math.sqrt(1 + aspect_ratio ** 2) / aspect_ratio).item())
    if not math.isfinite(focal_x) or focal_x <= 0:
        # The published graph fixes invalid least-squares camera solutions at 60 degrees.
        return EstimatedCamera(60., width * 0.5 / math.tan(math.pi / 6))
    field_of_view = math.degrees(2 * math.atan(0.5 / focal_x))
    return EstimatedCamera(field_of_view, focal_x * width)
