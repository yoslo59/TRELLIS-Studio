"""Portrait preparation and the optional, local TOPOS geometry runtime."""
import math
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
TOOL = ROOT / '.tools' / 'topos'
SOURCE = TOOL / 'source'
WEIGHTS = ROOT / 'models' / 'topos'
PYTHON = TOOL / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
WEIGHT_SIZES = {
    'TOPOS-VAE.model.pth': 40033015,
    'TOPOS-DiT.model.pth': 2228761001,
    'dinov2_vitl14_reg4_pretrain.pth': 1217607321,
}


def detector_path():
    from pixal3d_runtime import verified_face_detector
    return verified_face_detector(ROOT)


def capability():
    try:
        detector_path()
        ready = all(p.is_file() for p in (PYTHON, SOURCE / 'train_dit.py',
                    SOURCE / 'data/template.obj', TOOL / 'dinov2/hubconf.py'))
        ready = ready and all((WEIGHTS / name).stat().st_size == size for name, size in WEIGHT_SIZES.items())
    except (ValueError, KeyError, OSError, RuntimeError):
        ready = False
    return {'available': ready, 'reason': '' if ready else
            'Install the optional face component on the Studio host. See docs/FACE_REFINEMENT.md.'}


def open_portrait(path):
    try:
        with Image.open(path) as image:
            if (image.format not in ('JPEG', 'PNG', 'WEBP') or min(image.size) < 16
                    or image.width * image.height > 16_000_000):
                raise ValueError('Choose a PNG, JPEG or WebP portrait up to 16 megapixels.')
            return ImageOps.exif_transpose(image).convert('RGBA')
    except (Image.DecompressionBombError, OSError) as error:
        raise ValueError('Choose a readable PNG, JPEG or WebP portrait up to 16 megapixels.') from error


def _detect(image, max_size):
    import cv2
    import numpy as np
    from face_projection import _find_face
    detector = cv2.FaceDetectorYN.create(str(detector_path()), '', (320, 320), .65, .3, 20)
    backdrop = Image.new('RGB', image.size, (74, 74, 74))
    backdrop.paste(image, mask=image.getchannel('A'))
    return _find_face(detector, np.asarray(backdrop), 'portrait', max_size)[0]


def suggest_crop(path):
    """Return x/y/width/height fractions in the displayed (EXIF-oriented) image."""
    from face_projection import ProjectionSkipped
    image = open_portrait(path)
    try:
        x, y, w, h = _detect(image, 1024)
    except ProjectionSkipped:
        return None
    width, height = image.size
    side = min(max(w, h) * 2.05, width, height)
    left = max(0., min(width - side, x + .5 * w - .5 * side))
    top = max(0., min(height - side, y + .44 * h - .5 * side))
    return [float(left / width), float(top / height), float(side / width), float(side / height)]


def prepare_portrait(source_path, crop, output_png):
    image = open_portrait(source_path)
    if (not isinstance(crop, (tuple, list)) or len(crop) != 4
            or not all(type(v) in (int, float) and math.isfinite(v) for v in crop)):
        raise ValueError('Select a crop around the face.')
    x, y, w, h = crop
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
        raise ValueError('Keep the selected crop inside the portrait.')
    box = (round(x * image.width), round(y * image.height), round((x + w) * image.width),
           round((y + h) * image.height))
    if min(box[2] - box[0], box[3] - box[1]) < 24:
        raise ValueError('Choose a larger crop or a closer portrait.')
    cropped = image.crop(box)
    face = _detect(cropped, 640)
    if max(face[2:]) < max(cropped.size) * .22:
        raise ValueError('Crop closer around the head and neck, or choose a closer portrait.')
    # TOPOS takes a square image. Preserve facial proportions for rectangular crops.
    cropped.thumbnail((768, 768), Image.Resampling.LANCZOS)
    side = max(cropped.size)
    portrait = Image.new('RGBA', (side, side))
    portrait.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    portrait.save(output_png, format='PNG')


def reconstruct_head(portrait, obj_path):
    runtime = capability()
    if not runtime['available']:
        raise RuntimeError(runtime['reason'])
    command = [str(PYTHON), str(Path(__file__).resolve()), str(Path(portrait).resolve()),
               str(Path(obj_path).resolve())]
    result = subprocess.run(command, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode == 3:
        raise RuntimeError('Face reconstruction needs more free GPU memory. Close other GPU workloads and retry.')
    if result.returncode == 4:
        raise RuntimeError('A CUDA GPU must be available on the Studio host to refine a face.')
    if result.returncode:
        raise RuntimeError('TOPOS could not reconstruct the portrait. See the preparation log for details.')


def _run(portrait, destination):
    from gpu_runtime import select_gpu
    select_gpu()
    os.environ['ATTN_BACKEND'] = 'sdpa'
    os.chdir(SOURCE)
    sys.path.insert(0, str(SOURCE))
    import torch
    if not torch.cuda.is_available():
        return 4
    from thsolver.config import parse_args
    from train_dit import TOPOSDiTSolver
    torch.cuda.set_device(0)
    torch.set_float32_matmul_precision('high')
    with TemporaryDirectory(prefix='topos-', dir=destination.parent) as work:
        sys.argv = ['face-refine', '--config', 'configs/dit_test.yaml',
                    'SOLVER.logdir', Path(work).as_posix(),
                    'SOLVER.ckpt', (WEIGHTS / 'TOPOS-DiT.model.pth').as_posix(),
                    'MODEL.vae_ckpt', (WEIGHTS / 'TOPOS-VAE.model.pth').as_posix(),
                    'MODEL.image_cond_model_dir', (TOOL / 'dinov2').as_posix(),
                    'MODEL.image_cond_model_path', (WEIGHTS / 'dinov2_vitl14_reg4_pretrain.pth').as_posix()]
        try:
            with torch.inference_mode():
                solver = TOPOSDiTSolver(parse_args(backup=False))
                solver._prepare_for_inference()
                solver.model.eval()
                image = solver._load_image(str(portrait))
                template = solver._build_template_hgraph()
                torch.manual_seed(0)
                torch.cuda.manual_seed_all(0)
                mesh = solver.vae.decode_code(solver.image_cond_sample(image), template, template.depth)
                if not torch.isfinite(mesh).all().item():
                    raise ValueError('The face reconstruction contains invalid coordinates.')
                solver._save_pred('head', mesh)
        except torch.cuda.OutOfMemoryError:
            return 3
        (Path(work) / 'results/pred_mesh/head_pred.obj').replace(destination)
    return 0


if __name__ == '__main__':
    raise SystemExit(_run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()))
