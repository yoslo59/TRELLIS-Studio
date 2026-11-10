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
from studio_workspace import write
options = options_for_job(settings)
started = time.time()

def status(state, stage, progress, **extra):
    record = dict(state=state, stage=stage, progress=progress, elapsed=round(time.time()-started, 1), **extra)
    tmp = JOB / 'status.tmp'
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    tmp.replace(JOB / 'status.json')
    print(f'STAGE {progress}% {stage}', flush=True)

def generate_source(torch):
    status('running', 'Preparing image', 3)
    from PIL import Image, ImageOps
    reviewed = 'reference_edits' in settings
    if reviewed:
        with Image.open(JOB / 'conditioning.png') as prepared:
            image = prepared.convert('RGB')
    else:
        with Image.open(JOB / 'input.png') as original:
            image = ImageOps.exif_transpose(original).convert('RGBA')
        if settings.get('remove_bg') and image.getchannel('A').getextrema()[0] == 255:
            from rembg import new_session, remove
            session = new_session('u2net', providers=['CPUExecutionProvider'])
            image = remove(image, session=session)
            del session
            gc.collect()
        image.save(JOB / 'prepared.png')
    status('running', 'Loading model weights', 8)
    from api_spz.core.state_manage import _apply_patches
    _apply_patches()
    from trellis2.pipelines import Trellis2ImageTo3DPipeline
    from studio_pipeline import prepare_pipeline_config
    config_folder,pipeline_kind=prepare_pipeline_config(ROOT,JOB,requested_resolution)
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(str(config_folder))
    pipeline.cuda()
    if settings.get('keep_finishing_source') and not reviewed:
        prepare_image = pipeline.preprocess_image
        def record_conditioning(image):
            prepared = prepare_image(image)
            prepared.save(JOB / 'conditioning.png')
            return prepared
        pipeline.preprocess_image = record_conditioning
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
        preprocess_image=not reviewed,
        sparse_structure_sampler_params=samplers['structure'],
        shape_slat_sampler_params=samplers['shape'], tex_slat_sampler_params=samplers['texture'],
    )
    mesh = pipeline.decode_and_cleanup(shape, texture, resolution)[0]
    del shape, texture, pipeline
    gc.collect()
    torch.cuda.empty_cache()
    return dict(vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs.cpu(),
                coords=mesh.coords.cpu(), attr_layout=mesh.layout,
                voxel_size=float(mesh.voxel_size), resolution=int(resolution)), pipeline_kind


def save_finishing_source(torch, source):
    # Cache the decoded surface before the first simplification.
    status('running', 'Saving reusable finishing source', 77)
    cpu_source = {
        key: value.detach().cpu().contiguous() if isinstance(value, torch.Tensor) else value
        for key, value in source.items()
    }
    pending = JOB / 'raw_state.pending.pt'
    try:
        torch.save(cpu_source, pending)
        pending.replace(JOB / 'raw_state.pt')
    finally:
        pending.unlink(missing_ok=True)


def load_finishing_source(torch, identifier):
    import re
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('Invalid finishing source.')
    folder = JOB.parent / identifier
    if folder.resolve().parent != JOB.parent:
        raise ValueError('Invalid finishing source directory.')
    # Both the Studio and the original local pilot use only tensors, scalars and slices.
    with torch.serialization.safe_globals([slice]):
        return torch.load(folder / 'raw_state.pt', map_location='cpu', weights_only=True)


def finish_mesh(torch, source):
    status('running', 'Finishing mesh and baking textures', 83)
    import cumesh
    import o_voxel
    configure_simplifier(cumesh, options)
    export = o_voxel.postprocess.to_glb
    export_kwargs = export_options(options)
    if settings.get('adaptive_finishing'):
        # Load the Studio exporter from the application, including on an existing
        # installation with an older O-Voxel runtime package.
        from importlib.util import module_from_spec, spec_from_file_location
        from adaptive_finishing import choose_mesh
        spec = spec_from_file_location('o_voxel._studio_postprocess',
            ROOT / 'runtime' / 'engine-overrides' / 'o_voxel' / 'postprocess.py')
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        export = module.to_glb
        export_kwargs.update(remesh=False, repair_holes=False, adaptive_mesh=choose_mesh)
        status('running', 'Measuring detail against the master mesh', 84)
    vertices, faces = source['vertices'].cuda(), source['faces'].cuda()
    if not settings.get('adaptive_finishing') and options['pre_simplify'] and len(faces) > 2000000:
        mesh = cumesh.CuMesh()
        mesh.init(vertices, faces)
        mesh.simplify(2000000)
        vertices, faces = mesh.read()
        del mesh
        torch.cuda.empty_cache()
    result = export(
        vertices=vertices, faces=faces, attr_volume=source['attr_volume'], coords=source['coords'],
        attr_layout=source['attr_layout'], voxel_size=source['voxel_size'],
        aabb=[[-0.5]*3, [0.5]*3], decimation_target=settings['triangles'],
        texture_size=settings['texture_size'], **export_kwargs, verbose=True,
    )
    if source.get('coordinate_system') == 'y_up':
        # Pixal3D is already Y-up. Undo O-Voxel's Z-up to Y-up export conversion.
        result.apply_transform([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]])
    bounds = result.bounds
    height = max(float(bounds[1,1]-bounds[0,1]), 1e-6)
    center = [(bounds[0,0]+bounds[1,0])/2, bounds[0,1], (bounds[0,2]+bounds[1,2])/2]
    scale = settings['height_m'] / height
    result.vertices -= center
    result.vertices *= scale
    pending = JOB / 'model.pending.glb'
    try:
        result.export(pending)
        if settings.get('bake_normals'):
            from normal_bake import bake_master_normals
            status('running', 'Transferring master detail to the normal map', 93)
            result.metadata['normal_bake'] = bake_master_normals(
                pending, source, scale, [-coordinate * scale for coordinate in center], settings['texture_size'],
            )
        pending.replace(JOB / 'model.glb')
    finally:
        pending.unlink(missing_ok=True)
    return result


def project_requested_face():
    if settings.get('engine') != 'pixal3d' or not settings.get('reproject_face'):
        return None
    status('running', 'Reprojecting the original face', 96)
    try:
        from pixal3d_runtime import verified_face_detector
        detector = verified_face_detector(ROOT)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return {'state': 'skipped', 'reason': 'The verified face detector is unavailable.'}
    try:
        from face_projection import reproject_face
        report = reproject_face(JOB / 'model.glb', JOB / 'input.png', detector)
    except Exception:
        # The optional pass must preserve the successful model and surface failures in its log.
        traceback.print_exc()
        return {'state': 'skipped', 'reason': 'Face reprojection failed. Original texture kept.'}
    print('FACE_PROJECTION ' + json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main():
    import torch
    torch.set_num_threads(6)
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is unavailable in this environment.')
    torch.cuda.reset_peak_memory_stats()
    source_id = settings.get('finish_source_id') if settings.get('source_kind') == 'refinish' else None
    if source_id:
        status('running', 'Loading cached geometry and materials', 10)
        from api_spz.core.state_manage import _apply_patches
        _apply_patches()
        source = load_finishing_source(torch, source_id)
        pipeline_kind = settings.get('pipeline_type')
    else:
        source, pipeline_kind = generate_source(torch)
        if settings.get('keep_finishing_source'):
            save_finishing_source(torch, source)
    resolution = source['resolution']
    result = finish_mesh(torch, source)
    face_report = project_requested_face()
    stats = dict(name=settings['name'], height_m=settings['height_m'], seed=settings['seed'],
                 triangles=len(result.faces), vertices=len(result.vertices),
                 engine='Pixal3D' if settings.get('engine') == 'pixal3d' else 'TRELLIS.2',
                 dimensions_m=result.extents.tolist(), resolution=resolution,
                 requested_resolution=requested_resolution, pipeline_type=pipeline_kind,
                 texture_size=settings['texture_size'], generation_options=options,
                 sampler_options=sampler_options(options), settings_version=2,
                 neural_inferences=0 if source_id else 1,
                 peak_vram_gib=round(torch.cuda.max_memory_allocated()/1024**3, 2),
                 peak_reserved_gib=round(torch.cuda.max_memory_reserved()/1024**3, 2),
                 seconds=round(time.time()-started, 1))
    if face_report is not None:
        stats['face_projection'] = face_report
    if source_id:
        stats['finish_source_id'] = source_id
    if 'adaptive_finishing' in result.metadata:
        stats['adaptive_finishing'] = result.metadata['adaptive_finishing']
    if 'normal_bake' in result.metadata:
        report = result.metadata['normal_bake']
        stats['normal_bake'] = report
        stats['vertices'] += report['tangent_vertex_splits']
    write(JOB / 'result.json', stats)
    status('done', 'Finishing variant ready' if source_id else 'Model ready', 100, result=stats)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        traceback.print_exc()
        message = str(error)
        finishing = settings.get('source_kind') == 'refinish'
        if 'out of memory' in message.lower():
            message = ('Insufficient GPU memory. Reduce the triangle budget or texture size, then try again.'
                       if finishing else 'Insufficient GPU memory. Reduce 3D resolution (1536 → 1024 → 512) or texture size (4K → 2K), then try again.')
        stage = 'Finishing failed' if finishing else 'Generation failed'
        status('error', stage, 0, error=message[:1400])
        sys.exit(1)
