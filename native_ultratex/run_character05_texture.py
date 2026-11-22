"""Run the Character 05 UltraTex bridge using installed native operators and weights."""

import argparse
from contextlib import contextmanager
from hashlib import file_digest, sha256
from pathlib import Path
from types import SimpleNamespace
import json
import math
import os
import struct
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_character05 import NATIVE_RUNTIME, ROOT, prepare_textures

import numpy as np
from PIL import Image
import torch

from glb_data import read
from native_ultratex_models import (TextureAssets, encode_qwen_clip, load_flux_texture_vae, load_klein_gguf,
                                    load_pinned_file, load_qwen_text, text_prompt_tokens)
from native_ultratex_recipe import ATLAS_ORDER, character05_passes, diffusion_times, foreground_noise
from native_ultratex.options import EDITABLE_TEXTURE_FIELDS, texture_bake_options, validate_texture_options


def check_cancel(cancelled):
    if cancelled is not None and cancelled():
        raise InterruptedError('UltraTex was cancelled.')


def assert_native_imports():
    if any(name == 'comfy' or name.startswith(('comfy.', 'comfy_api.', 'comfy_extras.')) for name in sys.modules):
        raise RuntimeError('The standalone UltraTex runtime unexpectedly imported the graph host.')


def verify_model_assets(root):
    path = NATIVE_RUNTIME / 'model-manifest.json'
    files = json.loads(path.read_text(encoding='utf-8'))
    if len(files) != 6:
        raise ValueError('Character 05 UltraTex model manifest is incomplete.')
    for record in files:
        path = root / record['path']
        if not path.is_file() or path.stat().st_size != record['bytes']:
            raise ValueError(f'Missing or incorrect UltraTex model file: {record["path"]}')
        with path.open('rb') as model:
            if file_digest(model, 'sha256').hexdigest() != record['sha256']:
                raise ValueError(f'UltraTex checkpoint digest mismatch: {record["path"]}')
    return {record['path']: record['sha256'] for record in files}


@contextmanager
def runtime_ops(cancelled):
    """Bind cancellation and CUDA device selection for the standalone core."""
    from utx_graphics import bake, conditions, fastbake, guidance, render, runtime

    modules = (bake, conditions, guidance, render, runtime)
    saved = tuple(module.interrupt for module in modules)
    saved_bake = fastbake._interrupt, fastbake.choose_device
    interrupt = lambda: check_cancel(cancelled)
    for module in modules:
        module.interrupt = interrupt
    fastbake._interrupt = interrupt
    def choose_device(preference='auto', estimate_bytes=0):
        return (torch.device('cpu'), 'cpu requested') if preference == 'cpu' else (torch.device('cuda:0'), 'cuda')

    fastbake.choose_device = choose_device
    try:
        yield
    finally:
        for module, original in zip(modules, saved, strict=True):
            module.interrupt = original
        fastbake._interrupt, fastbake.choose_device = saved_bake


def selected_sources(manifest=None):
    if manifest is None:
        manifest = json.loads((NATIVE_RUNTIME / 'source-manifest.json').read_text(encoding='utf-8'))
    for relative, digest in manifest['files'].items():
        path = NATIVE_RUNTIME / relative
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Native UltraTex source changed: {relative}')
    sys.path.insert(0, str(NATIVE_RUNTIME / 'modules'))
    vae_source = load_pinned_file('original_fluxa', NATIVE_RUNTIME / 'sources/autoencoder.py',
                                 manifest['files']['sources/autoencoder.py'])
    flux_source = load_pinned_file('original_fluxd', NATIVE_RUNTIME / 'sources/flux2.py',
                                  manifest['files']['sources/flux2.py'])
    gguf = load_pinned_file('exact_gguf_ops', NATIVE_RUNTIME / 'sources/gguf_dequant.py',
                            manifest['files']['sources/gguf_dequant.py'])
    return vae_source, flux_source, gguf.dequantize


def load_texture_recipe(options=None):
    """Accept already validated settings and keep the installed recipe hash intact."""
    recipe = ROOT / 'runtime/native-game-geometry/character-05-recipe.json'
    recipe_data = recipe.read_bytes()
    if sha256(recipe_data).hexdigest() != 'f0a041581429eb673b3decd57183c87b3444e71231041ca23d6ed5829d6cfa9d':
        raise ValueError('The Character 05 reference recipe changed.')
    prompt = json.loads(recipe_data)['payload']['prompt']
    if options:
        validate_texture_options(options)
        for identifier, fields in EDITABLE_TEXTURE_FIELDS.items():
            values = options.get(identifier, {})
            prompt[identifier]['inputs'].update({key: values[key] for key in fields & values.keys()})
    return prompt


@torch.inference_mode()
def encode_conditions(vae, setup, gbuffer, cancelled=None, init_images=None, keep_masks=None):
    from utx_graphics.conditions import small_mask
    from utx_graphics.contracts import EncodedConditions
    from utx_graphics.runtime import Store, fingerprint

    store = Store(setup.runtime)
    size = gbuffer.plan.sample_size
    encoder_id = 'flux2-native-cpu-evidence-v1'

    def encode(rgb, digest):
        check_cancel(cancelled)
        key = fingerprint(encoder_id, digest, size, str(vae.encoder.conv_in.weight.dtype))
        pixels = torch.from_numpy(np.ascontiguousarray(rgb.astype(np.float32) / 255.0))[None]
        return store.cached(key, lambda: vae.encode(pixels).float().cpu().numpy())

    def encode_rgb(rgba, digest):
        alpha = rgba[..., 3:4].astype(np.float32) / 255.0
        return encode(np.clip(rgba[..., :3] * alpha + 255 * (1 - alpha) + 0.5, 0, 255).astype(np.uint8), digest)

    normal_tiles, masks, init_tiles, preserve = [], [], [], []
    background = encode(np.zeros((size, size, 3), dtype=np.uint8), 'black')
    for index, normal in enumerate(gbuffer.normals):
        rgba = normal.read()
        present = bool(np.any(gbuffer.masks[index].read()))
        if present:
            fitted = rgba if rgba.shape[:2] == (size, size) else np.asarray(
                Image.fromarray(rgba).resize((size, size), Image.Resampling.BILINEAR))
            normal_tiles.append(encode_rgb(fitted, normal.digest))
            masks.append(store.put(small_mask(rgba, size // 16)))
        else:
            normal_tiles.append(background)
            masks.append(store.put(np.zeros((size // 16, size // 16), np.bool_)))
        if init_images is not None:
            if present:
                init = np.clip(init_images[index] * 255 + 0.5, 0, 255).astype(np.uint8)
                init_tiles.append(encode(init, fingerprint(init)))
                keep = Image.fromarray(keep_masks[index].astype(np.float32)).resize((size // 16, size // 16), Image.Resampling.BOX)
                preserve.append(store.put(np.asarray(keep)))
            else:
                init_tiles.append(background)
                preserve.append(store.put(np.zeros((size // 16, size // 16), np.float32)))
    reference = setup.reference.rgba.read()
    return EncodedConditions(gbuffer, tuple(normal_tiles), encode_rgb(reference, setup.reference.identity), background,
                             store.put(small_mask(reference, size // 16)), tuple(masks), tuple(init_tiles), tuple(preserve), encoder_id)


class UltraTexAttention:
    def __init__(self, retention):
        from utx_graphics.vendor.sparse_attention import SparseLinearAttention

        self.engine = SparseLinearAttention(head_dim=128, topk=retention, BLKQ=128, BLKK=64)

    def __call__(self, query, key, value):
        if not query.is_cuda or query.ndim != 4 or query.shape[-1] != 128 or query.shape != key.shape:
            raise RuntimeError('UltraTex needs the supported CUDA attention layout.')
        if query.shape[-2] < 1024:
            # The sparse operator's verified layout begins at 1024 tokens.
            return torch.nn.functional.scaled_dot_product_attention(query, key, value)
        return self.engine(query, key, value)


@torch.inference_mode()
def sample_pass(model, pack, text, negative_text, view_pass, *, device, emit_step, cancelled=None):
    from utx_graphics.runtime import tensor_from

    dtype = torch.bfloat16
    indices = tensor_from(pack.foreground_indices).long()
    noise = foreground_noise(view_pass, indices).to(device)
    positions = tensor_from(pack.position_ids).to(device)
    reference = tensor_from(pack.references).to(device, dtype)
    text = text.to(device, dtype)
    negative = negative_text.to(device, dtype) if negative_text is not None and view_pass.guidance != 1 else None
    use_guidance = negative is not None and not torch.equal(text, negative)
    text_ids = torch.zeros((1, text.shape[1], 4), device=device)
    text_ids[..., 3] = torch.arange(text.shape[1], device=device)
    times = diffusion_times(view_pass)
    initial = tensor_from(pack.init_tokens).to(device) if pack.init_tokens is not None else None
    preserve = tensor_from(pack.keep_weights).to(device) if pack.keep_weights is not None else None
    current = noise.clone() if initial is None else (1 - times[0]) * initial + times[0] * noise
    for count, (time, next_time) in enumerate(zip(times[:-1], times[1:]), 1):
        check_cancel(cancelled)
        step = torch.full((1,), time, device=device, dtype=dtype)
        def predict(conditioning):
            values = torch.cat((current.to(dtype), reference), dim=1)
            return model(values, positions, step, conditioning, text_ids, None)[:, :pack.target_count].float()

        pred = predict(text)
        if use_guidance:
            unconditioned = predict(negative)
            pred = unconditioned + view_pass.guidance * (pred - unconditioned)
        current = current + (next_time - time) * pred
        if preserve is not None:
            known = (1 - next_time) * initial + next_time * noise
            current = preserve * known + (1 - preserve) * current
        emit_step(count, len(times) - 1)
    return current.cpu()


@torch.inference_mode()
def decode_views(vae, pack, values, policy, view_pass, cancelled):
    from utx_graphics.contracts import ViewSet
    from utx_graphics.runtime import Store, fingerprint, tensor_from
    from utx_graphics.vae_tiles import tiled_pass

    store = Store(policy)
    background = tensor_from(pack.conditions.background)
    _, channels, height, width = background.shape
    canvas = background.repeat(1, 1, 2, 3).permute(0, 2, 3, 1).reshape(1, -1, channels).contiguous()
    canvas[:, tensor_from(pack.foreground_indices).long()] = values
    canvas = canvas.reshape(1, 2 * height, 3 * width, channels).permute(0, 3, 1, 2)
    gbuffer = pack.conditions.gbuffer
    size = gbuffer.plan.sample_size
    images, masks = [None] * 6, [None] * 6
    empty = None
    feather_masks = {}
    progress = SimpleNamespace(update=lambda _: check_cancel(cancelled))
    for row, order in enumerate(ATLAS_ORDER):
        for col, view in enumerate(order):
            check_cancel(cancelled)
            native_mask = gbuffer.masks[view].read()
            if view not in gbuffer.plan.active_indices or not np.any(native_mask):
                if empty is None:
                    empty = (store.put(np.full((size, size, 3), 255, np.uint8)),
                             store.put(np.zeros((size, size), np.bool_)))
                images[view], masks[view] = empty
                continue
            mask = np.asarray(Image.fromarray(np.uint8(native_mask) * 255).resize((size, size), Image.Resampling.NEAREST)) > 0
            tile = canvas[:, :, row * height:(row + 1) * height, col * width:(col + 1) * width].contiguous()
            if view_pass.tiled_decode:
                dims = policy.decode_tile
                support = ((dims * 2, dims // 2), (dims // 2, dims * 2), (dims, dims))
                total = None
                for shape in support:
                    part = tiled_pass(tile, vae.raw_decode, shape, max(8, dims // 4), 16, 3, 'cpu', 1, progress, feather_masks)
                    total = part if total is None else total + part
                pixels = (total[0].movedim(0, -1) / 3.0 / 2.0 + 0.5).clamp(0, 1).numpy()
            else:
                pixels = vae.decode(tile)[0].cpu().numpy()
            pixels[~mask] = 1.0
            images[view] = store.put(np.clip(pixels * 255 + 0.5, 0, 255).astype(np.uint8))
            masks[view] = store.put(mask)
    job = fingerprint('native-ultratex', gbuffer.plan.identity, view_pass.task, view_pass.seed, *images)
    return ViewSet(gbuffer.plan, tuple(images), tuple(masks), view_pass.task, job, gbuffer)


def guided_views(setup, first, second_plan):
    from utx_graphics.bake import bake_map, prepare_atlas, repair_atlas
    from utx_graphics.guidance import reproject_atlas

    size = min(2048, max(1024, first.plan.sample_size))
    atlas, _ = prepare_atlas(setup.context, first.gbuffer, size, setup.runtime)
    rgb, _, seen, confidence, holes, _ = bake_map(atlas, first, 'base_color', setup.runtime, gutter=0,
                                                blend_mode='detail_preserving')
    guide, _, _ = repair_atlas(rgb, holes, atlas, 'normal_aware_surface', 8)
    return reproject_atlas(setup.context, second_plan, guide, seen, confidence)


def write_texture_copy(source_glb, output_glb, maps, material_options):
    from retexture_worker import append_textures

    def bytes_from_map(values):
        return np.clip(values * 255 + 0.5, 0, 255).astype(np.uint8)

    document, binary = read(source_glb)
    document.setdefault('images', [])
    document.setdefault('textures', [])
    if not document.get('materials'):
        document['materials'] = [{}]
    materials = np.full(maps['base_color'].shape[:2] + (3,), 255, np.uint8)
    materials[..., 1] = bytes_from_map(maps['roughness'][..., 0])
    materials[..., 2] = bytes_from_map(maps['metallic'][..., 0])
    temporary = output_glb.with_name(output_glb.name + '.tmp')
    append_textures(temporary, document, binary, Image.fromarray(bytes_from_map(maps['base_color'])), Image.fromarray(materials))
    write_character05_sampler(temporary, output_glb, material_options)
    temporary.unlink()


def write_character05_sampler(source, output, material_options):
    """Match the reference exporter's linear/clamped texture sampler metadata."""
    document, binary = read(source)
    primitive = document['meshes'][0]['primitives'][0]
    material = document['materials'][primitive['material']]
    sampler = {'magFilter': 9729, 'minFilter': 9729, 'wrapS': 33071, 'wrapT': 33071}
    document.setdefault('samplers', []).append(sampler)
    sampler_index = len(document['samplers']) - 1
    pbr = material['pbrMetallicRoughness']
    entries = [pbr['baseColorTexture'], pbr['metallicRoughnessTexture']]
    if 'normalTexture' in material:
        normal = material['normalTexture']
        normal.setdefault('scale', 1.0)
        normal.setdefault('texCoord', 0)
        entries.append(normal)
    for entry in entries:
        document['textures'][entry['index']]['sampler'] = sampler_index
    mode = material_options['alpha_mode']
    if mode == 'inherit':
        material.setdefault('alphaMode', 'OPAQUE')
    else:
        material['alphaMode'] = mode
        if mode == 'MASK':
            material['alphaCutoff'] = material_options['alpha_cutoff']
        else:
            material.pop('alphaCutoff', None)
    descriptor = json.dumps(document, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    descriptor += b' ' * (-len(descriptor) % 4)
    temporary = output.with_name(output.name + '.publish.tmp')
    with temporary.open('wb') as stream:
        stream.write(struct.pack('<4sIIII', b'glTF', 2, 28 + len(descriptor) + len(binary), len(descriptor), 0x4E4F534A))
        stream.write(descriptor)
        stream.write(struct.pack('<II', len(binary), 0x004E4942))
        stream.write(binary)
    temporary.replace(output)


def sampling_budget(policy, pack, text, device, emit):
    """Check a requested native CUDA budget without importing the graph host's allocator."""
    customized = (policy.gpu_headroom_mb, policy.activation_factor, policy.max_vram_mb) != (768, 12, 0)
    customized = customized or policy.performance_mode == 'conservative'
    if not customized and not policy.adaptive_diagnostics:
        return
    free, total = torch.cuda.mem_get_info(device)
    allocated = torch.cuda.memory_allocated(device)
    reusable = max(0, torch.cuda.memory_reserved(device) - allocated)
    mib = 1 << 20
    capacity = min(total, policy.max_vram_mb * mib or total)
    available = min(free + reusable, max(0, capacity - allocated))
    margin = policy.gpu_headroom_mb * mib
    if policy.performance_mode == 'adaptive':
        margin = max(margin, int(capacity * 0.08))
    elif policy.performance_mode == 'conservative':
        margin = max(margin, 1024 * mib, int(capacity * 0.12))
    length = pack.position_ids.shape[1] + text.shape[1]
    factor = policy.activation_factor if policy.token_chunk else max(28.0, policy.activation_factor)
    estimate = int(length * 4096 * 2 * factor) + 32 * math.ceil(length / 128) * math.ceil(length / 64) * 12 + margin
    if policy.adaptive_diagnostics:
        emit(f'UltraTex memory estimate {math.ceil(estimate / mib)} MiB, available {available // mib} MiB')
    if customized and estimate > available:
        raise ValueError('UltraTex exceeds the requested GPU budget. Lower the view resolution or reserved headroom.')


def run_character05_texture(source_glb, source_image, foreground_mask, output_glb, work_dir, *, assets, options=None,
                            progress=None, cancelled=None):
    """Execute Character 05's two UltraTex branches without its graph host."""
    if not torch.cuda.is_available():
        raise RuntimeError('Character 05 UltraTex requires the CUDA sparse attention backend.')
    start = perf_counter()
    source_glb, source_image, output_glb, work_dir = map(Path, (source_glb, source_image, output_glb, work_dir))
    if output_glb.resolve() in {source_glb.resolve(), source_image.resolve()}:
        raise ValueError('UltraTex output must be distinct from the source files.')
    work_dir.mkdir(parents=True, exist_ok=True)
    prompt = load_texture_recipe(options)
    passes = character05_passes(prompt)
    bake = prompt['377']['inputs']
    bake_size = bake['texture_size']
    emit = progress or (lambda _stage: None)
    emit('Verifying six pinned UltraTex checkpoints')
    assets = TextureAssets(Path(assets))
    model_hashes = verify_model_assets(assets.root)
    input_hashes = {}
    for path in (source_glb, source_image):
        with path.open('rb') as stream:
            input_hashes[path.name] = file_digest(stream, 'sha256').hexdigest()
    vae_source, flux_source, gguf_dequant = selected_sources()
    from utx_graphics.conditions import build_tokens
    from utx_graphics.contracts import ViewCollection
    from utx_graphics.fastbake import bake_surface_maps

    device = torch.device('cuda:0')
    torch.cuda.init()
    if cap := prompt['387']['inputs']['max_vram_mb']:
        fraction = min(1.0, (cap * (1 << 20)) / torch.cuda.get_device_properties(device).total_memory)
        torch.cuda.set_per_process_memory_fraction(fraction, device)
    torch.cuda.reset_peak_memory_stats(device)
    emit('Loading native UltraTex reference and six-view camera rigs')
    rendered = dict(albedo=[], mr=[])
    with runtime_ops(cancelled):
        setups = prepare_textures(source_glb, source_image, foreground_mask, work_dir, prompt)
        check_cancel(cancelled)
        emit('Encoding the UltraTex text prompts')
        tokenizer = NATIVE_RUNTIME / 'tokenizer'
        tokens = text_prompt_tokens(prompt['357']['inputs']['text'], tokenizer)
        qwen, embedding = load_qwen_text(assets, gguf_dequant, device=device, dtype=torch.float32)
        text = encode_qwen_clip(qwen, embedding, tokens, device=device, dtype=torch.float32).cpu()
        negative = None
        if (prompt['357']['inputs']['text'] != prompt['376']['inputs']['text']
                and any(view_pass.guidance != 1 for view_pass in passes)):
            tokens = text_prompt_tokens(prompt['376']['inputs']['text'], tokenizer)
            negative = encode_qwen_clip(qwen, embedding, tokens, device=device, dtype=torch.float32).cpu()
        del qwen, embedding
        torch.cuda.empty_cache()
        vae = load_flux_texture_vae(assets, vae_source, device=device, dtype=torch.bfloat16)
        keep_branch = None
        flux = None
        try:
            for view_pass in passes:
                check_cancel(cancelled)
                emit(f'Preparing {view_pass.task} at {view_pass.yaw:g} degrees')
                setup = setups[view_pass.task]
                buffers = setup.gbuffers[int(view_pass.yaw > 0)]
                initial = keep = None
                if view_pass.use_reprojected_init:
                    initial, keep = guided_views(setup, rendered['albedo'][0], buffers)
                conditions = encode_conditions(vae, setup, buffers, cancelled, initial, keep)
                del initial, keep
                pack, _ = build_tokens(conditions, setup.runtime)
                if keep_branch != view_pass.task:
                    del flux
                    flux = load_klein_gguf(assets, flux_source, gguf_dequant, view_pass.task,
                                          device=device, dtype=torch.bfloat16, lora_strength=view_pass.lora_strength,
                                          token_chunk=setup.runtime.token_chunk)
                    keep_branch = view_pass.task
                sampling_budget(setup.runtime, pack, text, device, emit)
                flux_source._SPARSE_ATTENTION = UltraTexAttention(view_pass.sparse_retention)
                values = sample_pass(flux, pack, text, negative, view_pass, device=device, cancelled=cancelled,
                                     emit_step=lambda i, n: emit(f'{view_pass.task} {view_pass.yaw:g} degrees, step {i}/{n}'))
                rendered[view_pass.task].append(decode_views(vae, pack, values, setup.runtime, view_pass, cancelled))
        finally:
            flux_source._SPARSE_ATTENTION = None
        del flux, vae
        torch.cuda.empty_cache()
        emit(f'Baking the {bake_size}px UltraTex albedo and material maps')
        maps, _, report = bake_surface_maps(setups['albedo'], ViewCollection(tuple(rendered['albedo'])),
                                           bake_size, bake['completion'] == 'surface_fill',
                                           setups['mr'], ViewCollection(tuple(rendered['mr'])), **texture_bake_options(bake))
        check_cancel(cancelled)
        assert_native_imports()
        write_texture_copy(source_glb, output_glb, maps, prompt['384']['inputs'])
    with output_glb.open('rb') as stream:
        checksum = file_digest(stream, 'sha256').hexdigest()
    report.update(seconds=round(perf_counter() - start, 3), backend='native CUDA sparse', model_variant='Q4_0/Q4_K_M',
                  input_sha256=input_hashes, output_sha256=checksum, model_sha256=model_hashes, atlas_size=bake_size,
                  view_counts={task: sum(len(view.plan.active_indices) for view in views) for task, views in rendered.items()},
                  graph_host_imported=False, peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(device),
                  peak_cuda_reserved_bytes=torch.cuda.max_memory_reserved(device))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--source-glb', type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--foreground-mask', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--models', type=Path, default=ROOT / 'models/native-game-texture')
    parser.add_argument('--options', type=Path)
    args = parser.parse_args()
    if any(not path.is_absolute() for path in (args.source_glb, args.reference, args.foreground_mask, args.output,
                                                args.work, args.models, args.options)
           if path is not None):
        parser.error('File and directory arguments must use absolute paths.')
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    compiler = NATIVE_RUNTIME / 'deps/triton/runtime/tcc/tcc.exe'
    if not compiler.is_file():
        raise RuntimeError('The standalone UltraTex Triton compiler is missing.')
    os.environ.setdefault('CC', str(compiler))
    os.environ.setdefault('TRITON_CACHE_DIR', str(NATIVE_RUNTIME / 'triton-cache'))
    if args.preflight:
        selected_sources()
        assert_native_imports()
        status = NATIVE_RUNTIME / 'source-manifest.json'
        manifest = json.loads(status.read_text(encoding='utf-8')) if status.is_file() else {}
        result = {'sources': 'available', 'checkpoints': verify_model_assets(args.models),
                  'gpu_execution': manifest.get('gpu_witness', 'pending')}
        print(json.dumps(result), flush=True)
    else:
        source_glb, reference, mask_path, output = args.source_glb, args.reference, args.foreground_mask, args.output
        if None in (source_glb, reference, mask_path, output):
            parser.error('Provide --source-glb, --reference, --foreground-mask and --output.')
        options = None
        if args.options:
            from native_workflow_options import settings

            options = settings('game_asset_native', json.loads(args.options.read_text(encoding='utf-8')))
        mask = np.load(mask_path, allow_pickle=False)
        if mask.ndim != 2 or mask.dtype != np.float32 or not np.isfinite(mask).all() or mask.min() < 0 or mask.max() > 1:
            raise ValueError('Provide a float32 H×W foreground mask with values in [0,1].')
        result = run_character05_texture(source_glb, reference, mask, output, work, assets=args.models, options=options,
                                        progress=lambda statement: print('STAGE ' + statement, flush=True))
        (work / 'texture-result.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
