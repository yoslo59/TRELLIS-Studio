"""One generation per process: release CUDA and RAM fully after each object."""
from pathlib import Path
import gc, json, os, sys, time, traceback

ROOT = Path(__file__).resolve().parent
JOB = Path(sys.argv[1]).resolve()
settings = json.loads((JOB / 'settings.json').read_text(encoding='utf-8'))
requested_resolution = int(settings.get('resolution',512))
os.environ.update({
    'ATTN_BACKEND': 'sdpa', 'SPARSE_ATTN_BACKEND': 'xformers',
    'TRELLIS_NO_REMBG': '1', 'TRELLIS_UV_RASTERIZER': 'torch',
    'TRELLIS_MAX_RESOLUTION': str(requested_resolution), 'HF_HUB_OFFLINE': '1',
    'HF_HUB_DISABLE_TELEMETRY': '1', 'TRANSFORMERS_OFFLINE': '1',
    'U2NET_HOME': str(ROOT / 'models' / 'rembg'),
    'HF_HOME': str(ROOT / 'models' / 'hf'),
    'TORCH_HOME': str(ROOT / 'models' / 'torch'),
    'PYTORCH_CUDA_ALLOC_CONF': 'garbage_collection_threshold:0.65',
    'TORCHDYNAMO_DISABLE': '1', 'OPENCV_IO_ENABLE_OPENEXR': '1',
    'PYTHONUTF8': '1',
})
sys.path[:0] = [str(ROOT), str(ROOT / 'engine'), str(ROOT / 'engine' / 'o-voxel')]
os.chdir(ROOT / 'engine')
from generation_options import options_for_job, sampler_options, export_options, configure_simplifier
options = options_for_job(settings)
started = time.time()

def status(state, stage, progress, **extra):
    record = dict(state=state, stage=stage, progress=progress, elapsed=round(time.time()-started, 1), **extra)
    tmp = JOB / 'status.tmp'
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    tmp.replace(JOB / 'status.json')
    print(f'STAGE {progress}% {stage}', flush=True)

def main():
    status('running', 'Preparing image', 3)
    from PIL import Image, ImageOps
    image = ImageOps.exif_transpose(Image.open(JOB / 'input.png')).convert('RGBA')
    if settings.get('remove_bg') and image.getchannel('A').getextrema()[0] == 255:
        from rembg import new_session, remove
        session = new_session('u2net', providers=['CPUExecutionProvider'])
        image = remove(image, session=session)
        del session
        gc.collect()
    image.save(JOB / 'prepared.png')
    import torch
    torch.set_num_threads(6)
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA indisponible dans cet environnement.')
    torch.cuda.reset_peak_memory_stats()
    status('running', 'Loading model weights', 8)
    from api_spz.core.state_manage import _apply_patches
    _apply_patches()
    from trellis2.pipelines import Trellis2ImageTo3DPipeline
    from studio_pipeline import prepare_pipeline_config
    config_folder,pipeline_kind=prepare_pipeline_config(ROOT,JOB,requested_resolution)
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(str(config_folder))
    pipeline.cuda()
    for method, stage, percent in [
        ('get_cond', 'Analysing image', 16),
        ('sample_sparse_structure', 'Building volume', 24),
        ('sample_shape_slat', 'Generating geometry', 39),
        ('sample_tex_slat', 'Generating materials', 58),
        ('decode_and_cleanup', 'Extracting mesh', 73),
    ]:
        original = getattr(pipeline, method)
        def wrap(*args, _original=original, _stage=stage, _percent=percent, **kwargs):
            status('running', _stage, _percent)
            if _stage=='Generating materials' and options['texture_seed']>=0:
                torch.manual_seed(options['texture_seed'])
            return _original(*args, **kwargs)
        setattr(pipeline, method, wrap)
    samplers = sampler_options(options)
    print('GENERATION_OPTIONS '+json.dumps(options,ensure_ascii=False),flush=True)
    shape, texture, resolution = pipeline.run(
        image, seed=settings['seed'], pipeline_type=pipeline_kind, return_before_decode=True,
        sparse_structure_sampler_params=samplers['structure'],
        shape_slat_sampler_params=samplers['shape'], tex_slat_sampler_params=samplers['texture'],
    )
    mesh = pipeline.decode_and_cleanup(shape, texture, resolution)[0]
    del shape, texture, pipeline
    gc.collect()
    torch.cuda.empty_cache()
    status('running', 'UV et cuisson des textures', 83)
    mesh.attrs = mesh.attrs.cpu()
    mesh.coords = mesh.coords.cpu()
    import cumesh
    configure_simplifier(cumesh,options)
    if options['pre_simplify'] and len(mesh.faces) > 2000000:
        mesh.simplify(2000000)
    import o_voxel
    args = dict(vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
                coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size)
    del mesh
    torch.cuda.empty_cache()
    result = o_voxel.postprocess.to_glb(
        **args, aabb=[[-0.5]*3, [0.5]*3], decimation_target=settings['triangles'],
        texture_size=settings['texture_size'], **export_options(options), verbose=True,
    )
    del args
    bounds = result.bounds
    height = max(float(bounds[1,1]-bounds[0,1]), 1e-6)
    result.vertices -= [(bounds[0,0]+bounds[1,0])/2, bounds[0,1], (bounds[0,2]+bounds[1,2])/2]
    result.vertices *= settings['height_m']/height
    result.export(JOB / 'model.glb')
    status('running', 'Preparing Unity export', 96)
    sys.path.insert(0, str(ROOT))
    from unity_export import export_unity
    stats = export_unity(result, JOB, {**settings,'resolution':int(resolution)})
    stats['dimensions_m'] = result.extents.tolist()
    stats['resolution'] = int(resolution)
    stats['requested_resolution'] = requested_resolution
    stats['pipeline_type'] = pipeline_kind
    stats['generation_options'] = options
    stats['sampler_options'] = samplers
    stats['settings_version'] = 2
    stats['peak_vram_gib'] = round(torch.cuda.max_memory_allocated()/1024**3, 2)
    stats['peak_reserved_gib'] = round(torch.cuda.max_memory_reserved()/1024**3, 2)
    stats['seconds'] = round(time.time()-started, 1)
    (JOB / 'result.json').write_text(json.dumps(stats, indent=2), encoding='utf-8')
    status('done', 'Model ready', 100, result=stats)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        traceback.print_exc()
        message = str(error)
        if 'out of memory' in message.lower():
            message = 'Insufficient GPU memory. Reduce 3D resolution (1536 → 1024 → 512) or texture size (4K → 2K), then try again.'
        status('error', 'Generation failed', 0, error=message[:1400])
        sys.exit(1)
