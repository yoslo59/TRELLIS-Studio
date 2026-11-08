"""Studio worker for the optional calibrated Pixal3D character pipeline."""
import gc
import json
import os
from pathlib import Path
import sys
import traceback

from pixal3d_runtime import CAMERA_FOV, camera_matrix, load_manifest, runtime_root, source_path, validate_views
from studio_workspace import write

ROOT = Path(__file__).resolve().parent
RUNTIME = runtime_root(ROOT)
ATTR_LAYOUT = {'base_color': slice(0, 3), 'metallic': slice(3, 4),
               'roughness': slice(4, 5), 'alpha': slice(5, 6)}


def configure_runtime():
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


def prepared_views(job, views):
    import numpy as np
    from PIL import Image, ImageOps

    cutouts = []
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
            alpha = image.getchannel('A')
        bounds = alpha.point(lambda value: 255 if value >= 128 else 0).getbbox()
        if bounds is None or bounds[2] - bounds[0] < 16 or bounds[3] - bounds[1] < 16:
            raise ValueError(f"No usable silhouette in the {view['role']} view.")
        cutouts.append(image.crop(bounds))
    del session
    # Use the same body height in every view. Widen the framing of all views
    # together if a subject's arm span would otherwise leave the square.
    height = min(920, min(944 * image.height // image.width for image in cutouts))
    if height < 128:
        raise ValueError('The reference silhouette is too wide. Use a full-body character image.')
    for image, view in zip(cutouts, views):
        width = max(1, round(image.width * height / image.height))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', (1024, 1024))
        canvas.paste(image, ((1024 - width) // 2, (1024 - height) // 2))
        filename = 'prepared.png' if view['role'] == 'front' else f"prepared-{view['role']}.png"
        canvas.save(job / filename)
        rgba = np.asarray(canvas, dtype=np.float32) / 255
        yield rgba[..., :3] * rgba[..., 3:4]


def sampling_options(options, seed):
    # Match the tested structure/shape seed split, with independent materials.
    texture_seed = options['texture_seed']
    return {
        'structure': {'seed': (seed + 14) % (2**31), 'steps': options['structure_steps']},
        'shape_low': {'seed': seed, 'steps': options['shape_steps']},
        'shape_high': {'seed': seed, 'steps': 12},
        'texture': {'seed': texture_seed if texture_seed >= 0 else (seed + 1) % (2**31),
                    'steps': options['texture_steps']},
    }


def generate_source(torch, job, views, options, sampling, status):
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
        return str(RUNTIME / 'models' / relative)

    def vae(filename):
        state, metadata = comfy.utils.load_torch_file(model_file('vae/' + filename), return_metadata=True)
        model = comfy.sd.VAE(sd=state, metadata=metadata)
        model.throw_exception_if_invalid()
        return model

    def sample(model, positive, negative, latent, pass_name, guidance, scheduler='normal'):
        recipe = sampling[pass_name]
        return common_ksampler(model, recipe['seed'], recipe['steps'], guidance,
                               'euler', scheduler, positive, negative, latent)[0]

    status('running', 'Preparing Pixal3D reference views', 4)
    images = torch.from_numpy(np.stack(list(prepared_views(job, views))))
    matrices = torch.tensor([camera_matrix(view['azimuth']) for view in views], dtype=torch.float32)
    fov = torch.full((len(views),), CAMERA_FOV)
    scale = torch.tensor([1.], dtype=torch.float32)
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
        model_file('diffusion_models/pixal3d_multiview_int8_convrot.safetensors'),
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
    }


def check_runtime():
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
    print('Pixal3D runtime imports verified on ' + torch.cuda.get_device_name(), flush=True)


def run_job():
    # Shared finishing writes the normal Studio GLB and uses standard job telemetry.
    from worker import (JOB, finish_mesh, options, project_requested_face, save_finishing_source,
                        settings, started, status)
    import time

    try:
        views = validate_views(settings['views'])
        if settings.get('resolution') != 1024:
            raise ValueError('Pixal3D character generation uses the 1024 cascade.')
        configure_runtime()
        import comfy.model_management as mm
        import torch
        from api_spz.core.state_manage import _apply_patches

        torch.set_num_threads(6)
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable in this environment.')
        torch.cuda.reset_peak_memory_stats()
        sampling = sampling_options(options, settings['seed'])
        with torch.inference_mode():
            source = generate_source(torch, JOB, views, options, sampling, status)
        mm.unload_all_models()
        mm.soft_empty_cache()
        gc.collect()
        _apply_patches()
        if settings.get('keep_finishing_source'):
            save_finishing_source(torch, source)
        result = finish_mesh(torch, source)
        face_report = project_requested_face()
        stats = dict(
            name=settings['name'], height_m=settings['height_m'], seed=settings['seed'],
            triangles=len(result.faces), vertices=len(result.vertices), engine='Pixal3D',
            dimensions_m=result.extents.tolist(), resolution=source['resolution'],
            requested_resolution=1024, pipeline_type='pixal3d_1024_multiview',
            texture_size=settings['texture_size'], generation_options=options, sampler_options=sampling,
            settings_version=2, neural_inferences=1, raw_triangles=len(source['faces']),
            view_count=len(views), views=views,
            peak_vram_gib=round(torch.cuda.max_memory_allocated()/1024**3, 2),
            peak_reserved_gib=round(torch.cuda.max_memory_reserved()/1024**3, 2),
            seconds=round(time.time()-started, 1),
        )
        if face_report is not None:
            stats['face_projection'] = face_report
        write(JOB / 'result.json', stats)
        status('done', 'Pixal3D model ready', 100, result=stats)
    except Exception as error:
        traceback.print_exc()
        message = str(error)
        if 'out of memory' in message.lower():
            message = 'Insufficient GPU memory. Close other GPU applications or use fewer Pixal3D views, then retry.'
        status('error', 'Pixal3D generation failed', 0, error=message[:1400])
        return 1
    return 0


if __name__ == '__main__':
    if sys.argv[1:] == ['--check-runtime']:
        check_runtime()
    elif len(sys.argv) == 2:
        sys.exit(run_job())
    else:
        sys.exit('Usage: pixal3d_worker.py <job-folder> or --check-runtime')
