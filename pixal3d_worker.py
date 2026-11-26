"""Studio worker for the optional single-image and multi-view Pixal3D pipelines."""
import gc
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import sys
import traceback

from pixal3d_runtime import (
    CAMERA_FOV, VIEW_FILES, SINGLE_IMAGE_SETUP_HINT, camera_matrix, load_manifest, runtime_root, runtime_status,
    source_path, validate_views, weight_path,
)
from gpu_runtime import configure_attention, probe, select_gpu
from studio_workspace import write

ROOT = Path(__file__).resolve().parent
RUNTIME = runtime_root(ROOT)
ATTR_LAYOUT = {'base_color': slice(0, 3), 'metallic': slice(3, 4),
               'roughness': slice(4, 5), 'alpha': slice(5, 6)}
SINGLE_DIFFUSION = 'diffusion_models/pixal3d_int8_convrot.safetensors'
MULTIVIEW_DIFFUSION = 'diffusion_models/pixal3d_multiview_int8_convrot.safetensors'
SINGLE_MAX_TOKENS = 49_152


def configure_runtime():
    select_gpu(ROOT)
    manifest = load_manifest(ROOT)
    # Comfy and Studio each have an app.py. Keep the isolated backend first.
    sys.path[:0] = [str(source_path(RUNTIME, manifest)), str(RUNTIME / 'deps'),
                   str(ROOT / 'engine' / 'o-voxel'), str(ROOT / 'engine')]
    os.environ.update({
        'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1',
        'OPENCV_IO_ENABLE_OPENEXR': '1',
        'TRELLIS_UV_RASTERIZER': 'torch',
        'U2NET_HOME': str(ROOT / 'models' / 'rembg'),
        'TORCHINDUCTOR_CACHE_DIR': str(RUNTIME / 'inductor-cache'),
        'TRITON_CACHE_DIR': str(RUNTIME / 'triton-cache'),
        'FLEX_GEMM_AUTOTUNE_CACHE_PATH': str(RUNTIME / 'flex-gemm-cache.json'),
    })
    # The shared Studio worker has already read its own command line.
    sys.argv = [sys.argv[0], '--lowvram', '--disable-dynamic-vram', '--use-pytorch-cross-attention',
                '--reserve-vram', '1.1', '--disable-smart-memory']
    import comfy.options
    comfy.options.enable_args_parsing()


def prepared_views(job, views, reviewed=False):
    from PIL import Image, ImageOps
    from reference_preparation import PREPARED_VIEWS, frame_pixal, pixal_silhouette, premultiplied_rgb

    if not reviewed:
        cutouts = {}
        session = None
        for view in views:
            with Image.open(job / view['file']) as original:
                image = ImageOps.exif_transpose(original).convert('RGBA')
            alpha = image.getchannel('A')
            if alpha.getextrema()[0] >= 128:
                if not (ROOT / 'models' / 'rembg' / 'u2net.onnx').is_file():
                    raise RuntimeError('Background removal needs the Studio U2Net model. Use transparent images or reinstall Studio.')
                from rembg import new_session, remove
                if session is None:
                    session = new_session('u2net', providers=['CPUExecutionProvider'])
                image = remove(image, session=session).convert('RGBA')
            cutouts[view['role']] = pixal_silhouette(image, view['role'])
        del session
        for role, frame in frame_pixal(cutouts).items():
            frame.save(job / PREPARED_VIEWS[role])

    for view in views:
        payload = (job / PREPARED_VIEWS[view['role']]).read_bytes()
        with Image.open(BytesIO(payload)) as frame:
            yield premultiplied_rgb(frame.convert('RGBA')), hashlib.sha256(payload).hexdigest()


def prepared_single_view(job, reviewed=False):
    import numpy as np
    from PIL import Image, ImageOps
    from reference_preparation import PREPARED_VIEWS, premultiplied_rgb, prepare_references

    path = job / PREPARED_VIEWS['front']
    if not reviewed:
        with Image.open(job / VIEW_FILES['front']) as original:
            image = ImageOps.exif_transpose(original).convert('RGBA')
        _, frames = prepare_references({'front': image}, {}, 'pixal3d_single', True,
                                       ROOT / 'models' / 'rembg' / 'u2net.onnx')
        frames['front'].save(path)
    payload = path.read_bytes()
    with Image.open(BytesIO(payload)) as frame:
        if frame.mode != 'RGBA' or frame.width != frame.height or not 16 <= frame.width <= 2048:
            raise ValueError('The prepared Pixal3D image must be square with transparency.')
        rgb = (premultiplied_rgb(frame) * 255).astype(np.uint8).astype(np.float32) / 255
        return rgb, hashlib.sha256(payload).hexdigest()


def load_moge_estimator(torch):
    from comfy.ldm.moge.model import MoGeModel

    checkpoint = torch.load(RUNTIME / 'models' / 'depth' / 'moge-2-vitl.pt',
                            map_location='cpu', weights_only=True, mmap=True)
    estimator = MoGeModel(checkpoint['model'])
    if estimator.version != 'v2':
        raise ValueError('The installed Pixal3D camera estimator must be MoGe-2.')
    return estimator


def estimate_single_camera(torch, images):
    import comfy.model_management as mm

    estimator = load_moge_estimator(torch)
    estimate = estimator.infer(images.movedim(-1, -3).contiguous(), resolution_level=9)
    intrinsics = estimate['intrinsics'][0].float().cpu()
    del estimator, estimate
    mm.unload_all_models()
    gc.collect()
    mm.soft_empty_cache()
    focal = float(intrinsics[0, 0])
    if not math.isfinite(focal) or focal <= 0:
        raise ValueError('MoGe could not estimate a positive horizontal focal length.')
    fov = 2 * math.atan(0.5 / focal)
    if not math.radians(2.) <= fov <= math.radians(160.):
        raise ValueError('MoGe estimated an unusable field of view. Try a different reference.')
    transform = camera_matrix(0)
    transform[1][3] = -focal
    return torch.tensor([fov], dtype=torch.float32), torch.tensor([transform], dtype=torch.float32)


def sampling_options(options, seed, settings_version=2):
    # Match the tested structure/shape seed split, with independent materials.
    # Recipes through v2 used 12 high-resolution steps regardless of shape_steps.
    # Preserve those recipes; new jobs apply the shape control to both passes.
    high_steps = options['shape_steps'] if settings_version >= 3 else 12
    texture_seed = options['texture_seed']
    return {
        'structure': {'seed': (seed + 14) % (2**31), 'steps': options['structure_steps']},
        'shape_low': {'seed': seed, 'steps': options['shape_steps']},
        'shape_high': {'seed': seed, 'steps': high_steps},
        'texture': {'seed': texture_seed if texture_seed >= 0 else (seed + 1) % (2**31),
                    'steps': options['texture_steps']},
    }


def generate_source(torch, job, views, options, sampling, status, reviewed=False, *, single_image=False):
    import comfy.clip_vision
    import comfy.model_management as mm
    import comfy.sd
    import comfy.utils
    import numpy as np
    from nodes import common_ksampler
    from comfy_extras.nodes_custom_sampler import CFGOverride
    from comfy_extras.nodes_model_advanced import ModelSamplingSD3, RescaleCFG
    from comfy_extras.nodes_trellis2 import (
        EmptyTrellis2LatentStructure, Trellis2ShapeStage, Trellis2TextureStage,
        Trellis2UpsampleStage, VaeDecodeShapeTrellis, VaeDecodeStructureTrellis2,
        VaeDecodeTextureTrellis, _build_pixal3d_conditioning,
    )

    def model_file(relative):
        return str(weight_path(ROOT, relative))

    def vae(filename):
        state, metadata = comfy.utils.load_torch_file(model_file('vae/' + filename), return_metadata=True)
        model = comfy.sd.VAE(sd=state, metadata=metadata)
        model.throw_exception_if_invalid()
        return model

    def sample(model, positive, negative, latent, pass_name, guidance, scheduler='normal'):
        recipe = sampling[pass_name]
        return common_ksampler(model, recipe['seed'], recipe['steps'], guidance,
                               'euler', scheduler, positive, negative, latent)[0]

    if single_image:
        status('running', 'Estimating a single camera with MoGe-2', 4)
        pixels, fingerprint = prepared_single_view(job, reviewed)
        images = torch.from_numpy(pixels).unsqueeze(0)
        fingerprints = [fingerprint]
        fov, matrices = estimate_single_camera(torch, images)
    else:
        status('running', 'Preparing Pixal3D reference views', 4)
        prepared = list(prepared_views(job, views, reviewed))
        images = torch.from_numpy(np.stack([pixels for pixels, _ in prepared]))
        fingerprints = [fingerprint for _, fingerprint in prepared]
        del prepared
        matrices = torch.tensor([camera_matrix(view['azimuth']) for view in views], dtype=torch.float32)
        fov = torch.full((len(views),), CAMERA_FOV)
    scale = torch.tensor([1.], dtype=torch.float32)
    calibration = [dict(role=view['role'], azimuth=view['azimuth'], sha256=hash_value,
                        camera_angle_x=angle, transform_matrix=matrix)
                   for view, hash_value, angle, matrix in zip(views, fingerprints, fov.tolist(), matrices.tolist(), strict=True)]
    status('running', 'Encoding Pixal3D reference views', 12)
    vision = comfy.clip_vision.load(model_file('clip_vision/dino_v3_L_naf_fp32.safetensors'))
    positive, negative = _build_pixal3d_conditioning(
        vision, images, matrices, fov, scale, num_views=len(views),
    ).result
    del vision, images
    mm.unload_all_models()
    mm.soft_empty_cache()

    status('running', 'Loading Pixal3D model', 22)
    model = comfy.sd.load_diffusion_model(
        model_file(SINGLE_DIFFUSION if single_image else MULTIVIEW_DIFFUSION),
        disable_dynamic=True,
    )
    if model is None:
        raise RuntimeError('The installed Pixal3D checkpoint could not be loaded.')
    shape_vae = vae('trellis_2_shape_vae_bf16.safetensors')
    texture_vae = vae('trellis_2_texture_vae_bf16.safetensors')

    status('running', 'Building Pixal3D volume', 28)
    structure = ModelSamplingSD3().patch(model, options['structure_rescale_t'])[0]
    structure = RescaleCFG().patch(structure, options['structure_rescale'])[0]
    structure = CFGOverride.execute(structure, 1., .667, 1.).result[0]
    latent = EmptyTrellis2LatentStructure.execute(1).result[0]
    initial = sample(structure, positive, negative, latent, 'structure', options['structure_guidance'])
    voxel = VaeDecodeStructureTrellis2.execute(initial, shape_vae, 32).result[0]

    status('running', 'Reconstructing Pixal3D geometry', 44)
    shape = ModelSamplingSD3().patch(model, options['shape_rescale_t'])[0]
    shape = RescaleCFG().patch(shape, options['shape_rescale'])[0]
    shape = CFGOverride.execute(shape, 1., .769, 1.).result[0]
    positive_low, negative_low, latent_low = Trellis2ShapeStage.execute(positive, negative, voxel).result
    low = sample(shape, positive_low, negative_low, latent_low, 'shape_low', options['shape_guidance'])
    positive_high, negative_high, latent_high = Trellis2UpsampleStage.execute(
        positive_low, negative_low, low, shape_vae, 1024,
    ).result
    if single_image and int(latent_high['coord_counts'].max()) > SINGLE_MAX_TOKENS:
        raise ValueError('The single-image 1024 cascade exceeded its token budget. Try a less detailed reference.')
    high = sample(shape, positive_high, negative_high, latent_high, 'shape_high',
                  options['shape_guidance'], 'simple')
    mesh, subdivisions = VaeDecodeShapeTrellis.execute(high, shape_vae).result
    vertices = mesh.vertices[0].float().cpu().contiguous()
    faces = mesh.faces[0].int().cpu().contiguous()

    status('running', 'Generating Pixal3D materials', 66)
    texture = ModelSamplingSD3().patch(model, options['texture_rescale_t'])[0]
    if options['texture_rescale']:
        texture = RescaleCFG().patch(texture, options['texture_rescale'])[0]
    positive_tex, negative_tex, latent_tex = Trellis2TextureStage.execute(positive_high, negative_high, high).result
    texture_latent = sample(texture, positive_tex, negative_tex, latent_tex,
                            'texture', options['texture_guidance'])
    color = VaeDecodeTextureTrellis.execute(texture_latent, texture_vae, subdivisions).result[0]
    return {
        'vertices': vertices, 'faces': faces, 'attr_volume': color.voxel_colors.float().cpu().contiguous(),
        'coords': color.data[:, -3:].int().cpu().contiguous(), 'attr_layout': ATTR_LAYOUT,
        'voxel_size': 1. / color.resolution, 'resolution': int(color.resolution),
        'coordinate_system': 'y_up',
    }, calibration


def check_runtime(single_image=False):
    configure_runtime()
    import comfy.clip_vision
    import comfy.sd
    from comfy_extras.nodes_trellis2 import _build_pixal3d_conditioning
    from nodes import common_ksampler
    import cumesh
    import o_voxel
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is unavailable in the Studio environment.')
    expected = ROOT / 'engine' / 'o-voxel'
    if not Path(o_voxel.__file__).resolve().is_relative_to(expected.resolve()):
        raise RuntimeError('Studio O-Voxel patches are unavailable.')
    if single_image:
        from comfy_extras.nodes_trellis2 import Pixal3DConditioning

        if not weight_path(ROOT, SINGLE_DIFFUSION).is_file():
            raise RuntimeError(SINGLE_IMAGE_SETUP_HINT)
        load_moge_estimator(torch)
    probe()
    print('Pixal3D runtime imports verified on ' + torch.cuda.get_device_name(), flush=True)


def run_job():
    # Shared finishing writes the normal Studio GLB and uses standard job telemetry.
    from worker import (JOB, finish_mesh, options, project_requested_face, refine_multiview_texture,
                        save_finishing_source, settings, started, status)
    import time

    single_image = settings.get('view_mode') == 'single'
    try:
        if single_image:
            if not runtime_status(ROOT)['single_image']['ready']:
                raise RuntimeError(SINGLE_IMAGE_SETUP_HINT)
            views = [{'role': 'front', 'azimuth': 0, 'file': VIEW_FILES['front']}]
        else:
            views = validate_views(settings['views'])
        if settings.get('resolution') != 1024:
            raise ValueError('Pixal3D generation uses the 1024 cascade.')
        configure_runtime()
        import comfy.model_management as mm
        import torch
        from api_spz.core.state_manage import _apply_patches

        torch.set_num_threads(6)
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable in this environment.')
        configure_attention(torch)
        torch.cuda.reset_peak_memory_stats()
        settings_version = settings.get('settings_version', 2)
        sampling = sampling_options(options, settings['seed'], settings_version)
        with torch.inference_mode():
            source, calibration = generate_source(torch, JOB, views, options, sampling, status,
                                                  reviewed='reference_edits' in settings, single_image=single_image)
        mm.unload_all_models()
        mm.soft_empty_cache()
        gc.collect()
        _apply_patches()
        if settings.get('keep_finishing_source'):
            save_finishing_source(torch, source)
        result = finish_mesh(torch, source)
        resolution, raw_triangles = source['resolution'], len(source['faces'])
        del source
        multiview_report = refine_multiview_texture(torch, result, pixal=True)
        face_report = project_requested_face()
        finishing = result.metadata['finishing']
        from generation_options import resolve_texture_method
        texture_method = resolve_texture_method(settings.get('texture_method'), settings.get('multiview_texture_refinement'))
        if multiview_report is not None and texture_method == 'multiview':
            finishing['multiview_texture_refinement'] = multiview_report
        status('running', 'Finalizing model', 99)
        with (JOB / 'model.glb').open('rb') as model_file:
            finishing['model_sha256'] = hashlib.file_digest(model_file, 'sha256').hexdigest()
        from generation_view_projection import save_generation_views
        try:
            if single_image:
                save_generation_views(JOB, finishing['normalization'], calibration, camera_model='moge')
            else:
                save_generation_views(JOB, finishing['normalization'], calibration)
        except (OSError, ValueError) as error:
            print(f'Could not save the optional generation camera provenance ({type(error).__name__}).', flush=True)
        stats = dict(
            name=settings['name'], height_m=settings['height_m'], seed=settings['seed'],
            triangles=len(result.faces), vertices=len(result.vertices), engine='Pixal3D',
            dimensions_m=result.extents.tolist(), resolution=resolution,
            requested_resolution=1024, pipeline_type='pixal3d_1024_monoview' if single_image else 'pixal3d_1024_multiview',
            texture_size=settings['texture_size'], generation_options=options, sampler_options=sampling,
            texture_method=texture_method,
            settings_version=settings_version, neural_inferences=1, raw_triangles=raw_triangles,
            view_count=len(views), views=views, view_mode='single' if single_image else 'multi',
            peak_vram_gib=round(torch.cuda.max_memory_allocated()/1024**3, 2),
            peak_reserved_gib=round(torch.cuda.max_memory_reserved()/1024**3, 2),
            seconds=round(time.time()-started, 1), finishing=finishing,
        )
        if single_image:
            camera = calibration[0]
            stats['camera_estimation'] = {
                'method': 'MoGe-2', 'horizontal_fov_degrees': round(math.degrees(camera['camera_angle_x']), 2),
            }
        if face_report is not None:
            stats['face_projection'] = face_report
        if 'adaptive_finishing' in result.metadata:
            stats['adaptive_finishing'] = result.metadata['adaptive_finishing']
        if 'surface_repair' in result.metadata:
            stats['surface_repair'] = result.metadata['surface_repair']
        write(JOB / 'result.json', stats)
        ready = 'Pixal3D model ready'
        if multiview_report and multiview_report['state'] == 'skipped':
            ready += ' · multiview texture pass skipped'
        status('done', ready, 100, result=stats)
    except Exception as error:
        traceback.print_exc()
        message = str(error)
        if 'out of memory' in message.lower():
            message = ('Insufficient GPU memory. Close other GPU applications or select TRELLIS.2.'
                       if single_image else
                       'Insufficient GPU memory. Close other GPU applications or use fewer Pixal3D views, then retry.')
        status('error', 'Pixal3D generation failed', 0, error=message[:1400])
        return 1
    return 0


if __name__ == '__main__':
    if sys.argv[1:] in (['--check-runtime'], ['--check-single-image-runtime']):
        check_runtime(sys.argv[1] == '--check-single-image-runtime')
    elif len(sys.argv) == 2:
        sys.exit(run_job())
    else:
        sys.exit('Usage: pixal3d_worker.py <job-folder> | --check-runtime | --check-single-image-runtime')
