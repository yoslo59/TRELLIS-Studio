"""Standalone projected or global TRELLIS.2 shape generation for the Studio recipes."""

from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory

from studio_model_assets import model_asset_path


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config/native-pixal3d.json'
CHECKPOINTS = {
    'pixal3d': 'diffusion_models/pixal3d_int8_convrot.safetensors',
    'trellis2': 'diffusion_models/trellis_2_int8_convrot.safetensors',
}
SHAPE_VAE = 'vae/trellis_2_shape_vae_bf16.safetensors'
VISION = 'clip_vision/dino_v3_L_naf_fp32.safetensors'
BIREFNET = 'background_removal/birefnet.safetensors'
MOGE = 'geometry_estimation/moge_2_vitl_normal_fp16.safetensors'


def _config(backend, *, mask_only=False):
    if backend not in CHECKPOINTS:
        raise ValueError('Choose TRELLIS.2 or PiXal3D for geometry.')
    try:
        data = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
        keys = ('python', 'weights', 'birefnet') if mask_only else ('python', 'architecture', 'weights', 'birefnet', 'moge')
        paths = {key: (CONFIG.parent / data[key]).resolve(strict=True) for key in keys}
        paths['dependencies'] = [(CONFIG.parent / path).resolve(strict=True) for path in data['dependencies']]
        paths['environment'] = {key: str((CONFIG.parent / value).resolve(strict=True))
                                for key, value in data.get('environment', {}).items()
                                if key in ('CC', 'CUDA_PATH', 'TRITON_CACHE_DIR')}
    except (OSError, ValueError, AttributeError, KeyError, TypeError) as error:
        action = 'BiRefNet foreground-mask runtime' if mask_only else 'shape runtime and its local weights'
        raise ValueError(f'Configure the standalone {action}.') from error
    if mask_only:
        if not paths['python'].is_file() or not (paths['birefnet'] / 'birefnet.py').is_file():
            raise ValueError('The standalone BiRefNet Python runtime or source is missing.')
        if not (paths['weights'] / BIREFNET).is_file():
            raise ValueError('The pinned local BiRefNet foreground checkpoint is missing.')
        return paths
    weights = [CHECKPOINTS[backend], SHAPE_VAE, VISION, BIREFNET] + ([MOGE] if backend == 'pixal3d' else [])
    paths['weight_files'] = {name: model_asset_path(ROOT, 'native-pixal3d', name, directory=paths['weights'])
                             for name in weights}
    if not all(weight.is_file() for weight in paths['weight_files'].values()):
        raise ValueError('A local checkpoint is missing for the selected geometry engine.')
    if not (paths['architecture'] / 'native_shape_architecture/model.py').is_file():
        raise ValueError('The portable shape architecture is missing.')
    return paths


def runtime_status(backend='pixal3d'):
    try:
        _config(backend)
        record = json.loads((CONFIG.parent / 'native-pixal3d-verified.json').read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get(backend) is not True:
            raise ValueError('The selected standalone geometry engine is still being verified.')
    except (ValueError, OSError) as error:
        return {'available': False, 'reason': str(error) if isinstance(error, ValueError)
                else 'The standalone geometry engines are still being verified.'}
    return {'available': True, 'reason': ''}


def texture_mask_status():
    try:
        _config('pixal3d', mask_only=True)
    except ValueError as error:
        return {'available': False, 'reason': str(error)}
    return {'available': True, 'reason': ''}


def _offline_environment():
    return dict(os.environ, HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TRANSFORMERS_OFFLINE='1')


def _activate_dependencies(paths):
    directories = [paths['architecture']] if 'architecture' in paths else []
    directories.extend(paths['dependencies'])
    # Configured package overlays override the base dependency directory in the same order as shape generation.
    for folder in directories:
        sys.path.insert(0, str(folder))


def prepare_texture_mask(image_path, output_path):
    """Prepare the UltraTex foreground mask without loading any shape weights or using CUDA."""
    image_path = Path(image_path).resolve(strict=True)
    output = Path(output_path).resolve()
    if output.suffix.lower() != '.npy' or output == image_path:
        raise ValueError('Write the texture foreground mask to a separate NPY file.')
    paths = _config('pixal3d', mask_only=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='texture-mask-', dir=output.parent) as directory:
        scratch = Path(directory) / 'foreground.npy'
        command = [str(paths['python']), '-B', '-u', str(ROOT / 'native_pixal3d.py'), str(image_path),
                   str(scratch), '--mask-only']
        env = dict(_offline_environment(), CUDA_VISIBLE_DEVICES='-1')
        process = subprocess.run(command, cwd=directory, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding='utf-8', errors='replace',
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if process.stdout:
            print(process.stdout, end='', flush=True)
        if process.returncode or not scratch.is_file():
            raise RuntimeError('UltraTex foreground preparation failed. See the generation log.')
        scratch.replace(output)
    return output


def generate_pixal3d_geometry(image_path, options=None, progress=None, *, backend='pixal3d', job_dir=None,
                              texture_mask=None):
    """Return model-space Y-up vertices and faces before any remeshing."""
    import numpy as np

    paths = _config(backend)
    with TemporaryDirectory(prefix='native-shape-', dir=job_dir) as scratch:
        scratch = Path(scratch)
        arguments = scratch / 'options.json'
        arguments.write_text(json.dumps(options or {}), encoding='utf-8')
        output = scratch / 'geometry.npz'
        command = [str(paths['python']), '-B', '-u', str(ROOT / 'native_pixal3d.py'), str(Path(image_path).resolve()),
                   str(output), '--backend', backend, '--options', str(arguments)]
        if texture_mask is not None:
            command += ['--mask-output', str(Path(texture_mask).resolve())]
        with subprocess.Popen(command, cwd=scratch, env=_offline_environment(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace',
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            try:
                for line in process.stdout:
                    if line.startswith('GEOMETRY_PROGRESS ') and progress is not None:
                        label, percent = json.loads(line.removeprefix('GEOMETRY_PROGRESS '))
                        progress(label, percent)
                    else:
                        print(line, end='', flush=True)
                if process.wait() or not output.is_file():
                    raise RuntimeError('Standalone geometry generation failed. See the generation log.')
            finally:
                if process.poll() is None:
                    process.terminate()
        with np.load(output, allow_pickle=False) as arrays:
            return arrays['vertices'], arrays['faces']


def _compute(image_path, paths, backend, options, report, mask_output=None, trace=None):
    os.environ.update(paths['environment'])
    _activate_dependencies(paths)

    import gc
    from time import perf_counter

    import numpy as np
    import torch
    from torch import nn
    from torch.nn import functional as F
    from native_pixal_layers import flow_operations, load_flow_weights
    from native_shape_architecture.model import SLatFlowModel, SparseStructureFlowModel, Trellis2
    from native_shape_architecture.vae import ShapeVae, SparseTensor
    from native_game_asset import prepare_reference, estimate_camera
    from native_game_conditioning import conditioning_image, encode_dinov3, load_dinov3_naf, project_front_features
    from native_game_sampling import CHARACTER_STAGES, SamplingStage, sample_flow

    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is required for the standalone shape models.')
    from comfy_kitchen.registry import registry
    registry.disable('cuda')
    torch.set_num_threads(8)
    torch.set_num_interop_threads(1)
    device = torch.device('cuda')
    dtype = torch.bfloat16
    projected = backend == 'pixal3d'
    weights = paths['weight_files']
    checkpoint = weights[CHECKPOINTS[backend]]
    started = perf_counter()
    print(f'Native {backend} shape generation using Torch {torch.__version__}', flush=True)

    def release():
        gc.collect()
        torch.cuda.empty_cache()

    def stage(index, defaults):
        node_id = ('3', '18', '23')[index]
        switch_id = ('199', '279', '279')[index]
        rescale_id = ('125', '126', '126')[index]
        value = options.get(node_id, {})
        override = options.get(switch_id, {})
        return SamplingStage(
            int(value.get('seed', defaults.seed)), int(value.get('steps', defaults.steps)),
            value.get('scheduler', defaults.scheduler),
            float(options.get('108', {}).get('shift', defaults.shift)) if index == 0 else defaults.shift,
            float(value.get('cfg', defaults.cfg)), float(options.get(rescale_id, {}).get('multiplier', defaults.rescale)),
            float(override.get('start_percent', defaults.cfg_switch)),
            float(override.get('end_percent', 1.0)), float(override.get('cfg', 1.0)),
            value.get('sampler_name', defaults.sampler), float(value.get('denoise', defaults.denoise)),
        )

    @torch.inference_mode()
    def vae_load(included):
        with torch.device('meta'):
            vae = ShapeVae()
        delattr(vae, 'shape_dec' if included == 'struct_dec' else 'struct_dec')
        vae.resolution = torch.tensor(1024.0)
        from safetensors import safe_open
        with safe_open(weights[SHAPE_VAE], framework='pt', device='cpu') as source:
            state = {key: source.get_tensor(key).to(device, dtype=torch.float16)
                     for key in source.keys() if key.startswith(included + '.')}
        vae.load_state_dict(state, strict=True, assign=True)
        return vae.eval()

    @torch.inference_mode()
    def run_flow(index, shape, context, projection, coords=None):
        model_options = dict(model_channels=1536, cond_channels=1024, num_blocks=30, num_heads=12, mlp_ratio=5.3334,
                             share_mod=True, qk_rms_norm=True, qk_rms_norm_cross=True, dtype=dtype,
                             device=torch.device('cpu'), operations=flow_operations,
                             image_attn_mode='proj' if projected else 'global',
                             proj_in_channels=(1024 if index == 0 else 2048) if projected else None)
        architecture = Trellis2.__new__(Trellis2)
        nn.Module.__init__(architecture)
        architecture.image_attn_mode_structure = model_options['image_attn_mode']
        architecture.image_attn_mode_shape = model_options['image_attn_mode']
        prefix = ('structure_model', 'img2shape_512', 'img2shape')[index]
        if index == 0:
            model = SparseStructureFlowModel(16, 8, out_channels=8, **model_options)
        else:
            model = SLatFlowModel(32 if index == 1 else 64, 32, out_channels=32, **model_options)
        report(f'Loading native {prefix} weights', 30 + index * 15)
        setattr(architecture, prefix, load_flow_weights(model, checkpoint, f'model.{prefix}.', device=device, dtype=dtype))
        coord_counts = torch.tensor([len(coords)], device=device) if coords is not None else None
        positive = context.to(device=device, dtype=dtype)
        desired = stage(index, CHARACTER_STAGES[index])

        def velocity(value, timestep, needs_unconditional):
            inputs, conditions = value, positive
            slots = [0]
            if needs_unconditional:
                inputs = torch.cat((value, value))
                timestep = timestep.repeat(2)
                conditions = torch.cat((positive, torch.zeros_like(positive)))
                slots = [0, 1]
            result = architecture(inputs.to(dtype), timestep.float(), conditions, embeds=conditions,
                                  transformer_options={'cond_or_uncond': slots},
                                  trellis2_generation_mode=('structure_generation', 'shape_generation_512',
                                                            'shape_generation')[index],
                                  trellis2_coords=coords, trellis2_coord_counts=coord_counts,
                                  trellis2_proj_feats=projection).float()
            return (result[:1], result[1:]) if needs_unconditional else (result, None)

        label = ('Sampling the sparse structure', 'Sampling the first shape latent',
                 'Sampling the high-resolution shape latent')[index]
        initial = torch.zeros(shape, device=device, dtype=torch.float32)
        if not torch.isfinite(positive).all() or projection is not None and not torch.isfinite(projection).all():
            raise RuntimeError(f'Image conditioning is non-finite in the {prefix} stage.')
        sampled = sample_flow(desired, initial, velocity,
                              progress=lambda current, total: report(f'{label} ({current}/{total})',
                                                                     32 + index * 15 + int(11 * current / total)))
        if not torch.isfinite(sampled).all():
            raise RuntimeError(f'The {prefix} sampler produced non-finite latents.')
        return sampled

    def full_tokens(features):
        patches = features.patches.flatten(2).transpose(1, 2)
        return torch.cat((features.global_tokens, patches), dim=1)

    def sampling_features(features, naf_map, coord_resolution, coords=None):
        if not projected:
            return None
        image_size = 512 if features is vision512 else 1024
        locations = coords[:, 1:] if coords is not None else None
        low = project_front_features(features.patches, fov, image_size, coord_resolution, sparse_xyz=locations)
        if naf_map is None:
            return low.unsqueeze(0).to(dtype)
        high = project_front_features(naf_map, fov, image_size, coord_resolution, sparse_xyz=locations)
        return torch.cat((low, high), dim=-1).to(dtype)

    # Shape-latent distribution from the pinned TRELLIS.2 checkpoint.
    mean = [
        0.781296, 0.018091, -0.495192, -0.558457, 1.060530, 0.093252, 1.518149, -0.933218,
        -0.732996, 2.604095, -0.118341, -2.143904, 0.495076, -2.179512, -2.130751, -0.996944,
        0.261421, -2.217463, 1.260067, -0.150213, 3.790713, 1.481266, -1.046058, -1.523667,
        -0.059621, 2.220780, 1.621212, 0.877230, 0.567247, -3.175944, -3.186688, 1.578665]
    std = [
        5.972266, 4.706852, 5.445010, 5.209927, 5.320220, 4.547237, 5.020802, 5.444004,
        5.226681, 5.683095, 4.831436, 5.286469, 5.652043, 5.367606, 5.525084, 4.730578,
        4.805265, 5.124013, 5.530808, 5.619001, 5.103930, 5.417670, 5.269677, 5.547194,
        5.634698, 5.235274, 6.110351, 5.511298, 6.237273, 4.879207, 5.347008, 5.405691]

    def denormalize(latent, coords):
        features = latent[0, :, :, 0].T.contiguous()
        features = (features * features.new_tensor(std) + features.new_tensor(mean)).half()
        return SparseTensor(feats=features, coords=coords)

    with torch.inference_mode():
        reference = prepare_reference(image_path, weights[BIREFNET], paths['birefnet'], device=device, progress=report,
                                      crop_options=options.get('312'))
        if mask_output is not None:
            np.save(mask_output, reference.foreground_mask.astype(np.float32, copy=False), allow_pickle=False)
        release()
        fov = None
        if projected:
            controls = options.get('56', {})
            fov = float(controls.get('fov_x_degrees', 0))
            if not fov:
                camera = estimate_camera(reference.conditioning_rgb, weights[MOGE], paths['moge'], device=device,
                                         progress=report, resolution_level=controls.get('resolution_level', 9))
                fov = camera.horizontal_fov_degrees
            print(f'PiXal3D horizontal FOV {fov:.8f} degrees', flush=True)
            release()
        report('Extracting DINOv3 image features', 21)
        # The reference ViT receives normalized FP32 pixels and casts its
        # visual weights to the activation dtype at each operation.
        dino, naf = load_dinov3_naf(weights[VISION], device=device, dino_dtype=torch.float32)
        image512 = conditioning_image(reference.conditioning_rgb, 512, device=device)
        image1024 = conditioning_image(reference.conditioning_rgb, 1024, device=device)
        vision512 = encode_dinov3(dino, image512)
        vision1024 = encode_dinov3(dino, image1024)
        del dino
        release()
        naf512 = naf(image512, vision512.patches, (512, 512)) if projected else None
        naf1024 = naf(image1024, vision1024.patches, (512, 512)) if projected else None
        del naf
        release()
        context512 = vision512.global_tokens if projected else full_tokens(vision512)
        context1024 = vision1024.global_tokens if projected else full_tokens(vision1024)
        ss_projection = sampling_features(vision512, None, 16)
        latent = run_flow(0, (1, 32, 16, 16, 16), context512, ss_projection)
        if trace is not None:
            torch.save(dict(global512=context512.float().cpu(), global1024=context1024.float().cpu(),
                            projection=ss_projection.float().cpu() if ss_projection is not None else None,
                            input=torch.from_numpy(reference.conditioning_rgb.copy()).div(255).unsqueeze(0),
                            angle=fov, structure={'samples': latent.cpu()}), trace / 'structure.pt')
        release()
        report('Decoding the sparse structure', 44)
        decoder = vae_load('struct_dec')
        voxel_size = int(options.get('119', {}).get('resolution', 32))
        occupancy = decoder.decode_structure(latent[:, :8].half()) > 0
        if occupancy.shape[2] != voxel_size:
            if voxel_size < 1 or occupancy.shape[2] < voxel_size or occupancy.shape[2] % voxel_size:
                raise ValueError('The requested structure grid is incompatible with the geometry decoder.')
            stride = occupancy.shape[2] // voxel_size
            occupancy = F.max_pool3d(occupancy.float(), stride, stride) > 0.5
        coords = torch.argwhere(occupancy)[:, [0, 2, 3, 4]].int()
        if not len(coords):
            raise RuntimeError('The structure stage produced no occupied voxels.')
        if trace is not None:
            torch.save(coords.cpu(), trace / 'structure-coords.pt')
        print(f'Structure grid {voxel_size} with {len(coords)} occupied voxels', flush=True)
        del decoder, latent, occupancy
        release()

        first_map = sampling_features(vision512, naf512, voxel_size, coords)
        shape = run_flow(1, (1, 32, len(coords), 1), context512, first_map, coords)
        if trace is not None:
            torch.save(dict(samples=shape.cpu(), coords=coords.cpu()), trace / 'shape512.pt')
        release()
        decoder = vae_load('shape_dec')
        report('Subdividing the shape latent with the VAE', 60)
        high = decoder.upsample_shape(denormalize(shape, coords), 4)
        resolution = int(options.get('94', {}).get('target_resolution', 1536))
        if resolution < 1024 or resolution > 2048 or resolution % 128:
            raise ValueError('Shape upsampling resolution must be between 1024 and 2048 in increments of 128.')
        grid = resolution // 16
        spatial = (high[:, 1:].float() + 0.5) * ((grid - 1 if projected else grid) / 512)
        if projected:
            spatial.round_()
        coords = torch.cat((high[:, :1], spatial.int()), dim=1).unique(dim=0)
        if not len(coords):
            raise RuntimeError('The shape refinement produced no occupied voxels.')
        print(f'Refinement grid {grid} with {len(coords)} occupied voxels', flush=True)
        del decoder, high, shape, first_map, naf512
        release()

        fine_map = sampling_features(vision1024, naf1024, grid, coords)
        shape = run_flow(2, (1, 32, len(coords), 1), context1024, fine_map, coords)
        if trace is not None:
            torch.save(dict(samples=shape.cpu(), coords=coords.cpu()), trace / 'shape-final.pt')
        del fine_map, naf1024
        release()
        decoder = vae_load('shape_dec')
        report('Decoding the high-resolution surface', 76)
        meshes, _ = decoder.decode_shape_slat(denormalize(shape, coords), resolution)
        vertices, faces = meshes[0]
        if not projected:
            vertices = torch.stack((vertices[:, 0], vertices[:, 2], -vertices[:, 1]), dim=1)
        if not len(faces) or not torch.isfinite(vertices).all():
            raise RuntimeError('The native shape decoder returned invalid geometry.')
        print(f'Shape ready with {len(faces)} triangles in {perf_counter() - started:.2f}s', flush=True)
        return vertices.float().cpu().numpy(), faces.int().cpu().numpy()


def _main():
    from argparse import ArgumentParser
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--backend', choices=CHECKPOINTS, default='pixal3d')
    parser.add_argument('--options', type=Path)
    parser.add_argument('--mask-output', type=Path)
    parser.add_argument('--mask-only', action='store_true')
    parser.add_argument('--trace', type=Path, help='Save private tensors for stage-by-stage comparison.')
    args = parser.parse_args()
    paths = _config(args.backend, mask_only=args.mask_only)
    if args.mask_only:
        _activate_dependencies(paths)
        import numpy as np
        import torch
        from native_game_asset import mask_reference

        torch.set_num_threads(4)
        _, mask = mask_reference(args.image, paths['weights'] / BIREFNET, paths['birefnet'], device='cpu')
        np.save(args.output, np.ascontiguousarray(mask, dtype=np.float32), allow_pickle=False)
        return
    options = json.loads(args.options.read_text()) if args.options else {}
    if args.trace:
        args.trace.mkdir(parents=True, exist_ok=False)

    # Abort if a dependency accidentally reintroduces the graph runtime.
    class BlockGraphRuntime:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.partition('.')[0] in {'comfy', 'comfy_api', 'comfy_extras', 'folder_paths'}:
                raise ImportError('The standalone shape worker cannot load the ComfyUI application.')

    sys.meta_path.insert(0, BlockGraphRuntime())
    vertices, faces = _compute(
        args.image, paths, args.backend, options,
        lambda label, percent: print('GEOMETRY_PROGRESS ' + json.dumps([label, percent]), flush=True),
        args.mask_output, args.trace,
    )
    import numpy as np
    np.savez(args.output, vertices=vertices, faces=faces)


if __name__ == '__main__':
    _main()
