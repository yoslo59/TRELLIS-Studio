"""Loopback-only local asset studio. No remote inference or hosted dependencies."""
from pathlib import Path
from studio_workspace import ID, finishing_source, read as read_workspace_json
from generation_timings import project_timings, stopped_generation
from studio_version import VERSION
from studio_release import UI_REVISION, release_status, render_studio_version
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
import asyncio, base64, hashlib, io, json, os, re, shutil, subprocess, sys, time, uuid
from urllib.parse import urlparse
from typing import Annotated
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from collection_preview import CollectionPreviews, preview_image, preview_record
from generation_options import (normalize_options, options_for_job, schema as generation_schema,
                                DEFAULT_PROFILE, DEFAULT_GENERATION, GENERATION_PRESETS, GAME_D_ADVANCED, PROFILE_ADVANCED,
                                DENSE_MASTER, DENSE_MASTER_NAME, DENSE_MASTER_TRIANGLES, DENSE_MASTER_FINISHING, FIGURE_MASTER,
                                HUMANOID_EXPERIMENTAL, TRIANGLE_MIN, TRIANGLE_MAX, validate_hd_reconstruction,
                                HI3D_FINISHING_METHODS, NO_MASTER_FINISHING, master_finishing_options,
                                validate_create_master_finishing, resolve_texture_method, validate_texture_method,
                                validate_post_refinement_texturing)
from asset_taxonomy import ASSET_TYPES, SYSTEM_TYPES, schema as taxonomy_schema
from generation_profiles import (COMFY_INPUT_FILES, COMFY_PROFILES, STANDALONE_PROFILES, WORKFLOW_PROFILES,
                                 DETAIL_STUDY, DENSE_MASTER_DESCRIPTION, PROPS_PROFILE, STARTUP_PROFILE, ProfileStore,
                                 builtin_profiles, workflow_descriptor)
from native_workflow_options import geometry_backend as workflow_geometry, settings as workflow_settings, summary as workflow_summary
from gpu_runtime import guided_install
from studio_import import FORMATS, MAX_BYTES, find_blender, validate_glb, copy_version
from animation_edits import recover as recover_animation
from glb_data import media as model_media
from library_actions import validate_metadata,validate_ids
from pixal3d_runtime import VIEW_FILES, runtime_status as pixal3d_runtime_status
from reference_preparation import (BackgroundRemovalError, PREPARED_VIEWS, parse_reference_edits,
                                   prepare_references, premultiplied_rgb)
from wtivo_runtime import wtivo_capability
from hi3d_runtime import hi3d_status

ROOT = Path(__file__).resolve().parent
PIXAL_CHARACTER_PROFILE = dict(id='character_pixal', name='Character · Pixal3D',
                               resolution=1024, triangles=10600, texture_size=2048)
PIXAL_TEXTURE_SIZES = (1024, 2048)
PIXAL_CHARACTER_OPTIONS = dict(GAME_D_ADVANCED, structure_steps=12, shape_steps=20,
                               texture_steps=12, texture_guidance=1.0)
PIXAL_SINGLE_OPTIONS = dict(PIXAL_CHARACTER_OPTIONS, shape_steps=12)
PIXAL_HUMANOID_PROFILE = dict(id=HUMANOID_EXPERIMENTAL, name='Humanoid · Experimental',
                              resolution=1024, triangles=250000, texture_size=2048,
                              experimental=True, asset_type='humanoid', keep_finishing_source=True,
                              description='Dense comparison master. Keeps the finishing source; review anatomy before reducing. No automatic local reconstruction or rig.')
PIXAL_DENSE_MASTER_PROFILE = dict(id=DENSE_MASTER, name=DENSE_MASTER_NAME, resolution=1024,
                                  triangles=DENSE_MASTER_TRIANGLES, texture_size=2048,
                                  keep_finishing_source=True, description=DENSE_MASTER_DESCRIPTION)
PIXAL_PROFILES = (PIXAL_CHARACTER_PROFILE, PIXAL_HUMANOID_PROFILE, PIXAL_DENSE_MASTER_PROFILE)
REFERENCE_FILES = tuple(VIEW_FILES.values())
# Studio 3.1 adds typed agent access and a self-hosted developer reference.
_bundle = ROOT/'bundled-web.json'
SOURCE_REVISION=(json.loads(_bundle.read_text(encoding='utf-8'))['source_revision'] if _bundle.is_file()
                 else hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
def gpu_description():
    try:
        value=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits'],text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).splitlines()[0]
        name,memory=value.rsplit(',',1)
        return f'{name.strip()} · {round(float(memory)/1024,1):g} GB'
    except (OSError,ValueError,subprocess.SubprocessError,IndexError):return 'No NVIDIA GPU detected'
GPU_DESCRIPTION=gpu_description()
# Server workspaces each use their own application state and paths.
WORKSPACE_CONFIG = globals().get('WORKSPACE_CONFIG', {})
workspace_environment = {} if (ROOT / '.trellis-setup-test').is_file() else os.environ
PROFILES = ProfileStore(WORKSPACE_CONFIG.get('profiles', workspace_environment.get('TRELLIS_PROFILES_FILE', str(ROOT / 'config' / 'generation-profiles.json'))))
OUTPUT = Path(WORKSPACE_CONFIG.get('outputs', workspace_environment.get('TRELLIS_OUTPUT_DIR', str(ROOT / 'outputs')))).resolve()
TRASH = Path(WORKSPACE_CONFIG.get('trash', workspace_environment.get('TRELLIS_TRASH_DIR', str(ROOT / 'trash')))).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
PROCESS = None
ACTIVE = None
FINISH_SOURCE = None
TERRAIN_ACTIVE = None
LOG_HANDLE = None
LOCK = asyncio.Lock()
PORT = int(os.environ.get('TRELLIS_PORT', '7860'))
SERVER = None
SHUTTING_DOWN = False
EXPORTING = set()
EXPORT_PROCESS = None
EXPORT_TASK = None
EXPORT_LOG = None
EXPORT_ROOT = Path(WORKSPACE_CONFIG.get('exports', workspace_environment.get('TRELLIS_EXPORT_DIR',str(ROOT/'exports')))).resolve()
RIG_PILOT = Path(workspace_environment.get('TRELLIS_RIG_PILOT_DIR', str(ROOT / 'UniRig' / 'tests' / 'tira' / 'delivery'))).resolve()

def stop_worker():
    if PROCESS is None or PROCESS.poll() is not None: return
    import psutil
    if ACTIVE and 'STUDIO' in globals():
        from generated_animation_runtime import stop_runtime
        stop_runtime(ROOT, STUDIO.job_folder(ACTIVE))
    if ACTIVE and (OUTPUT/ACTIVE/'rig-work'/'runtime.pid').is_file():
        try: operation=json.loads((OUTPUT/ACTIVE/'status.json').read_text(encoding='utf-8')).get('operation')
        except (OSError,ValueError):operation=None
        pid_path=OUTPUT/ACTIVE/'rig-work'/'runtime.pid'
        if operation!='animation':
            try:
                pid=int(pid_path.read_text().strip())
                config_path=ROOT/'config'/'runtime.json'
                config=json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
                if pid>1: subprocess.run(['wsl.exe','-d',config.get('wsl_distribution','Ubuntu-22.04'),'--','kill','-TERM','--','-'+str(pid)],timeout=8,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except (OSError,ValueError,subprocess.TimeoutExpired): pass
    try: children = psutil.Process(PROCESS.pid).children(recursive=True)
    except psutil.NoSuchProcess: children = []
    for child in reversed(children):
        try: child.terminate()
        except psutil.NoSuchProcess: pass
    PROCESS.terminate()
    try: PROCESS.wait(timeout=8)
    except subprocess.TimeoutExpired: PROCESS.kill(); PROCESS.wait()
    _, alive = psutil.wait_procs(children, timeout=3)
    for child in alive:
        try: child.kill()
        except psutil.NoSuchProcess: pass

@asynccontextmanager
async def lifespan(app):
    await STUDIO.start()
    COLLECTION_PREVIEWS.start()
    try:
        yield
    finally:
        await COLLECTION_PREVIEWS.close()
    await STUDIO.close()
    if PROCESS is not None and PROCESS.poll() is None:
        await asyncio.to_thread(stop_worker)
    if LOG_HANDLE: LOG_HANDLE.close()
    if EXPORT_PROCESS is not None and EXPORT_PROCESS.poll() is None:
        await asyncio.to_thread(stop_export)
    if EXPORT_LOG:EXPORT_LOG.close()

app = FastAPI(title='TRELLIS Studio API', version=VERSION, description='Local asset studio. Typed JSON contracts are under /api/v1. Computation is asynchronous; poll job/export status. UI compatibility routes remain available.', lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url='/api/openapi.json')

@app.middleware('http')
async def local_requests(request: Request, call_next):
    if WORKSPACE_CONFIG and request.scope.get('trellis.server') is True:
        return await call_next(request)
    host = request.url.hostname
    if host not in ('127.0.0.1', 'localhost'):
        return JSONResponse({'detail': 'Local access only.'}, status_code=403)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': 'Origin rejected.'}, status_code=403)
        if SHUTTING_DOWN and request.url.path!='/api/shutdown':
            return JSONResponse({'detail': 'Studio is stopping.'},status_code=503)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

def job_path(identifier):
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise HTTPException(404)
    folder = OUTPUT / identifier
    if folder.resolve().parent != OUTPUT.resolve(): raise HTTPException(403, 'Library path rejected.')
    if not folder.is_dir(): raise HTTPException(404)
    return folder

PREPARED_OUTPUT_KINDS = ('unirig', 'animation_copy', 'texture_projection', 'baked_game_copy',
                         'multiview_texture_refinement', 'face_refinement')


def prepared_output_ready(settings, status):
    if settings.get('creature_phase') == 'draft' or status.get('rig_phase') == 'draft':
        return False
    return (settings.get('source_kind') not in PREPARED_OUTPUT_KINDS or status.get('state') == 'done'
            or status.get('operation') == 'animation')


def job_record(folder):
    try:
        publication_pending = WORKSPACE_CONFIG.get('publication_pending')
        pending = publication_pending is not None and publication_pending(folder.name)
        settings = json.loads((folder / 'settings.json').read_text(encoding='utf-8'))
        status_path = folder / 'status.json'
        data = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {'state':'queued','stage':'Starting','progress':0}
        finalizing = pending and data['state'] == 'done'
        if finalizing:
            # Server publication follows worker exit and quota reconciliation.
            data.update(state='running', stage='Finalizing model', progress=99)
        data.update(id=folder.name, settings=settings)
        data['media'] = (model_media(folder/'model.glb') if not finalizing and prepared_output_ready(settings, data)
                         else dict(available=False, animated=False, clips=0, bones=0))
        if data['state'] == 'done' and data['media'].get('available'):
            data['collection_preview'] = preview_record(COLLECTION_CACHE, folder)
        data['can_refinish'] = data['state'] == 'done' and bool(finishing_source(folder, settings))
        if not settings.get('workflow_id') and settings.get('asset_type') not in ('terrain_unity','imported','rigged','print'):
            try:data['generation_options']=options_for_job(settings)
            except (ValueError,TypeError):data['generation_options']=None
        metadata_path = folder / 'metadata.json'
        try: data['metadata'] = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
        except (ValueError,OSError): data['metadata'] = {}
        if not isinstance(data['metadata'],dict): data['metadata'] = {}
        data['lods']=json.loads((folder/'lod-manifest.json').read_text(encoding='utf-8')) if (folder/'lod-manifest.json').is_file() else None
        data['physics']=json.loads((folder/'physics.json').read_text(encoding='utf-8')) if (folder/'physics.json').is_file() else None
        data['presentation']=json.loads((folder/'Presentation/presentation.json').read_text(encoding='utf-8')) if (folder/'Presentation/presentation.json').is_file() else {}
        if not finalizing and data['state'] in ('running','queued') and not (data['state']=='queued' and data.get('operation')!='animation' and 'STUDIO' in globals() and STUDIO.queued(folder.name)):
            if folder.name != TERRAIN_ACTIVE and (folder.name != ACTIVE or PROCESS is None or PROCESS.poll() is not None):
                if data.get('operation')=='animation':
                    recover_animation(folder)
                    (folder/'generated-animation-request.json').unlink(missing_ok=True)
                    settings=json.loads((folder/'settings.json').read_text(encoding='utf-8'));data['settings']=settings
                    result_path=folder/'result.json'
                    if result_path.is_file():data['result']=json.loads(result_path.read_text(encoding='utf-8'))
                    data.update(state='done',stage='Model preserved after interruption',progress=100,operation_error='Animation editing was interrupted; the previous version has been preserved.')
                    write_json(status_path,{k:v for k,v in data.items() if k not in ('id','settings','metadata')})
                else:data.update(state='error', stage='Generation interrupted', error='The process stopped. Check the log and try again.')
        if isinstance(data.get('result'), dict):
            original = data['result'].pop('generation_timing', None)
            if (isinstance(original, dict) and original.get('job_id') == folder.name
                    and data['state'] == 'done' and not data.get('operation')):
                original.pop('job_id')
                data.setdefault('stage_timings', original)
        project_timings(data, at=time.monotonic() if data['state'] == 'running' else None)
        return data
    except (OSError, ValueError): return None

def generation_capabilities():
    checkpoints=ROOT/'models'/'trellis2'/'ckpts'
    higher=all((checkpoints/(name+suffix)).is_file() for name in ('slat_flow_img2shape_dit_1_3B_1024_bf16','slat_flow_imgshape2tex_dit_1_3B_1024_bf16') for suffix in ('.json','.safetensors'))
    resolutions=[512]+([1024] if higher else [])
    textures=[1024,2048]
    path=ROOT/'models'/'studio-capabilities.json'
    if path.is_file():
        try:
            validated=json.loads(path.read_text(encoding='utf-8'))
            if higher and 1536 in validated.get('resolutions',[]):resolutions.append(1536)
            if 4096 in validated.get('texture_sizes',[]):textures.append(4096)
        except (ValueError,OSError,AttributeError):pass
    return resolutions,textures

def guided_install_failure(component):
    if not guided_install(ROOT):
        return ''
    try:
        content = (ROOT / 'config' / 'component-install.json').read_text(encoding='utf-8-sig')
        record = json.loads(content).get('components', {}).get(component, {})
        if record.get('status') == 'ready':
            return ''
        if record.get('message'):
            return record['message']
    except (OSError, ValueError, AttributeError):
        pass
    return 'Use Repair or resume components to install and verify this component.'


def pixal3d_capabilities():
    runtime = pixal3d_runtime_status(ROOT)
    if message := guided_install_failure('pixal3d'):
        runtime.update(ready=False, message=message, single_image=dict(ready=False, message=message))
    return {**runtime, 'profile': PIXAL_CHARACTER_PROFILE,
            'default_profile': PIXAL_CHARACTER_PROFILE['id'],
            'profiles': [profile for profile in PIXAL_PROFILES if profile['id'] != HUMANOID_EXPERIMENTAL],
            'texture_sizes': PIXAL_TEXTURE_SIZES}


def create_master_finishing_capabilities():
    available, reason = wtivo_capability(ROOT, 1024)
    if available and not find_blender():
        available = False
        reason = 'Install Blender to use Surface Closure.'
    hi3d = hi3d_status(ROOT)
    return dict(studio_768=dict(available=True, reason=''),
                wtivo_1024=dict(available=available, reason=reason),
                **{NO_MASTER_FINISHING: dict(available=True, reason='')},
                **{method: dict(available=hi3d['available'], reason=hi3d['reason']) for method in HI3D_FINISHING_METHODS})


def require_create_master_finishing(method):
    if method == 'wtivo_1024' or method in HI3D_FINISHING_METHODS:
        capability = create_master_finishing_capabilities()[method]
        if not capability['available']:
            raise HTTPException(503, capability['reason'])


def multiview_texture_capability():
    from multiview_texture_runtime import availability

    return availability(ROOT)


def ultratex_texture_capability():
    try:
        from ultratex_runtime import availability
        return availability(ROOT)
    except ImportError:
        return dict(available=False, reason='Install the optional UltraTex texture component.')


def require_texture_method(method):
    capability = (multiview_texture_capability() if method == 'multiview'
                  else ultratex_texture_capability() if method == 'ultratex' else None)
    if capability and not capability['available']:
        raise HTTPException(503, capability['reason'])


def detail_texturing_capability():
    from retexture_runtime import availability
    return availability(ROOT)


def assign_create_master_finishing(settings, method):
    if not method:
        return
    settings.update(master_finishing=method,
                    generation_options=master_finishing_options(method, settings['generation_options']))
    flags = {'studio_768': 'hd_reconstruction', 'wtivo_1024': 'wtivo_reconstruction'}
    flag = flags.get(method)
    if flag:
        settings[flag] = True
    settings['keep_finishing_source'] = True
    if snapshot := settings.get('profile_snapshot'):
        snapshot.update(master_finishing=method, generation_options=settings['generation_options'], keep_finishing_source=True)
        if flag:
            snapshot[flag] = True


@app.get('/api/status')
def status():
    import psutil
    ready = (ROOT / 'models' / 'trellis2' / 'pipeline.json').exists() and (ROOT / 'engine' / 'MODELS' / 'dinov3' / 'model.safetensors').exists()
    preview_error=guided_install_failure('trellis')
    ready=ready and not preview_error
    resolutions,textures=generation_capabilities()
    pixal3d=pixal3d_capabilities()
    generation_methods = {
        'trellis2': dict(ready=ready, message='' if ready else preview_error or 'Install the TRELLIS.2 model weights.'),
        'pixal3d_single': pixal3d.get('single_image', dict(ready=False, message='Install Pixal3D Single Image.')),
        'pixal3d': {key: pixal3d[key] for key in ('ready', 'message')},
    }
    return dict(ready=ready, gpu=GPU_DESCRIPTION, ram_free_gib=round(psutil.virtual_memory().available/1024**3,1),
                active=ACTIVE if PROCESS is not None and PROCESS.poll() is None else TERRAIN_ACTIVE,
                export_active=EXPORT_PROCESS is not None and EXPORT_PROCESS.poll() is None,
                resolution=DEFAULT_GENERATION['resolution'], default_profile=DEFAULT_PROFILE, local=True, studio_version=VERSION,source_revision=SOURCE_REVISION,ui_revision=UI_REVISION,multi_view=pixal3d['ready'],pixal3d=pixal3d,
                generation_methods=generation_methods, create_master_finishing=create_master_finishing_capabilities(),
                detail_texturing=detail_texturing_capability(),
                multiview_texture_refinement=multiview_texture_capability(),
                ultratex_texture_refinement=ultratex_texture_capability(),
                texture_methods=dict(trellis=dict(available=True, reason=''),
                                     multiview=multiview_texture_capability(), ultratex=ultratex_texture_capability()),
                edition='server' if WORKSPACE_CONFIG else 'desktop',
                features=['library','metadata','trash','blender_export','uv_preview','resolution_choice','advanced_controls','generation_profiles','model_import','unirig','rig_landmarks','rig_profiles','creature_rig','creature_motions','print_copy','asset_taxonomy','comparison','diagnostics','target_exports','batch_actions','generation_queue','collections','version_history','lods','game_preparation','motion_tools','animation_batch','animation_clip_removal','generated_animations','presentation','library_turntable','collection_transfer','rest_api_v1','mcp','refinish','repair_surface','hd_reconstruction','hd_master_finishing','pixal3d_multiview','pixal3d_face_projection','texture_projection','multiview_texture_refinement','baked_game_copy','face_refinement'], rig_profiles=['advanced'], texture_sizes=textures,supported_resolutions=resolutions)

@app.get('/api/components')
def components():
    try: runtime=json.loads((ROOT/'logs'/'components.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError): runtime={}
    blender=bool(find_blender())
    unirig=runtime.get('unirig',{'ready':False,'message':'Run Open TRELLIS.cmd to check UniRig.'})
    if message := guided_install_failure('unirig'):
        unirig=dict(ready=False,message=message)
    return dict(blender=blender, import_formats=list(FORMATS) if blender and not WORKSPACE_CONFIG else ['.glb'], max_import_bytes=MAX_BYTES,
                unirig=unirig,
                rig_pilot=(RIG_PILOT/'tira_animated.glb').is_file())

def launch_worker(folder, script, source_id=None):
    global PROCESS, ACTIVE, LOG_HANDLE, FINISH_SOURCE
    if LOG_HANDLE: LOG_HANDLE.close()
    LOG_HANDLE=(folder/'generation.log').open('a' if script in ('animation_worker.py','generated_animation_worker.py') else 'w',encoding='utf-8')
    env=os.environ.copy();env['PYTHONUTF8']='1'
    PROCESS=subprocess.Popen([sys.executable,'-u',str(ROOT/script),str(folder)],cwd=ROOT,env=env,
                             stdout=LOG_HANDLE,stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    ACTIVE=folder.name
    FINISH_SOURCE = source_id

def require_idle():
    if SHUTTING_DOWN:raise HTTPException(503,'Studio is stopping.')
    if TERRAIN_ACTIVE or (PROCESS is not None and PROCESS.poll() is None):
        raise HTTPException(409,'Wait for the current computation to finish.')

@app.post('/api/import',status_code=201)
async def import_model(model: UploadFile=File(...), name: str=Form('')):
    async with LOCK:
        require_idle()
        suffix=Path(model.filename or '').suffix.lower()
        if WORKSPACE_CONFIG and suffix != '.glb': raise HTTPException(400,'Server imports require a self-contained GLB.')
        if suffix not in FORMATS: raise HTTPException(400,'Choose a model GLB, FBX, OBJ, STL ou PLY.')
        if suffix!='.glb' and find_blender() is None: raise HTTPException(503,'Blender is required for this format. You can import a self-contained GLB without it.')
        if len(name)>80: raise HTTPException(400,'Name too long (80 characters maximum).')
        identifier=uuid.uuid4().hex;folder=OUTPUT/identifier;folder.mkdir()
        source=folder/('original'+suffix)
        total=0
        try:
            with source.open('wb') as stream:
                while chunk:=await model.read(1024*1024):
                    total+=len(chunk)
                    if total>MAX_BYTES: raise ValueError('The file exceeds 500 MB.')
                    stream.write(chunk)
            if not total: raise ValueError('Empty file.')
            if suffix=='.glb': validate_glb(source)
        except (ValueError,OSError) as error:
            # Only this newly created, checked upload directory can be removed.
            if folder.resolve().parent==OUTPUT and folder.name==identifier: shutil.rmtree(folder)
            raise HTTPException(400,str(error))
        finally: await model.close()
        original_name=(model.filename or 'Model').replace('\\','/').split('/')[-1]
        display=(name.strip() or Path(original_name).stem)[:80] or 'Model imported'
        safe=re.sub(r'[^A-Za-z0-9_-]+','-',display).strip('-')[:50] or 'Modele'
        write_json(folder/'settings.json',dict(name=safe,asset_type='imported',source_kind='import',created=time.time(),
                   source_filename=original_name[:255],original_file=source.name,height_m=0,texture_size=0,triangles=0))
        write_json(folder/'metadata.json',dict(display_name=display,review_state='review',favorite=False,tags=[]))
        write_json(folder/'status.json',dict(state='queued',stage='Importing model',progress=2))
        launch_worker(folder,'import_worker.py')
        return {'id':identifier}

@app.post('/api/jobs/{identifier}/version',status_code=201)
async def import_version(identifier: str):
    async with LOCK:
        require_idle();source=job_path(identifier);record=job_record(source)
        if record['settings'].get('asset_type') not in ('imported','rigged','print') or record['state']!='done':
            raise HTTPException(409,'Select a completed imported or rigged model to create a copy.')
        destination=OUTPUT/uuid.uuid4().hex;destination.mkdir();copy_version(source,destination)
        settings={**record['settings'],'created':time.time(),'parent_id':identifier}
        write_json(destination/'settings.json',settings)
        write_json(destination/'metadata.json',{**record['metadata'],'display_name':(record['metadata'].get('display_name',settings['name'])+' · version')[:80]})
        write_json(destination/'status.json',dict(state='done',stage='New version',progress=100,result=record.get('result')))
        return {'id':destination.name}

def rig_input_file(folder, name):
    path = folder / name
    if not path.is_file() or path.resolve() != path:
        raise HTTPException(409, 'The rig source is unavailable.')
    return path


def rig_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


async def rig_json(request, limit):
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > limit:
            raise HTTPException(413, 'Rig options are too large.')
        payload.extend(chunk)
    try:
        return json.loads(payload) if payload else {}
    except (ValueError, RecursionError) as error:
        raise HTTPException(400, 'Invalid rig options.') from error


@app.post('/api/jobs/{identifier}/rig', status_code=201)
async def rig_model(identifier: str, request: Request):
    from rig_landmarks import validate_rig_options

    try:
        options = validate_rig_options(await rig_json(request, 16 * 1024))
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error) or 'Invalid rig options.') from error
    creature = options.get('mode') == 'creature'
    advanced = options.get('profile') == 'advanced'
    async with LOCK:
        if not WORKSPACE_CONFIG:
            require_idle()
        source = job_path(identifier)
        record = job_record(source)
        if not record or record['state'] != 'done' or not prepared_output_ready(record['settings'], record):
            raise HTTPException(409, 'Choose a completed model.')
        if record['settings'].get('asset_type') == 'terrain_unity':
            raise HTTPException(400, 'Choose a character or mesh object.')
        model = rig_input_file(source, 'model.glb')
        try:
            skinned = bool((await asyncio.to_thread(validate_glb, model)).get('skins'))
        except ValueError as error:
            raise HTTPException(400, 'Choose a valid GLB model.') from error
        if skinned and (creature or advanced):
            raise HTTPException(409, 'Open the original unrigged model to create this rig.')
        if skinned and options.get('landmarks'):
            raise HTTPException(409, 'This model already has a skeleton. Landmark placement requires an unrigged copy.')
        if not components()['unirig'].get('ready') or not find_blender():
            raise HTTPException(503, 'UniRig or Blender is unavailable. Run Open TRELLIS.cmd and check its diagnostic log.')
        destination = OUTPUT / uuid.uuid4().hex
        source_settings = record['settings']
        label = record['metadata'].get('display_name') or source_settings['name']
        suffix = ' · Creature rig' if creature else ' · Advanced rig' if advanced else ' · rig'
        settings = dict(name=source_settings['name'] + '-rig', asset_type='rigged', source_kind='unirig',
                        animation_clips=[], rig_options=options, created=time.time(), parent_id=identifier,
                        height_m=source_settings.get('height_m', 0), texture_size=source_settings.get('texture_size', 0))
        stage = 'Predicting creature skeleton' if creature else 'Preparing rig'
        with TemporaryDirectory(prefix='.rig-', dir=OUTPUT) as temporary:
            staged = Path(temporary)
            await asyncio.to_thread(shutil.copy2, model, staged / 'source.glb')
            if creature:
                digest = await asyncio.to_thread(rig_digest, staged / 'source.glb')
                settings.update(creature_phase='draft', rig_source_sha256=digest)
            reference = source / 'input.png'
            if reference.is_file() and reference.resolve() == reference:
                await asyncio.to_thread(shutil.copy2, reference, staged / 'input.png')
            write_json(staged / 'settings.json', settings)
            write_json(staged / 'metadata.json', dict(display_name=label[:80-len(suffix)] + suffix,
                                                     review_state='review', favorite=False, tags=['UniRig']))
            write_json(staged / 'status.json', dict(state='queued', stage=stage, progress=2))
            staged.rename(destination)
        try:
            launch_worker(destination, 'rig_worker.py')
        except OSError as error:
            write_json(destination / 'status.json', dict(state='error', stage='Worker launch failed', progress=0))
            raise HTTPException(503, 'Unable to start rig preparation.') from error
        return {'id': destination.name}


@app.post('/api/jobs/{identifier}/rig-complete', status_code=202)
async def rig_complete(identifier: str, request: Request):
    from creature_rig import validate_creature_edit

    try:
        data = validate_creature_edit(await rig_json(request, 1024 * 1024))
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error) or 'Invalid creature skeleton.') from error
    async with LOCK:
        if not WORKSPACE_CONFIG:
            require_idle()
        folder = job_path(identifier)
        record = job_record(folder)
        if not record or not record['settings'].get('creature_phase') or active_job(identifier):
            raise HTTPException(409, 'Choose an editable creature rig.')
        state = record['state']
        if not (state in ('error', 'cancelled') or state == 'done' and record.get('rig_phase') == 'draft'):
            raise HTTPException(409, 'Wait for an editable creature skeleton.')
        preview = rig_input_file(folder, 'rig-preview.json')
        snapshot = rig_input_file(folder, 'source.glb')
        if await asyncio.to_thread(rig_digest, preview) != data['revision']:
            raise HTTPException(409, 'The creature skeleton changed. Reopen the current draft.')
        if await asyncio.to_thread(rig_digest, snapshot) != record['settings'].get('rig_source_sha256'):
            raise HTTPException(409, 'The creature source changed. Start a new rig from the original model.')
        rig_input_file(folder, 'rig-work/skeleton.fbx')
        write_json(folder / 'creature-edit.json', data)
        write_json(folder / 'settings.json', dict(record['settings'], creature_phase='final'))
        write_json(folder / 'status.json', dict(state='queued', stage='Preparing creature skin weights', progress=2,
                                                rig_phase='final'))
        try:
            launch_worker(folder, 'rig_worker.py')
        except OSError as error:
            write_json(folder / 'status.json', dict(state='error', stage='Worker launch failed', progress=0,
                                                    rig_phase='draft'))
            raise HTTPException(503, 'Unable to start the creature rig.') from error
        return {'id': identifier}


from retarget_catalogue import ANIMATIONS as ANIMATION_LIBRARY, BY_ID as ANIMATION_BY_ID

@app.get('/api/asset-taxonomy')
def asset_taxonomy():
    return taxonomy_schema()

@app.post('/api/jobs/{identifier}/print-copy',status_code=201)
async def print_copy(identifier: str,request: Request):
    from print_prepare import validate_options
    try: options=validate_options(await request.json())
    except (ValueError,TypeError):raise HTTPException(400,'Choose a height from 20–500 mm and a voxel size from 0.15–2 mm.')
    async with LOCK:
        require_idle();source=job_path(identifier);record=job_record(source)
        if record['state']!='done' or not (source/'model.glb').is_file():raise HTTPException(409,'Choose a completed model.')
        if not find_blender():raise HTTPException(503,'Blender is required to prepare a print copy.')
        destination=OUTPUT/uuid.uuid4().hex;destination.mkdir()
        shutil.copy2(source/'model.glb',destination/'source.glb')
        if (source/'input.png').is_file():shutil.copy2(source/'input.png',destination/'input.png')
        write_json(destination/'settings.json',dict(name=record['settings']['name']+'-print',asset_type='print',source_kind='print',
            parent_id=identifier,created=time.time(),height_m=options['height_mm']/1000,texture_size=0,print_options=options))
        write_json(destination/'metadata.json',dict(display_name=(record['metadata'].get('display_name',record['settings']['name'])+' · print')[:80],
            category=record['metadata'].get('category',''),review_state='review',favorite=False,tags=['3D print']))
        write_json(destination/'status.json',dict(state='queued',stage='Preparing print copy',progress=2))
        launch_worker(destination,'print_worker.py')
        return dict(id=destination.name)

@app.get('/api/animations')
def animation_library():
    return dict(animations=ANIMATION_LIBRARY,min_duration=2,max_duration=5,step=.5,max_batch_clips=len(ANIMATION_LIBRARY),
                description='Authored Quaternius CC0 animations, retargeted to your character.')

@app.get('/api/motion-source/{pack}')
def motion_source(pack: str):
    recipe=next((clip for clip in ANIMATION_LIBRARY if clip['source_pack']==pack),None)
    if recipe is None:raise HTTPException(404)
    return FileResponse(ROOT/recipe['source_model'],media_type='model/gltf-binary')

async def requested_animations(request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError()
        items = data['clips'] if 'clips' in data else [data]
        if not isinstance(items, list) or not 1 <= len(items) <= len(ANIMATION_LIBRARY):
            raise ValueError()
        clips = {}
        for item in items:
            clip, duration = item['clip'], float(item['duration'])
            if clip not in ANIMATION_BY_ID or clip in clips or not 2 <= duration <= 5 or duration * 2 != int(duration * 2):
                raise ValueError()
            clips[clip] = dict(id=clip, duration=duration)
        return clips
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise HTTPException(400, 'Choose distinct library motions and durations from 2 to 5 seconds, in 0.5-second steps.') from error


def stage_animation_copy(source, record, clips):
    destination = OUTPUT / uuid.uuid4().hex
    settings = dict(record['settings'], created=time.time(), parent_id=source.name,
                    source_kind='animation_copy', asset_type='rigged')
    label = record['metadata'].get('display_name') or settings['name']
    suffix = ' · animations'
    metadata = dict(record['metadata'], display_name=label[:80-len(suffix)] + suffix,
                    review_state='review', favorite=False)
    with TemporaryDirectory(prefix='.animation-copy-', dir=OUTPUT) as temporary:
        staged = Path(temporary)
        shutil.copy2(source/'model.glb', staged/'source.glb')
        for filename in (*REFERENCE_FILES, 'physics.json', 'lod-manifest.json', 'motion-report.json'):
            if (source/filename).is_file():
                shutil.copy2(source/filename, staged/filename)
        for directory in ('LODs', 'Collisions'):
            if (source/directory).is_dir():
                shutil.copytree(source/directory, staged/directory)
        write_json(staged/'settings.json', settings)
        write_json(staged/'metadata.json', metadata)
        write_json(staged/'animation-request.json', dict(settings=settings, result=record.get('result') or {},
                                                         clips=clips, linked=True))
        write_json(staged/'status.json', dict(state='queued', stage='Waiting to add animations',
                                              progress=0, operation='animation_copy'))
        staged.rename(destination)
    return destination


def animation_operations_root():
    return STUDIO.workspace.root / 'animation-ops'


def animation_operation_path(identifier):
    root = animation_operations_root()
    path = root / identifier
    if not WORKSPACE_CONFIG or not ID.fullmatch(identifier) or path.resolve() != path or not path.is_dir():
        raise HTTPException(404, 'Animation operation not found.')
    return path


def stage_animation_operation(source, record, request, revision, generated=False):
    root = animation_operations_root()
    root.mkdir(parents=True, exist_ok=True)
    for entry in STUDIO.workspace.queue['items']:
        pending = root / entry['id']
        state = read_workspace_json(pending/'status.json', {}).get('state')
        if state in ('queued', 'running', 'ready'):
            current = read_workspace_json(pending/'operation.json', {})
            if current.get('model_id') == source.name:
                raise HTTPException(409, 'Wait for the current animation update on this model.')
    target = root / uuid.uuid4().hex
    with TemporaryDirectory(prefix='.animation-operation-', dir=root) as temporary:
        staged = Path(temporary)
        shutil.copy2(source/'model.glb', staged/'source.glb')
        if (source/'LODs').is_dir():
            shutil.copytree(source/'LODs', staged/'LODs')
        if (source/'motion-report.json').is_file():
            shutil.copy2(source/'motion-report.json', staged/'motion-report.json')
        settings = dict(record['settings'])
        if generated:
            settings['generated_animation_action'] = 'apply'
        write_json(staged/'settings.json', settings)
        write_json(staged/'operation.json', dict(model_id=source.name, revision=revision,
                                                name=record['metadata'].get('display_name') or record['settings']['name']))
        request_file = 'generated-animation-request.json' if generated else 'animation-request.json'
        write_json(staged/request_file, dict(request, linked=True, defer_publish=True))
        write_json(staged/'status.json', dict(state='queued', stage='Waiting to update animations', progress=0,
                                             operation='animation'))
        staged.rename(target)
    return target


def removal_selection(glb, selection, revision):
    if not isinstance(selection, dict) or 'clip' not in selection or selection.keys() - {'clip', 'index', 'revision'}:
        raise HTTPException(400, 'Choose an animation to remove.')
    expected = selection.get('revision')
    if expected is not None and expected != revision:
        raise HTTPException(409, 'The animation list changed. Reload it before removing a clip.')
    names = [clip.get('name') for clip in glb.get('animations', [])]
    name = selection['clip']
    index = selection.get('index')
    if index is not None:
        if type(index) is not int or index < 0:
            raise HTTPException(400, 'Choose a valid animation index.')
        if index >= len(names) or names[index] != name:
            raise HTTPException(409, 'The animation list changed. Reload it before removing a clip.')
    else:
        if not isinstance(name, str) or not name:
            raise HTTPException(400, 'Choose a named animation or supply its index and expected name.')
        matches = [index for index, candidate in enumerate(names) if candidate == name]
        if not matches:
            raise HTTPException(404, 'Animation not found on this model.')
        if len(matches) > 1:
            raise HTTPException(409, 'Use the animation index to select between clips with the same name.')
        index = matches[0]
    return dict(remove_clip=name, remove_index=index)


async def prepare_animations(identifier, request, linked=False, remove=False):
    if remove:
        try:
            selection = await request.json()
        except ValueError as error:
            raise HTTPException(400, 'Choose an animation to remove.') from error
    else:
        selected = await requested_animations(request)
    async with LOCK:
        if not WORKSPACE_CONFIG:
            require_idle()
        source=job_path(identifier);record=job_record(source)
        if record['state']!='done' or not (source/'model.glb').is_file():raise HTTPException(409,'Choose a completed model.')
        refresh_export()
        if identifier in EXPORTING:raise HTTPException(409,'Wait for this model’s export to finish.')
        glb=validate_glb(source/'model.glb')
        revision = str((source/'model.glb').stat().st_mtime_ns)
        result = record.get('result') or {}
        clips = record['settings'].get('animation_clips', [])
        extra = {}
        if remove:
            extra = removal_selection(glb, selection, revision)
        else:
            if not glb.get('skins'):
                raise HTTPException(409, 'Add a rig to the model first.')
            if result.get('rigged') and not result.get('humanoid_mapping'):
                raise HTTPException(422, 'This skeleton could not be identified as humanoid. Standard humanoid motions do not fit this character.')
            clips = [clip for clip in clips if clip['id'] not in selected] + list(selected.values())
        if not find_blender():raise HTTPException(503,'Blender is unavailable.')
        if linked:
            destination = await asyncio.to_thread(stage_animation_copy, source, record, clips)
            try:
                launch_worker(destination, 'animation_worker.py')
            except OSError as error:
                write_json(destination/'status.json', dict(state='error', stage='Worker launch failed', progress=0))
                raise HTTPException(503, 'Unable to start animation preparation.') from error
            return {'id': destination.name}
        recipe = dict(settings=record['settings'], result=result, clips=clips, **extra)
        return await queue_animation_update(source, record, recipe, revision)


async def queue_animation_update(source, record, recipe, revision):
    if WORKSPACE_CONFIG:
        operation = await asyncio.to_thread(stage_animation_operation, source, record, recipe, revision)
        launch_worker(operation, 'animation_worker.py')
        return {'id': source.name, 'operation_id': operation.name}
    previous = read_workspace_json(source / 'status.json', {})
    write_json(source / 'animation-request.json', recipe)
    write_json(source / 'status.json', dict(state='queued', stage='Updating animations', progress=5,
                                             operation='animation', result=recipe['result']))
    try:
        launch_worker(source, 'animation_worker.py')
    except OSError as error:
        write_json(source / 'status.json', previous)
        raise HTTPException(503, 'Unable to start animation preparation.') from error
    return {'id': source.name}


def creature_motion_target(identifier):
    source = job_path(identifier)
    record = job_record(source)
    if not record or record['state'] != 'done' or not record['media']['available']:
        raise HTTPException(409, 'Choose a completed model to inspect its rig.')
    model = rig_input_file(source, 'model.glb')
    return source, record, model


@app.get('/api/jobs/{identifier}/creature-motions')
def creature_motion_options(identifier: str):
    from creature_motion_edit import creature_motion_capabilities

    _, _, model = creature_motion_target(identifier)
    revision = str(model.stat().st_mtime_ns)
    capabilities = creature_motion_capabilities(model)
    if str(model.stat().st_mtime_ns) != revision:
        raise HTTPException(409, 'The model changed. Refresh its motion options.')
    blender_ready = bool(find_blender())
    defaults = {key: value for key, value in capabilities.get('defaults', {}).items() if key != 'name'}
    return {**capabilities, 'revision': revision, 'defaults': defaults,
            'rig_supported': bool(capabilities['available']),
            'creation_available': blender_ready,
            'creation_reason': '' if blender_ready else 'Configure Blender to save new animations.'}


@app.post('/api/jobs/{identifier}/creature-motions', status_code=202)
async def add_creature_motion(identifier: str, request: Request):
    from creature_motion_edit import validate_creature_motion_recipe

    try:
        payload = await rig_json(request, 16 * 1024)
        if not isinstance(payload, dict):
            raise ValueError('Choose valid creature motion settings.')
        expected = payload.pop('revision', None)
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9]{1,24}', expected):
            raise ValueError('Refresh the motion options before creating a clip.')
        options = validate_creature_motion_recipe(payload)
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error) or 'Invalid creature motion settings.') from error
    async with LOCK:
        if not WORKSPACE_CONFIG:
            require_idle()
        source, record, model = creature_motion_target(identifier)
        refresh_export()
        if identifier in EXPORTING:
            raise HTTPException(409, 'Wait for this model’s export to finish.')
        if str(model.stat().st_mtime_ns) != expected:
            raise HTTPException(409, 'The model changed. Refresh its motion options.')
        if not record['media'].get('bones'):
            raise HTTPException(409, 'Add or import a rig before creating a creature motion.')
        if not find_blender():
            raise HTTPException(503, 'Configure Blender to save new animations.')
        recipe = dict(settings=record['settings'], result=record.get('result') or {}, creature_motion=options)
        queued = await queue_animation_update(source, record, recipe, expected)
        operation = queued.get('operation_id')
        return {**queued, 'state': 'queued', 'status_url': (
            '/api/v1/animation-operations/' + operation if operation else '/api/v1/jobs/' + identifier)}


@app.post('/api/jobs/{identifier}/animations',status_code=202)
async def add_animation(identifier: str,request: Request):
    return await prepare_animations(identifier, request)


@app.get('/api/jobs/{identifier}/animations')
def list_model_animations(identifier: str):
    record = job(identifier)
    media = record['media']
    if not media['available']:
        raise HTTPException(409, 'Wait until this model is available.')
    return dict(animations=media.get('animation_items', []), revision=media.get('animation_revision'))


@app.delete('/api/jobs/{identifier}/animations', status_code=202)
async def remove_animation(identifier: str, request: Request):
    return await prepare_animations(identifier, request, remove=True)


@app.get('/api/animation-operations/{identifier}')
def animation_operation(identifier: str):
    folder = animation_operation_path(identifier)
    state = read_workspace_json(folder/'status.json', {})
    operation = read_workspace_json(folder/'operation.json', {})
    if state.get('state') == 'ready':
        state = dict(state='running', stage='Saving animations to model', progress=98, operation='animation')
    return dict(state, id=identifier, model_id=operation.get('model_id'), name=operation.get('name'))


@app.post('/api/animation-operations/{identifier}/cancel')
async def cancel_animation_operation(identifier: str):
    global LOG_HANDLE
    folder = animation_operation_path(identifier)
    state = read_workspace_json(folder/'status.json', {})
    if state.get('state') in ('done', 'error', 'cancelled'):
        return {'ok': True}
    if ACTIVE == identifier and PROCESS is not None and PROCESS.poll() is None:
        await asyncio.to_thread(stop_worker)
        if LOG_HANDLE:
            LOG_HANDLE.close()
            LOG_HANDLE = None
    for item in STUDIO.workspace.queue['items']:
        if item['id'] == identifier:
            item['cancelled'] = True
    STUDIO.workspace.save_queue()
    write_json(folder/'status.json', dict(state='cancelled', stage='Animation update cancelled', progress=0))
    return {'ok': True}


@app.post('/api/jobs/{identifier}/animation-copy', status_code=202)
async def add_animation_copy(identifier: str, request: Request):
    return await prepare_animations(identifier, request, linked=True)

@app.get('/api/generation-options')
def generation_options_schema():
    return generation_schema()

@app.get('/api/profiles')
def profiles_list():
    try:
        descriptions = {}
        availability = {}
        available = [profile_response(profile, descriptions, availability)
                     for profile in PROFILES.list() if profile['id'] != HUMANOID_EXPERIMENTAL]
        startup = next(profile for profile in available if profile['id'] == STARTUP_PROFILE)
        startup_id = STARTUP_PROFILE if startup['workflow']['available'] else PROPS_PROFILE
        return {'default_profile': DEFAULT_PROFILE, 'startup_profile': startup_id, 'profiles': available}
    except ValueError as error: raise HTTPException(422, str(error))

def profile_response(profile, descriptions, availability):
    settings = profile['settings']
    preset = settings['preset']
    if preset not in STANDALONE_PROFILES:
        return profile
    if preset not in descriptions:
        descriptor = workflow_descriptor(preset)
        descriptor['advanced'] = workflow_advanced_capabilities(preset, descriptor['advanced'])
        descriptor['custom_profiles'] = True
        descriptor.update(workflow_capability(preset))
        descriptions[preset] = descriptor
    workflow = dict(descriptions[preset])
    settings = dict(settings)
    if 'workflow_options' in settings:
        options = workflow_settings(preset, settings['workflow_options'])
        summary = workflow_summary(preset, options)
        summary.pop('seed')
        settings.update(summary)
        workflow.update(resolution=summary['resolution'], target_faces=summary['triangles'],
                        texture_size=summary['texture_size'])
        key = (preset, summary['geometry_backend'], summary['texture_method'])
        if key not in availability:
            availability[key] = workflow_capability(*key)
        workflow.update(availability[key])
    elif preset == 'high_detail_native':
        material = next(stage for stage in workflow['advanced'] if stage['id'] == '25')
        defaults = {field['name']: field['default'] for field in material['fields']}
        size = defaults['ultratex_size'] if defaults['texture_method'] == 'ultratex' else defaults['texture_size']
        settings.update(texture_method=defaults['texture_method'], texture_size=size)
        workflow['texture_size'] = size
    return {**profile, 'settings': settings, 'workflow': workflow}


def workflow_capability(profile_id, backend=None, texture_method=None):
    if profile_id == 'high_detail_native':
        from native_high_detail import TRELLIS_GEOMETRY, runtime_status, ultratex_status
        capability = runtime_status(geometry_backend=backend or TRELLIS_GEOMETRY)
        if capability['available'] and texture_method == 'ultratex':
            return ultratex_status()
        return capability
    if profile_id == 'game_asset_native':
        from native_game_profile import runtime_status
        return runtime_status(backend or 'pixal3d')
    return dict(available=False, reason='Choose a current standalone profile.')

def workflow_advanced_capabilities(profile_id, stages):
    from copy import deepcopy

    stages = deepcopy(stages)
    for stage in stages:
        for field in stage['fields']:
            for choice in field.get('choices', ()):
                if backend := choice.get('requires_native_geometry'):
                    from native_high_detail import runtime_status
                    choice.update(runtime_status(geometry_backend=backend))
                if choice.get('requires_native_ultratex'):
                    from native_high_detail import ultratex_status
                    choice.update(ultratex_status())
                    if choice['available']:
                        field['default'] = choice['value']
                if profile_id == 'game_asset_native' and stage['id'] == '316' and field['name'] == 'value':
                    from native_game_profile import runtime_status
                    choice.update(runtime_status('trellis2' if choice['value'] else 'pixal3d'))
    return stages


def require_workflow_options(profile_id, options):
    backend = workflow_geometry(profile_id, options)
    texture_method = options['25']['texture_method'] if profile_id == 'high_detail_native' else None
    capability = workflow_capability(profile_id, backend, texture_method)
    if not capability['available']:
        raise HTTPException(503, capability['reason'])
    if profile_id == 'high_detail_native' and backend == 'pixal3d':
        from native_high_detail import DEFAULTS
        if options['17'] != DEFAULTS['17']:
            raise HTTPException(422, 'First TRELLIS.2 pass settings apply to TRELLIS.2 GGUF geometry only.')

def new_workflow_options(profile_id, overrides, *, saved=None):
    """Use local defaults for new submissions and saved settings for named recipes."""
    options = workflow_settings(profile_id, overrides, saved=saved)
    if profile_id == 'high_detail_native' and saved is None:
        material = options['25']
        if 'texture_method' not in (overrides or {}).get('25', {}) and not material['bake_on_vertices']:
            from native_high_detail import ultratex_status
            if ultratex_status()['available']:
                material['texture_method'] = 'ultratex'
    return options


async def save_profile(request, identifier=None):
    if identifier in GENERATION_PRESETS or identifier in WORKFLOW_PROFILES or identifier == DETAIL_STUDY:
        raise HTTPException(403, 'This built-in profile is protected. Save a custom copy instead.')
    async with LOCK:
        try:
            raw = await request.body()
            if len(raw) > 26000: raise ValueError('Profile is too large.')
            return profile_response(PROFILES.save(json.loads(raw), identifier), {}, {})
        except KeyError: raise HTTPException(404, 'Profile not found.')
        except (ValueError, TypeError, OverflowError) as error: raise HTTPException(400, str(error))

@app.post('/api/profiles', status_code=201)
async def create_profile(request: Request):
    return await save_profile(request)

@app.put('/api/profiles/{identifier}')
async def update_profile(identifier: str, request: Request):
    return await save_profile(request, identifier)

@app.delete('/api/profiles/{identifier}')
async def delete_profile(identifier: str):
    if identifier in GENERATION_PRESETS or identifier in WORKFLOW_PROFILES or identifier == DETAIL_STUDY:
        raise HTTPException(403, 'This built-in profile is protected.')
    async with LOCK:
        try:
            PROFILES.delete(identifier)
        except KeyError as error:
            raise HTTPException(404, 'Profile not found.') from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except OSError as error:
            raise HTTPException(503, 'Unable to save the profile list.') from error
    return dict(id=identifier, deleted=True)

@app.get('/api/jobs')
def jobs():
    records = [job_record(folder) for folder in sorted(OUTPUT.iterdir(), key=lambda p:p.stat().st_mtime, reverse=True)
               if folder.is_dir() and re.fullmatch(r'[a-f0-9]{32}',folder.name) and folder.resolve().parent==OUTPUT]
    return [item for item in records if item]

def write_json(path, data):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
    temp.replace(path)

def active_job(identifier):
    return identifier==TERRAIN_ACTIVE or (identifier in (ACTIVE, FINISH_SOURCE) and PROCESS is not None and PROCESS.poll() is None)

@app.patch('/api/jobs/{identifier}/metadata')
async def metadata(identifier: str, request: Request):
    async with LOCK:
        folder=job_path(identifier)
        try:
            values=validate_metadata(await request.json())
        except (ValueError,TypeError): raise HTTPException(400,'Invalid metadata.')
        path=folder/'metadata.json'
        current=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        current.update(values);current['updated']=time.time();write_json(path,current)
        # Include current provenance on the next Blender archive download.
        cached=folder/'Blender'/'manifest.json'
        if cached.exists():
            cache=json.loads(cached.read_text(encoding='utf-8'));cache['export_version']=0;write_json(cached,cache)
        return current

@app.delete('/api/jobs/{identifier}')
async def trash_job(identifier: str):
    async with LOCK:
        folder=job_path(identifier)
        refresh_export()
        if active_job(identifier) or identifier in EXPORTING: raise HTTPException(409,'Wait for generation or export to finish before removing this model.')
        # Windows cannot move the folder while its completed worker log is open.
        if identifier==ACTIVE and LOG_HANDLE and PROCESS is not None and PROCESS.poll() is not None:
            LOG_HANDLE.close()
        TRASH.mkdir(parents=True,exist_ok=True)
        destination=TRASH/identifier
        # Verify both resolved absolute targets before moving this one asset directory.
        if folder.resolve().parent!=OUTPUT or destination.resolve().parent!=TRASH: raise HTTPException(403)
        if destination.exists(): raise HTTPException(409,'This model is already in the trash.')
        try: folder.rename(destination)
        except OSError: raise HTTPException(409,'This model is being used by another operation. Try again when it finishes.')
        STUDIO.cancel_pending(identifier,destination)
        write_json(destination/'trashed.json',{'deleted_at':time.time()})
        return {'ok':True,'restorable':True}

@app.get('/api/trash')
def trash_list():
    if not TRASH.exists(): return []
    records=[]
    for folder in TRASH.iterdir():
        if folder.is_dir() and re.fullmatch(r'[a-f0-9]{32}',folder.name) and folder.resolve().parent==TRASH:
            record=job_record(folder)
            if record: records.append(record)
    return sorted(records,key=lambda j:j['metadata'].get('updated',j['settings'].get('created',0)),reverse=True)

@app.post('/api/trash/{identifier}/restore')
async def restore_job(identifier: str):
    async with LOCK:
        if not re.fullmatch(r'[a-f0-9]{32}',identifier): raise HTTPException(404)
        source=TRASH/identifier;destination=OUTPUT/identifier
        if source.resolve().parent!=TRASH or destination.resolve().parent!=OUTPUT: raise HTTPException(403)
        if not source.is_dir(): raise HTTPException(404)
        if destination.exists(): raise HTTPException(409,'A model with this identifier already exists.')
        try: source.rename(destination)
        except OSError: raise HTTPException(409,'This model is still in use. Try again when the operation finishes.')
        return {'ok':True,'id':identifier}

@app.get('/api/jobs/{identifier}/inspect')
async def inspect_job(identifier: str):
    from studio_assets import prepare_blender
    async with LOCK:
        folder=job_path(identifier)
        if job_record(folder)['state']!='done': raise HTTPException(409,'The model must be complete.')
        try: return await asyncio.to_thread(prepare_blender,folder,build_archive=not WORKSPACE_CONFIG)
        except (ValueError,OSError) as error: raise HTTPException(422,str(error))

@app.get('/api/jobs/{identifier}/diagnostics')
async def diagnostics(identifier: str,force: bool=False):
    from model_diagnostics import diagnose
    async with LOCK:
        folder=job_path(identifier)
        if job_record(folder)['state']!='done':raise HTTPException(409,'Choose a completed model.')
        try:return await asyncio.to_thread(diagnose,folder,force)
        except (ValueError,OSError,KeyError,TypeError) as error:raise HTTPException(422,'Unable to inspect this model: '+str(error))

@app.get('/api/jobs/{identifier}/diagnostics/report')
def diagnostics_report(identifier: str):
    path=job_path(identifier)/'Diagnostics'/'report.json'
    if not path.is_file():raise HTTPException(404,'Run model diagnostics first.')
    return FileResponse(path,filename='TRELLIS-diagnostics-'+identifier[:8]+'.json',media_type='application/json')

@app.patch('/api/library/metadata')
async def batch_metadata(request: Request):
    async with LOCK:
        try:
            data=await request.json()
            if not isinstance(data,dict) or set(data)-{'ids','changes','add_tags'}:raise ValueError('Invalid batch request.')
            ids=validate_ids(data.get('ids'));changes=data.get('changes',{})
            if set(changes)-{'category','asset_type','review_state','favorite'}:raise ValueError('Unsupported grouped edit.')
            values=validate_metadata(changes);added=validate_metadata({'tags':data.get('add_tags',[])})['tags']
            if not values and not added:raise ValueError('Choose at least one change.')
            planned=[]
            for identifier in ids:
                folder=job_path(identifier);path=folder/'metadata.json';original=path.read_bytes() if path.exists() else None
                old=json.loads(original) if original is not None else {}
                tags=validate_metadata({'tags':list(dict.fromkeys(old.get('tags',[])+added))})['tags']
                updated={**old,**values,'updated':time.time()}
                if added:updated['tags']=tags
                planned.append((path,original,updated))
        except (ValueError,TypeError,KeyError) as error:raise HTTPException(400,str(error))
        applied=[]
        try:
            for path,original,updated in planned:
                write_json(path,updated);applied.append((path,original))
            for path,_,_ in planned:
                cache=path.parent/'Blender'/'manifest.json'
                if cache.is_file():
                    try:cached=json.loads(cache.read_text(encoding='utf-8'));cached['export_version']=0;write_json(cache,cached)
                    except (ValueError,OSError):pass
        except OSError:
            for path,original in reversed(applied):
                if original is None:path.unlink(missing_ok=True)
                else:path.write_bytes(original)
            raise HTTPException(409,'Grouped editing failed; previous details were restored.')
        return dict(updated=len(ids),ids=ids)

@app.post('/api/library/trash')
async def batch_trash(request: Request):
    async with LOCK:
        try:
            data=await request.json();ids=validate_ids(data.get('ids'))
        except (ValueError,TypeError,AttributeError):raise HTTPException(400,'Choose 1–100 distinct models.')
        refresh_export();planned=[];TRASH.mkdir(parents=True,exist_ok=True)
        for identifier in ids:
            source=job_path(identifier);destination=TRASH/identifier
            if source.resolve().parent!=OUTPUT or destination.resolve().parent!=TRASH:raise HTTPException(403)
            if active_job(identifier) or identifier in EXPORTING:raise HTTPException(409,'A selected model is being generated or exported. Nothing was moved.')
            if destination.exists():raise HTTPException(409,'A selected model already exists in the trash. Nothing was moved.')
            planned.append((source,destination))
        moved=[]
        try:
            for source,destination in planned:
                source.rename(destination);moved.append((source,destination));write_json(destination/'trashed.json',dict(deleted_at=time.time()))
        except OSError:
            for source,destination in reversed(moved):
                if destination.is_dir() and not source.exists():destination.rename(source)
            raise HTTPException(409,'A selected model is in use; moved models were restored.')
        for source,destination in moved:STUDIO.cancel_pending(source.name,destination)
        return dict(moved=len(ids),ids=ids,restorable=True)

def export_folder(identifier):
    if not re.fullmatch('[a-f0-9]{32}',identifier):raise HTTPException(404)
    folder=EXPORT_ROOT/identifier
    if folder.resolve().parent!=EXPORT_ROOT or not folder.is_dir():raise HTTPException(404)
    return folder

def refresh_export():
    if EXPORT_PROCESS is not None and EXPORT_PROCESS.poll() is not None and EXPORT_TASK:
        request=json.loads((EXPORT_ROOT/EXPORT_TASK/'request.json').read_text(encoding='utf-8'))
        EXPORTING.difference_update(request['ids'])

def stop_export():
    if EXPORT_PROCESS is None or EXPORT_PROCESS.poll() is not None:return
    import psutil
    try:children=psutil.Process(EXPORT_PROCESS.pid).children(recursive=True)
    except psutil.NoSuchProcess:children=[]
    for child in reversed(children):
        try:child.terminate()
        except psutil.NoSuchProcess:pass
    EXPORT_PROCESS.terminate()
    try:EXPORT_PROCESS.wait(timeout=8)
    except subprocess.TimeoutExpired:EXPORT_PROCESS.kill();EXPORT_PROCESS.wait()
    _,alive=psutil.wait_procs(children,timeout=3)
    for child in alive:
        try:child.kill()
        except psutil.NoSuchProcess:pass
    refresh_export()

@app.post('/api/exports/preflight')
async def export_preflight(request: Request):
    from export_preflight import asset_report, options
    from target_export import TARGETS
    try:
        data = await request.json()
        target = data.get('target')
        if not isinstance(target, str) or target not in TARGETS or target == 'library':
            raise ValueError('Choose a model export destination.')
        ids = validate_ids(data.get('ids'), 50)
        budget = options(data.get('budget'), target)
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(400, str(error))
    reports = []
    async with LOCK:
        for identifier in ids:
            folder = job_path(identifier)
            record = job_record(folder)
            if not record or record['state'] != 'done' or active_job(identifier):
                raise HTTPException(409, 'Every selected model must be complete.')
            if target == 'print' and record['settings'].get('asset_type') != 'print':
                raise HTTPException(409, 'Prepare a dedicated print copy first.')
            try:
                reports.append(await asyncio.to_thread(
                    asset_report, folder, target, budget, metadata=record['metadata']
                ))
            except (ValueError, OSError, KeyError, TypeError, IndexError, OverflowError) as error:
                name = record['metadata'].get('display_name', record['settings']['name'])
                raise HTTPException(422, f'Could not check {name}: {error}')
    return dict(target=target, budget=budget, models=reports)


@app.post('/api/exports',status_code=202)
async def create_export(request: Request):
    global EXPORT_PROCESS,EXPORT_TASK,EXPORT_LOG
    from api_contract import Export
    from export_preflight import options, verified_hashes
    from target_export import TARGETS
    async with LOCK:
        try:
            data=await request.json();target=data.get('target');ids=validate_ids(data.get('ids'),500 if target=='library' else 50)
            if target not in TARGETS:raise ValueError('Unknown export target.')
            export = Export(ids=ids, target=target, material_adjustments=data.get('material_adjustments', {}))
            material_adjustments = {key: value.model_dump() for key, value in export.material_adjustments.items()
                                    if value.roughness or value.metalness}
            preflight = data.get('preflight')
            if preflight is not None:
                if target == 'library' or not isinstance(preflight, dict):
                    raise ValueError('Use export checks for model packages.')
                preflight = dict(budget=options(preflight.get('budget'), target),
                                 source_hashes=verified_hashes(preflight.get('source_hashes'), ids))
        except (ValueError,TypeError,AttributeError) as error:raise HTTPException(400,str(error))
        refresh_export()
        if EXPORT_PROCESS is not None and EXPORT_PROCESS.poll() is None:raise HTTPException(409,'An export is already running.')
        if target in ('blender','unity','unreal') and not find_blender():raise HTTPException(503,'Blender is required for this export.')
        assets=[]
        for identifier in ids:
            folder=job_path(identifier);record=job_record(folder)
            if record['state']!='done' or active_job(identifier) or not (folder/'model.glb').is_file():raise HTTPException(409,'Every selected model must be complete.')
            if target=='print' and (record['settings'].get('asset_type')!='print' or not (folder/'model.stl').is_file()):raise HTTPException(409,'Create a dedicated print copy for each selected model first.')
            try:validate_glb(folder/'model.glb')
            except ValueError as error:raise HTTPException(422,str(error))
            assets.append((folder,record))
        if sum((p/'model.glb').stat().st_size for p,_ in assets)>5*1024**3:raise HTTPException(400,'Export fewer models at once (5 GB input limit).')
        identifier=uuid.uuid4().hex;task=EXPORT_ROOT/identifier;task.mkdir(parents=True)
        for folder,record in assets:
            snapshot=task/'sources'/folder.name;snapshot.mkdir(parents=True)
            for name in ('model.glb','model.stl','print-report.json'):
                path=folder/name
                if path.is_file():
                    shutil.copy2(path,snapshot/name)
            for name,key in [('settings.json','settings'),('metadata.json','metadata')]:write_json(snapshot/name,record[key])
            if (folder/'Diagnostics/report.json').is_file():shutil.copy2(folder/'Diagnostics/report.json',snapshot/'diagnostics.json')
        if target=='library':
            for folder,record in assets:
                snapshot=task/'sources'/folder.name
                shutil.copytree(folder,snapshot,dirs_exist_ok=True,ignore=shutil.ignore_patterns('Blender','rig-work','before-animations','.*','*.log','*.tmp','*.py','*.pid',*(() if data.get('include_revisions') else ('Revisions',))))
                write_json(snapshot/'metadata.json',record['metadata'])
        else:
            for folder,record in assets:
                snapshot=task/'sources'/folder.name
                for child in ('LODs','Collisions'):
                    if (folder/child).is_dir():shutil.copytree(folder/child,snapshot/child)
                for filename in ('lod-manifest.json','physics.json','motion-report.json'):
                    if (folder/filename).is_file():shutil.copy2(folder/filename,snapshot/filename)
        write_json(task/'request.json',dict(ids=ids,target=target,created=time.time(),include_revisions=bool(data.get('include_revisions')),workspace=STUDIO.workspace.data,profiles=PROFILES.list(),preflight=preflight,material_adjustments=material_adjustments))
        write_json(task/'status.json',dict(state='queued',progress=0,stage='Preparing export',target=target,count=len(ids)))
        if EXPORT_LOG:EXPORT_LOG.close()
        EXPORT_LOG=(task/'export.log').open('w',encoding='utf-8');env=os.environ.copy();env['PYTHONUTF8']='1'
        EXPORT_PROCESS=subprocess.Popen([sys.executable,'-u',str(ROOT/'export_worker.py'),str(task)],cwd=ROOT,env=env,
            stdout=EXPORT_LOG,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        EXPORT_TASK=identifier;EXPORTING.update(ids)
        return dict(id=identifier)

@app.get('/api/exports/{identifier}')
def export_status(identifier: str):
    folder=export_folder(identifier);refresh_export();data=json.loads((folder/'status.json').read_text(encoding='utf-8'))
    if data['state'] in ('queued','running') and (identifier!=EXPORT_TASK or EXPORT_PROCESS is None or EXPORT_PROCESS.poll() is not None):
        data.update(state='error',stage='Export interrupted',error='The export process stopped. Original models are unchanged.');write_json(folder/'status.json',data)
    return dict(data,id=identifier)

@app.post('/api/exports/{identifier}/cancel')
async def cancel_export(identifier: str):
    async with LOCK:
        folder=export_folder(identifier)
        if identifier==EXPORT_TASK and EXPORT_PROCESS is not None and EXPORT_PROCESS.poll() is None:
            await asyncio.to_thread(stop_export);write_json(folder/'status.json',dict(state='cancelled',progress=0,stage='Export cancelled; original models preserved'))
        return dict(ok=True)

@app.get('/api/exports/{identifier}/download')
def export_download(identifier: str):
    folder=export_folder(identifier);data=export_status(identifier)
    if data['state']!='done' or not (folder/'package.zip').is_file():raise HTTPException(409,'The export is not ready.')
    return FileResponse(folder/'package.zip',filename='TRELLIS-'+data['target']+'-'+identifier[:8]+'.zip',media_type='application/zip')

@app.get('/api/jobs/{identifier}/blender/{filename}')
def blender_file(identifier: str, filename: str):
    folder=job_path(identifier)
    if not re.fullmatch(r'(mesh-[0-9]{2}-(BaseColor|MetallicRoughness|Roughness|Metallic|Normal|Occlusion|Emissive|UV)\.(png|svg)|manifest\.json|LIRE-BLENDER\.txt)',filename): raise HTTPException(404)
    path=folder/'Blender'/filename
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path)

@app.get('/api/jobs/{identifier}')
def job(identifier: str):
    return job_record(job_path(identifier))

@app.get('/api/jobs/{identifier}/rig-preview')
def rig_preview(identifier: str):
    folder = job_path(identifier)
    path = folder / 'rig-preview.json'
    if not path.is_file() or path.resolve() != path:
        return dict(ready=False)
    preview = dict(ready=True, **read_workspace_json(path))
    settings = read_workspace_json(folder / 'settings.json', {})
    if settings.get('creature_phase'):
        from creature_rig import MAX_CREATURE_BONES
        preview.update(revision=rig_digest(path), max_bones=MAX_CREATURE_BONES)
        edit = read_workspace_json(folder / 'creature-edit.json', {})
        if edit.get('revision') == preview['revision']:
            preview.update(edited_bones=edit['bones'], motion=edit['motion'])
    return preview

@app.get('/api/jobs/{identifier}/rig-preview-model')
def rig_preview_model(identifier: str):
    folder=job_path(identifier)
    settings = read_workspace_json(folder / 'settings.json', {})
    path = folder / 'rig-work' / 'input.glb'
    if settings.get('creature_phase') or not path.is_file():
        path = folder / 'source.glb'
    if not path.is_file() or path.resolve() != path:
        raise HTTPException(404)
    return FileResponse(path, media_type='model/gltf-binary')

async def read_generation_image(upload, label='Image', restrict_formats=False, preserve_original=False):
    content = await upload.read(25 * 1024 * 1024 + 1)
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, f'{label} exceeds the 25 MB limit.')
    try:
        with Image.open(io.BytesIO(content)) as original:
            if restrict_formats and original.format not in ('PNG', 'JPEG', 'WEBP'):
                raise ValueError('Use PNG, JPEG or WebP.')
            if original.width * original.height > 40_000_000:
                raise ValueError('Image too large (40 megapixels maximum).')
            source = ImageOps.exif_transpose(original).convert('RGBA')
            source.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            if preserve_original:
                if getattr(original, 'n_frames', 1) != 1:
                    raise ValueError('Choose a still image.')
                return source, content, {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}[original.format]
            return source
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise HTTPException(400, f'{label} could not be read: {error}') from error


async def reference_sources(uploads, engine):
    multiview = engine == 'pixal3d'
    sources = {}
    for role, upload in zip(VIEW_FILES, uploads):
        if upload is None:
            continue
        label = f'{role.title()} view' if multiview else 'Image'
        source = await read_generation_image(upload, label, restrict_formats=engine != 'trellis2')
        if source.getchannel('A').getextrema()[1] == 0:
            raise HTTPException(400, f'{label} is fully transparent.')
        sources[role] = source
    return sources


async def prepare_reference_uploads(sources, raw_edits, engine, remove_bg):
    try:
        edits = parse_reference_edits(raw_edits, sources)
        cutouts, frames = await asyncio.to_thread(
            prepare_references, sources, edits, engine, remove_bg, ROOT / 'models' / 'rembg' / 'u2net.onnx')
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except BackgroundRemovalError as error:
        raise HTTPException(503, str(error)) from error
    return edits, cutouts, frames


def encode_reference_preview(image, max_size=None):
    if max_size:
        image = image.copy()
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def reference_preview_views(sources, cutouts, frames, engine, preview_size=None):
    views = []
    for role, source in sources.items():
        frame = frames[role]
        if engine in ('pixal3d', 'pixal3d_single'):
            frame = Image.fromarray((premultiplied_rgb(frame) * 255).astype('uint8'))
        views.append(dict(role=role, width=source.width, height=source.height,
                          original=encode_reference_preview(source, max_size=preview_size or 768),
                          cutout=encode_reference_preview(cutouts[role], max_size=preview_size or 768),
                          framed=encode_reference_preview(frame, max_size=preview_size)))
    return views


async def prepare_reference_preview(image, left_image=None, back_image=None, right_image=None,
                                    *, engine, remove_bg, reference_edits, preview_size=None):
    if engine not in ('trellis2', 'pixal3d', 'pixal3d_single'):
        raise HTTPException(400, 'Unknown reference engine.')
    if engine != 'pixal3d' and any((left_image, back_image, right_image)):
        raise HTTPException(400, 'Additional views require Pixal3D Multi-view.')
    sources = await reference_sources((image, left_image, back_image, right_image), engine)
    _, cutouts, frames = await prepare_reference_uploads(sources, reference_edits, engine, remove_bg)
    views = await asyncio.to_thread(reference_preview_views, sources, cutouts, frames, engine, preview_size)
    return dict(engine=engine, views=views)


@app.post('/api/reference-preview')
async def reference_preview(
    image: Annotated[UploadFile, File()],
    left_image: Annotated[UploadFile | None, File()] = None,
    back_image: Annotated[UploadFile | None, File()] = None,
    right_image: Annotated[UploadFile | None, File()] = None,
    engine: Annotated[str, Form()] = 'trellis2',
    remove_bg: Annotated[bool, Form()] = True,
    reference_edits: Annotated[str, Form()] = '',
):
    return await prepare_reference_preview(image, left_image, back_image, right_image, engine=engine,
                                           remove_bg=remove_bg, reference_edits=reference_edits)


@app.post('/api/generate-multiview', status_code=202)
async def generate_multiview(
    image: Annotated[UploadFile, File()],
    left_image: Annotated[UploadFile | None, File()] = None,
    back_image: Annotated[UploadFile | None, File()] = None,
    right_image: Annotated[UploadFile | None, File()] = None,
    side_angle: Annotated[int, Form()] = 40,
    profile_id: Annotated[str, Form()] = 'character_pixal',
    name: Annotated[str, Form()] = 'Character',
    height_m: Annotated[float, Form()] = 1.7,
    seed: Annotated[int, Form()] = 42,
    triangles: Annotated[int, Form()] = 0,
    texture_size: Annotated[int, Form()] = 0,
    parent_id: Annotated[str, Form()] = '',
    reference_prompt: Annotated[str, Form()] = '',
    keep_finishing_source: Annotated[bool | None, Form()] = None,
    repair_surface: Annotated[bool, Form()] = False,
    hd_reconstruction: Annotated[bool, Form()] = False,
    reproject_face: Annotated[bool, Form()] = False,
    queue_priority: Annotated[int, Form()] = 0,
    reference_edits: Annotated[str, Form()] = '',
    preset: Annotated[str, Form()] = '',
    asset_type: Annotated[str, Form()] = 'humanoid',
    master_finishing: Annotated[str | None, Form()] = None,
    texture_method: Annotated[str | None, Form()] = None,
):
    profile = next((p for p in PIXAL_PROFILES if p['id'] == profile_id), None)
    if profile is None:
        try:
            saved_profile = next((item for item in PROFILES.list() if item['id'] == profile_id), None)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if saved_profile is None:
            raise HTTPException(400, 'Unknown generation profile. Choose a profile advertised by Studio.')
        if saved_profile['settings']['preset'] in WORKFLOW_PROFILES:
            raise HTTPException(400, 'This profile uses one reference image.')
        profile = dict(saved_profile['settings'], id=saved_profile['id'], name=saved_profile['name'],
                       keep_finishing_source=saved_profile.get('keep_finishing_source', False))
        profile.pop('master_finishing', None)
        profile.pop('retexture_after_refinement', None)
    preset = preset or profile.get('preset', profile['id'])
    if preset == FIGURE_MASTER:
        raise HTTPException(400, 'Figure Master uses TRELLIS.2 Single image. Choose another profile for Pixal3D.')
    if preset != 'character_pixal' and preset not in GENERATION_PRESETS:
        raise HTTPException(400, 'Unknown generation preset.')
    if repair_surface and preset != DENSE_MASTER:
        raise HTTPException(400, 'Choose HD Master to repair the surface during generation.')
    if preset == HUMANOID_EXPERIMENTAL:
        asset_type = 'humanoid'
    triangles = triangles or profile['triangles']
    texture_size = texture_size or profile['texture_size']
    try:
        texture_method = resolve_texture_method(texture_method)
        validate_texture_method(texture_method, engine='pixal3d', view_mode='multi', texture_size=texture_size)
        validate_hd_reconstruction(hd_reconstruction, preset=preset, resolution=1024,
                                   repair_surface=repair_surface)
        validate_create_master_finishing(master_finishing, preset=preset, resolution=1024,
                                         triangles=triangles, repair_surface=repair_surface,
                                         hd_reconstruction=hd_reconstruction, engine='pixal3d')
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if keep_finishing_source is None:
        keep_finishing_source = profile.get('keep_finishing_source', False)
    keep_finishing_source = keep_finishing_source or preset in (DENSE_MASTER, HUMANOID_EXPERIMENTAL)
    if (side_angle not in (40, 90) or not 0.01 <= height_m <= 100
        or not 0 <= seed <= 2147483647 or not -2 <= queue_priority <= 2
        or not TRIANGLE_MIN <= triangles <= TRIANGLE_MAX
        or texture_size not in PIXAL_TEXTURE_SIZES or asset_type not in ASSET_TYPES
        or len(name) > 80 or len(reference_prompt) > 6000):
        raise HTTPException(400, 'Invalid Pixal3D Multi-view settings.')
    if not any((left_image, back_image, right_image)):
        raise HTTPException(400, 'Add at least one side or back image to the front view.')
    runtime = pixal3d_capabilities()
    if not runtime['ready']:
        raise HTTPException(503, runtime['message'])
    if reproject_face and not runtime['face_projection']['ready']:
        raise HTTPException(503, runtime['face_projection']['message'])
    require_create_master_finishing(master_finishing)
    sources = await reference_sources((image, left_image, back_image, right_image), 'pixal3d')
    azimuths = dict(front=0, left=side_angle, back=180, right=-side_angle)
    views = [dict(role=role, azimuth=azimuths[role], file=VIEW_FILES[role]) for role in sources]
    if reference_edits:
        edits, _, frames = await prepare_reference_uploads(sources, reference_edits, 'pixal3d', True)
    else:
        frames = {}
    generation_options = dict(PIXAL_CHARACTER_OPTIONS)
    if preset == HUMANOID_EXPERIMENTAL:
        generation_options['mesh_mode'] = 'direct'
    elif preset == DENSE_MASTER:
        generation_options.update(DENSE_MASTER_FINISHING)
    safe_name = re.sub(r'[^A-Za-z0-9_-]+', '-', name.strip()).strip('-')[:50] or 'Character'
    settings = dict(name=safe_name, engine='pixal3d', preset=preset, view_mode='multi',
                    texture_method=texture_method,
                    profile=dict(id=profile['id'], name=profile['name']),
                    asset_type=asset_type, height_m=height_m, seed=seed,
                    triangles=triangles, texture_size=texture_size,
                    resolution=1024, pipeline_type='pixal3d_1024_multiview',
                    generation_options=generation_options, settings_version=3, created=time.time(),
                    views=views, side_angle=side_angle, keep_finishing_source=keep_finishing_source)
    settings['profile_snapshot'] = dict(profile, engine='pixal3d', preset=preset, resolution=1024, triangles=settings['triangles'],
        texture_size=settings['texture_size'], generation_options=generation_options,
        keep_finishing_source=keep_finishing_source)
    if preset == DENSE_MASTER and not (hd_reconstruction or master_finishing):
        settings['adaptive_finishing'] = True
        settings['profile_snapshot']['adaptive_finishing'] = True
    if repair_surface:
        settings['repair_surface'] = True
        settings['profile_snapshot']['repair_surface'] = True
    if hd_reconstruction:
        settings['hd_reconstruction'] = True
        settings['profile_snapshot']['hd_reconstruction'] = True
    assign_create_master_finishing(settings, master_finishing)
    if reference_edits:
        settings['reference_edits'] = edits
    if reproject_face:
        settings['reproject_face'] = True
    if parent_id:
        settings['parent_id'] = parent_id
    identifier = uuid.uuid4().hex
    folder = OUTPUT / identifier
    async with LOCK:
        if parent_id:
            job_path(parent_id)
        with TemporaryDirectory(prefix='.pixal3d-', dir=OUTPUT) as staging:
            staged = Path(staging)
            for role, source in sources.items():
                source.save(staged / VIEW_FILES[role])
            for role, frame in frames.items():
                frame.save(staged / PREPARED_VIEWS[role])
            write_json(staged / 'settings.json', settings)
            metadata = dict(display_name=name.strip() or safe_name, reference_prompt=reference_prompt.strip(),
                            review_state='review', favorite=False, tags=[])
            if asset_type == 'humanoid':
                metadata['category'] = 'Characters'
            write_json(staged / 'metadata.json', metadata)
            write_json(staged / 'status.json', dict(state='queued', stage='Waiting in queue', progress=0))
            staged.rename(folder)
        try:
            STUDIO.workspace.enqueue(identifier, queue_priority)
        except OSError as error:
            write_json(folder / 'status.json', dict(state='error', stage='Unable to save queue',
                                                    progress=0, error=str(error)))
            raise HTTPException(503, 'Unable to save the generation queue.') from error
    return {'id': identifier, 'queued': True}


async def generate_workflow(image, workflow_id, *, options, name, height_m, asset_type, reference_prompt,
                            parent_id, queue_only, queue_priority, queue_hold, profile=None):
    if (not 0.01 <= height_m <= 100 or asset_type not in ASSET_TYPES or len(name) > 80
            or len(reference_prompt) > 6000 or not -2 <= queue_priority <= 2):
        raise HTTPException(400, 'Invalid generation settings.')
    require_workflow_options(workflow_id, options)
    source, content, extension = await read_generation_image(image, restrict_formats=True, preserve_original=True)
    if source.getchannel('A').getextrema()[1] == 0:
        raise HTTPException(400, 'Image is fully transparent.')
    profile = profile or {'id': workflow_id, 'name': STANDALONE_PROFILES[workflow_id]['name']}
    safe_name = re.sub(r'[^A-Za-z0-9_-]+', '-', name.strip()).strip('-')[:50] or 'Object'
    filename = 'workflow-input' + extension
    settings = dict(name=safe_name, preset=workflow_id, workflow_id=workflow_id, workflow_input=filename,
                    profile=dict(id=profile['id'], name=profile['name']), engine='studio_workflow', view_mode='single',
                    pipeline_type=workflow_id, height_m=height_m, asset_type=asset_type,
                    workflow_options=options,
                    settings_version=4, created=time.time(), **workflow_summary(workflow_id, options))
    if parent_id:
        settings['parent_id'] = parent_id
    identifier = uuid.uuid4().hex
    folder = OUTPUT / identifier
    async with LOCK:
        if parent_id:
            job_path(parent_id)
        if not queue_only and (TERRAIN_ACTIVE or (PROCESS is not None and PROCESS.poll() is None)):
            raise HTTPException(409, 'A generation is already running; add the next image to the queue.')
        with TemporaryDirectory(prefix='.workflow-generation-', dir=OUTPUT) as staging:
            staged = Path(staging)
            (staged / filename).write_bytes(content)
            source.save(staged / 'input.png')
            write_json(staged / 'settings.json', settings)
            write_json(staged / 'metadata.json', dict(display_name=name.strip() or safe_name,
                       reference_prompt=reference_prompt.strip(), review_state='review', favorite=False, tags=[]))
            if queue_only:
                write_json(staged / 'status.json', dict(state='queued', stage='Waiting in queue', progress=0))
            staged.rename(folder)
        if queue_only:
            try:
                STUDIO.workspace.enqueue(identifier, queue_priority, queue_hold)
            except OSError as error:
                write_json(folder / 'status.json', dict(state='error', stage='Unable to save queue', progress=0))
                raise HTTPException(503, 'Unable to save the generation queue.') from error
            return {'id': identifier, 'queued': True}
        launch_worker(folder, 'worker.py')
        return {'id': identifier}


@app.post('/api/generate')
async def generate(image: UploadFile = File(...), name: str = Form('Object'), preset: str = Form(DEFAULT_PROFILE),
                   height_m: float = Form(1), seed: int | None = Form(None), remove_bg: bool = Form(True),
                   triangles: int = Form(0), asset_type: str = Form('object'), texture_size: int = Form(0),
                   reference_prompt: str = Form(''), resolution: int | None = Form(None), advanced_settings: str = Form(''),
                   keep_finishing_source: Annotated[bool,Form()]=False,
                   repair_surface: Annotated[bool,Form()]=False,
                   hd_reconstruction: Annotated[bool,Form()]=False,
                   profile_id: str = Form(''), parent_id: Annotated[str,Form()]='', queue_only: Annotated[bool,Form()]=False, queue_priority: Annotated[int,Form()]=0, queue_hold: Annotated[bool,Form()]=False,
                   reference_edits: Annotated[str, Form()] = '',
                   engine: Annotated[str, Form()] = 'trellis2',
                   master_finishing: Annotated[str | None, Form()] = None,
                   reproject_face: Annotated[bool, Form()] = False,
                   retexture_after_refinement: Annotated[bool, Form()] = False,
                   multiview_texture_refinement: Annotated[bool | None, Form()] = None,
                   texture_method: Annotated[str | None, Form()] = None,
                   workflow_options: Annotated[str, Form()] = ''):
    if WORKSPACE_CONFIG:
        queue_only = True
    profile = None
    if profile_id.startswith('user_'):
        try: profile = next((p for p in PROFILES.list() if p['id'] == profile_id), None)
        except ValueError as error: raise HTTPException(422, str(error)) from error
        if profile is None: raise HTTPException(400, 'Profile not found; reload profiles.')
    workflow_id = profile_id if profile_id in WORKFLOW_PROFILES else preset if preset in WORKFLOW_PROFILES else None
    custom_workflow = profile is not None and profile['settings']['preset'] in STANDALONE_PROFILES
    if custom_workflow:
        workflow_id = profile['settings']['preset']
    if workflow_id:
        if workflow_id in COMFY_PROFILES:
            raise HTTPException(409, 'Choose a current generation profile in Studio.')
        recipe = STANDALONE_PROFILES[workflow_id]
        selected_id = profile_id if custom_workflow else workflow_id
        if (preset not in (DEFAULT_PROFILE, workflow_id) or profile_id not in ('', selected_id)
                or engine != 'trellis2' or seed not in (None, recipe['seed']) or not remove_bg
                or triangles not in (0, recipe['target_faces']) or texture_size not in (0, recipe['texture_size'])
                or resolution not in (None, recipe['resolution']) or texture_method not in (None, recipe['texture_method'])
                or advanced_settings not in ('', '{}') or reference_edits not in ('', '{}') or master_finishing
                or any((reproject_face, retexture_after_refinement, multiview_texture_refinement,
                        keep_finishing_source, repair_surface, hd_reconstruction))):
            raise HTTPException(400, 'Use the advanced settings for the selected generation profile.')
        try:
            if len(workflow_options) > 24000:
                raise ValueError('Workflow settings are too large.')
            saved = profile['settings']['workflow_options'] if custom_workflow else None
            options = new_workflow_options(workflow_id, json.loads(workflow_options) if workflow_options else {}, saved=saved)
        except (TypeError, ValueError, OverflowError) as error:
            raise HTTPException(400, str(error)) from error
        return await generate_workflow(image, workflow_id, options=options, name=name, height_m=height_m, asset_type=asset_type,
                                       reference_prompt=reference_prompt, parent_id=parent_id, queue_only=queue_only,
                                       queue_priority=queue_priority, queue_hold=queue_hold, profile=profile)
    if workflow_options:
        raise HTTPException(400, 'Choose a profile that uses workflow settings.')
    seed = 42 if seed is None else seed
    resolution = DEFAULT_GENERATION['resolution'] if resolution is None else resolution
    if engine not in ('trellis2', 'pixal3d_single'):
        raise HTTPException(400, 'Choose a supported single-image method.')
    pixal = engine == 'pixal3d_single'
    if pixal and preset == FIGURE_MASTER:
        raise HTTPException(400, 'Figure Master uses TRELLIS.2 Single image. Choose another profile for Pixal3D.')
    if pixal:
        if resolution != 1024 or (texture_size and texture_size not in PIXAL_TEXTURE_SIZES):
            raise HTTPException(400, 'Pixal3D Single image uses resolution 1024 and a 1K or 2K atlas.')
        remove_bg = True
    if reproject_face and not pixal:
        raise HTTPException(400, 'Face reprojection requires Pixal3D.')
    # Legacy multipart profile_id is provenance; an edited base preset wins.
    experimental = preset == HUMANOID_EXPERIMENTAL
    cache_required = preset in (HUMANOID_EXPERIMENTAL, DENSE_MASTER, FIGURE_MASTER)
    if repair_surface and preset != DENSE_MASTER:
        raise HTTPException(400, 'Choose Dense Master to repair the surface during generation.')
    if cache_required:
        keep_finishing_source = True
    if experimental:
        asset_type = 'humanoid'
    if (preset not in GENERATION_PRESETS or not 0.01 <= height_m <= 100 or not 0 <= seed <= 2147483647
        or (triangles!=0 and not TRIANGLE_MIN<=triangles<=TRIANGLE_MAX) or asset_type not in ASSET_TYPES
        or texture_size not in (0,1024,2048,4096) or resolution not in (512,1024,1536) or len(reference_prompt)>6000 or len(name)>80):
        raise HTTPException(400, 'Invalid settings.')
    steps, default_triangles, default_texture = GENERATION_PRESETS[preset]
    triangles = triangles or default_triangles
    texture = texture_size or default_texture
    try:
        texture_method = resolve_texture_method(texture_method, multiview_texture_refinement,
                                                'multiview' if preset == FIGURE_MASTER else 'trellis')
        validate_texture_method(texture_method, engine=engine, texture_size=texture)
        multiview_texture_refinement = texture_method == 'multiview'
        validate_hd_reconstruction(hd_reconstruction, preset=preset, resolution=resolution,
                                   repair_surface=repair_surface)
        validate_create_master_finishing(master_finishing, preset=preset, resolution=resolution,
                                         triangles=triangles, repair_surface=repair_surface,
                                         hd_reconstruction=hd_reconstruction, engine=engine)
        validate_post_refinement_texturing(retexture_after_refinement, preset=preset, resolution=resolution,
                                           master_finishing=master_finishing, engine=engine)
        if len(advanced_settings)>12000:raise ValueError('Advanced settings are too large.')
        incoming=json.loads(advanced_settings) if advanced_settings else {}
        if not isinstance(incoming,dict):raise ValueError('Malformed advanced settings.')
        if pixal:
            if incoming:
                raise ValueError('Pixal3D uses its own sampling settings.')
            advanced = dict(PIXAL_SINGLE_OPTIONS)
            if preset in (DENSE_MASTER, HUMANOID_EXPERIMENTAL, 'high_detail'):
                advanced.update(DENSE_MASTER_FINISHING)
        else:
            advanced=normalize_options({**PROFILE_ADVANCED.get(preset,{}),**incoming},steps)
    except (ValueError,TypeError) as error:
        raise HTTPException(400,str(error))
    base_profile = next((p for p in builtin_profiles() if p['id'] == preset), None) if cache_required else None
    if profile_id and profile is None:
        try: profile = next((p for p in PROFILES.list() if p['id'] == profile_id), None)
        except ValueError as error: raise HTTPException(422, str(error))
        if profile is None: raise HTTPException(400, 'Profile not found; reload profiles.')
    profile = profile or base_profile
    require_create_master_finishing(master_finishing)
    require_texture_method(texture_method)
    if retexture_after_refinement:
        texture_runtime = detail_texturing_capability()
        if not texture_runtime['available']:
            raise HTTPException(503, texture_runtime['reason'])
    if pixal:
        runtime = pixal3d_capabilities()
        single_image = runtime.get('single_image', {})
        if not single_image.get('ready'):
            raise HTTPException(503, single_image.get('message', 'Install Pixal3D Single image.'))
        if reproject_face and not runtime['face_projection']['ready']:
            raise HTTPException(503, runtime['face_projection']['message'])
    if reference_edits:
        sources = await reference_sources((image,), engine)
        edits, cutouts, frames = await prepare_reference_uploads(sources, reference_edits, engine, remove_bg)
    async with LOCK:
        if parent_id:job_path(parent_id)
        if not queue_only and (TERRAIN_ACTIVE or (PROCESS is not None and PROCESS.poll() is None)):
            raise HTTPException(409, 'A generation is already running; add the next image to the queue.')
        if type(queue_priority) is not int or not -2<=queue_priority<=2:raise HTTPException(400,'Invalid queue priority.')
        if not pixal and not status()['ready']: raise HTTPException(503, 'Model weights are not installed yet.')
        supported, textures = ([1024], PIXAL_TEXTURE_SIZES) if pixal else generation_capabilities()
        if resolution not in supported:raise HTTPException(400,'This 3D resolution is not available or validated in this installation.')
        if texture_size and texture_size not in textures:raise HTTPException(400,'This texture size is not validated in this installation.')
        source = sources['front'] if reference_edits else (await reference_sources((image,), engine))['front']
        identifier = uuid.uuid4().hex
        folder = OUTPUT / identifier
        safe_name = re.sub(r'[^A-Za-z0-9_-]+', '-', name.strip()).strip('-')[:50] or 'Object'
        settings = dict(name=safe_name, preset=preset, height_m=height_m, seed=seed, remove_bg=remove_bg,
                        texture_method=texture_method,
                        steps=steps, triangles=triangles, texture_size=texture, created=time.time(), asset_type=asset_type,
                        resolution=resolution,pipeline_type={512:'512',1024:'1024_cascade',1536:'1536_cascade'}[resolution],
                        generation_options=advanced,settings_version=2)
        if pixal:
            settings.update(engine='pixal3d', view_mode='single', pipeline_type='pixal3d_1024_monoview',
                            settings_version=3)
        if reproject_face:
            settings['reproject_face'] = True
        if keep_finishing_source: settings['keep_finishing_source'] = True
        if repair_surface: settings['repair_surface'] = True
        if hd_reconstruction: settings['hd_reconstruction'] = True
        if preset == DENSE_MASTER and not (hd_reconstruction or master_finishing): settings['adaptive_finishing'] = True
        if reference_edits:
            settings['reference_edits'] = edits
        if profile: settings['profile'] = dict(id=profile['id'], name=profile['name'])
        if base_profile:
            settings['profile_snapshot'] = dict(id=preset, name=base_profile['name'],
                engine='pixal3d' if pixal else 'trellis2', asset_type=asset_type, keep_finishing_source=True,
                preset=preset, resolution=resolution, triangles=triangles, texture_size=texture,
                generation_options=advanced)
            settings['profile_snapshot']['texture_method'] = texture_method
            if experimental:
                settings['profile_snapshot']['experimental'] = True
            if preset == DENSE_MASTER and not (hd_reconstruction or master_finishing):
                settings['profile_snapshot']['adaptive_finishing'] = True
            if repair_surface:
                settings['profile_snapshot']['repair_surface'] = True
            if hd_reconstruction:
                settings['profile_snapshot']['hd_reconstruction'] = True
        assign_create_master_finishing(settings, master_finishing)
        if retexture_after_refinement:
            settings['retexture_after_refinement'] = True
            if snapshot := settings.get('profile_snapshot'):
                snapshot['retexture_after_refinement'] = True
        if multiview_texture_refinement:
            settings['multiview_texture_refinement'] = True
            if snapshot := settings.get('profile_snapshot'):
                snapshot['multiview_texture_refinement'] = True
        if parent_id:settings['parent_id']=parent_id
        with TemporaryDirectory(prefix='.trellis2-', dir=OUTPUT) as staging:
            staged = Path(staging)
            source.save(staged / 'input.png')
            if reference_edits:
                if pixal:
                    frames['front'].save(staged / 'prepared.png')
                else:
                    cutouts['front'].save(staged / 'prepared.png')
                    frames['front'].save(staged / 'conditioning.png')
            write_json(staged / 'settings.json', settings)
            write_json(staged / 'metadata.json', dict(display_name=name.strip()[:80] or safe_name,
                       reference_prompt=reference_prompt.strip(), review_state='review', favorite=False, tags=[]))
            if queue_only:
                write_json(staged / 'status.json', dict(state='queued', stage='Waiting in queue', progress=0))
            staged.rename(folder)
        if queue_only:
            try:
                STUDIO.workspace.enqueue(identifier, queue_priority, queue_hold)
            except OSError as error:
                write_json(folder / 'status.json', dict(state='error', stage='Unable to save queue',
                           progress=0, error=str(error)))
                raise HTTPException(503, 'Unable to save the generation queue.') from error
            return {'id':identifier,'queued':True}
        launch_worker(folder, 'pixal3d_worker.py' if pixal else 'worker.py')
        return {'id': identifier}

@app.post('/api/jobs/{identifier}/cancel')
async def cancel(identifier: str):
    if ID.fullmatch(identifier) and (generated_animations_root()/identifier).is_dir():
        return await cancel_generated_animation(identifier)
    global LOG_HANDLE
    face = STUDIO.job_folder(identifier)
    if face.parent == STUDIO.workspace.root.resolve() / 'face-previews':
        return await discard_face_preview(read_workspace_json(face/'settings.json')['parent_id'], identifier)
    if WORKSPACE_CONFIG and ID.fullmatch(identifier) and (animation_operations_root()/identifier).is_dir():
        return await cancel_animation_operation(identifier)
    folder = job_path(identifier)
    record=job_record(folder)
    if record['settings'].get('source_kind') == 'face_refinement':
        from face_refine_api import clear_copy_inputs
        async with LOCK:
            state = read_workspace_json(folder/'status.json', {}).get('state')
            if state == 'queued':
                STUDIO.cancel_pending(identifier, folder)
            elif state == 'running':
                if ACTIVE == identifier and PROCESS is not None and PROCESS.poll() is None:
                    await asyncio.to_thread(stop_worker)
                    if LOG_HANDLE:
                        LOG_HANDLE.close()
                        LOG_HANDLE = None
                write_json(folder/'status.json', dict(state='cancelled', stage='Face copy cancelled', progress=0))
            if read_workspace_json(folder/'status.json', {}).get('state') in ('cancelled', 'error'):
                clear_copy_inputs(folder)
        return {'ok':True}
    if record['state']=='queued' and record.get('operation')!='animation' and STUDIO.queued(identifier):
        for entry in STUDIO.workspace.queue['items']:
            if entry['id']==identifier:entry['cancelled']=True
        STUDIO.workspace.save_queue();write_json(folder/'status.json',dict(state='cancelled',stage='Removed from generation queue',progress=0));return {'ok':True}
    if ACTIVE == identifier and PROCESS is not None and PROCESS.poll() is None:
        cancelled_at = time.monotonic()
        await asyncio.to_thread(stop_worker)
        if LOG_HANDLE:
            LOG_HANDLE.close()
            LOG_HANDLE = None
        if record.get('operation')=='animation':
            recover_animation(folder)
            (folder/'generated-animation-request.json').unlink(missing_ok=True)
            result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
            write_json(folder/'status.json',dict(state='done',stage='Model preserved',progress=100,result=result,operation_error='Animation update cancelled.'))
        else:
            if record['settings'].get('workflow_id') in WORKFLOW_PROFILES:
                for child in folder.iterdir():
                    if (re.fullmatch(r'\.workflow-result-[a-z0-9_]{8}', child.name)
                            and child.is_dir() and child.resolve() == child):
                        await asyncio.to_thread(shutil.rmtree, child, ignore_errors=True)
            previous = read_workspace_json(folder / 'status.json', {})
            write_json(folder / 'status.json', stopped_generation(previous, 'cancelled', 'Generation cancelled', at=cancelled_at))
    return {'ok':True}

@app.post('/api/terrain')
async def terrain(request: Request):
    global TERRAIN_ACTIVE
    from terrain_generator import generate_terrain
    try:
        config = await request.json()
        parent_id=config.get('parent_id','')
        if not isinstance(parent_id,str) or parent_id and not re.fullmatch('[a-f0-9]{32}',parent_id):raise ValueError('Invalid source model.')
        name = re.sub(r'[^A-Za-z0-9_-]+', '-', str(config.get('name','Terrain')).strip()).strip('-')[:50] or 'Terrain'
        settings = dict(name=name, asset_type='terrain_unity', seed=int(config.get('seed',42)),
                        size_m=float(config.get('size_m',256)), height_m=float(config.get('height_m',32)),
                        roughness=float(config.get('roughness',.55)), flat_zone=float(config.get('flat_zone',.15)),
                        terrain_style=str(config.get('terrain_style','hills')), resolution=int(config.get('resolution',257)),
                        terrain_palette=str(config.get('terrain_palette','blackwater')),
                        created=time.time(), texture_size=512)
        if (settings['size_m'] not in (64,128,256,512,1024) or not 1 <= settings['height_m'] <= 250
            or not 0 <= settings['seed'] <= 2147483647 or not .1 <= settings['roughness'] <= .9
            or not 0 <= settings['flat_zone'] <= .4 or settings['resolution'] not in (129,257,513)
            or settings['terrain_style'] not in ('hills','mountains','basin','plateau') or settings['terrain_palette'] not in ('blackwater','dry','ash')):
            raise ValueError('Invalid terrain settings.')
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, 'Invalid terrain settings.')
    async with LOCK:
        if parent_id:job_path(parent_id);settings['parent_id']=parent_id
        if TERRAIN_ACTIVE or (PROCESS is not None and PROCESS.poll() is None):
            raise HTTPException(409, 'Wait for the current generation to finish.')
        identifier = uuid.uuid4().hex
        folder = OUTPUT / identifier
        folder.mkdir()
        TERRAIN_ACTIVE = identifier
        (folder/'settings.json').write_text(json.dumps(settings, indent=2),encoding='utf-8')
        (folder/'status.json').write_text(json.dumps({'state':'running','stage':'Computing terrain and textures','progress':25}),encoding='utf-8')
        try:
            await asyncio.to_thread(generate_terrain, folder, settings)
        except Exception as error:
            (folder/'status.json').write_text(json.dumps({'state':'error','stage':'Terrain failed','progress':0,'error':str(error)}),encoding='utf-8')
            raise HTTPException(500, 'Terrain creation failed.')
        finally:
            TERRAIN_ACTIVE = None
        return {'id':identifier}

@app.get('/api/jobs/{identifier}/log')
def logs(identifier: str):
    folder = job_path(identifier)
    path = folder / 'generation.log'
    return {'text':path.read_text(encoding='utf-8', errors='replace')[-18000:] if path.exists() else ''}

@app.get('/api/jobs/{identifier}/collection-preview/{revision}/{mode}.webp', include_in_schema=False)
def collection_preview(identifier: str, revision: str, mode: str):
    folder = job_path(identifier)
    publication_pending = WORKSPACE_CONFIG.get('publication_pending')
    if publication_pending and publication_pending(identifier):
        raise HTTPException(404)
    image = preview_image(COLLECTION_CACHE, folder, revision, mode)
    if image is None:
        raise HTTPException(404)
    return Response(image, media_type='image/webp')

@app.get('/api/jobs/{identifier}/files/{filename}')
async def files(identifier: str, filename: str):
    folder = job_path(identifier)
    if filename=='Blender.zip':
        from studio_assets import prepare_blender
        from starlette.background import BackgroundTask
        async with LOCK:
            folder = job_path(identifier)
            if job_record(folder)['state']!='done': raise HTTPException(409,'The model must be complete.')
            EXPORTING.add(identifier)
            try: await asyncio.to_thread(prepare_blender,folder)
            except Exception:
                EXPORTING.discard(identifier)
                raise
        name=json.loads((folder/'settings.json').read_text(encoding='utf-8'))['name']
        return FileResponse(folder/'Blender.zip',filename='TRELLIS-'+name+'-Blender.zip',media_type='application/zip',
                            background=BackgroundTask(EXPORTING.discard,identifier))
    if filename not in (*REFERENCE_FILES,*PREPARED_VIEWS.values(),*COMFY_INPUT_FILES,'conditioning.png','model.glb','model.stl','print-report.json','Unity.zip','Blender.zip','settings.json','result.json','heightmap.png','heightmap.raw','original.glb','original.fbx','original.obj','original.stl','original.ply') or not (folder / filename).exists():
        raise HTTPException(404)
    settings = json.loads((folder/'settings.json').read_text(encoding='utf-8'))
    if filename in COMFY_INPUT_FILES and (settings.get('workflow_id') not in WORKFLOW_PROFILES
            or settings.get('workflow_input') != filename or (folder / filename).is_symlink()):
        raise HTTPException(404)
    if settings.get('source_kind') in PREPARED_OUTPUT_KINDS and filename in ('model.glb', 'result.json'):
        status = read_workspace_json(folder / 'status.json', {})
        if not prepared_output_ready(settings, status):
            raise HTTPException(409, 'The model is available after preparation completes.')
    download_name = 'TRELLIS-' + settings['name']
    if filename == 'model.glb': download_name += '.glb'
    elif filename == 'model.stl': download_name += '-mm.stl'
    elif filename == 'Unity.zip': download_name += '-Unity.zip'
    elif filename == 'Blender.zip': download_name += '-Blender.zip'
    elif filename == 'settings.json': download_name += '-reglages.json'
    else: download_name = filename
    return FileResponse(folder/filename, filename=download_name if filename in ('model.glb','model.stl','Unity.zip','Blender.zip','settings.json') else None,
                        media_type={'model.glb':'model/gltf-binary','Unity.zip':'application/zip','Blender.zip':'application/zip','settings.json':'application/json'}.get(filename))

@app.get('/')
def index():
    html = (ROOT / 'web' / 'studio.html').read_text(encoding='utf-8')
    return HTMLResponse(render_studio_version(html, ui_revision=UI_REVISION),
                        headers={'Cache-Control': 'no-cache, must-revalidate'})


@app.get('/api/release-status', include_in_schema=False)
def studio_release_status():
    return JSONResponse(release_status(), headers={'Cache-Control': 'no-store'})

@app.get('/api/rig-pilot')
def rig_pilot():
    path = RIG_PILOT / 'report.json'
    if not path.is_file(): return {'ready': False, 'test_only': True}
    try: data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError): raise HTTPException(422, 'Pilot report is unreadable.')
    return {**data, 'ready': (RIG_PILOT / 'tira_animated.glb').is_file(), 'test_only': True, 'description':'The original UniRig rigging pilot with a greeting authored in Blender.', 'limitations':'Technical example; review joint deformation on each character.'}

@app.get('/api/rig-pilot/files/{filename}')
def rig_pilot_file(filename: str):
    if filename not in ('tira_animated.glb', 'tira_animated.blend', 'preview.png', 'report.json'):
        raise HTTPException(404)
    path = RIG_PILOT / filename
    if not path.is_file() or path.resolve().parent != RIG_PILOT: raise HTTPException(404)
    return FileResponse(path, media_type={'tira_animated.glb': 'model/gltf-binary', 'preview.png': 'image/png', 'report.json': 'application/json'}.get(filename))

@app.post('/api/shutdown')
async def shutdown(idle_only:bool=False):
    global SHUTTING_DOWN
    async with LOCK:
        if idle_only and (TERRAIN_ACTIVE or PROCESS is not None and PROCESS.poll() is None or
                          EXPORT_PROCESS is not None and EXPORT_PROCESS.poll() is None):
            raise HTTPException(409,'Wait for computation or export to finish.')
        if SERVER is not None:
            SHUTTING_DOWN=True
            asyncio.get_running_loop().call_later(0.3, setattr, SERVER, 'should_exit', True)
    return {'ok': True}

from studio_api import install
STUDIO=install(app,globals())
from face_refine_api import install as install_face_refinement
discard_face_preview = install_face_refinement(app, globals())
COLLECTION_CACHE = STUDIO.workspace.root.resolve() / 'collection-previews'
COLLECTION_PREVIEWS = CollectionPreviews(
    lambda: [(None, OUTPUT, TRASH, COLLECTION_CACHE)],
    lambda: SHUTTING_DOWN or bool(TERRAIN_ACTIVE) or any(
        process is not None and process.poll() is None for process in (PROCESS, EXPORT_PROCESS)),
    lambda _owner, _identifier: False,
)
from generated_animation_api import install as install_generated_animations
install_generated_animations(app,globals())
from studio_rest import install as install_rest
install_rest(app,globals())

app.mount('/web', StaticFiles(directory=ROOT/'web'), name='web')
app.mount('/vendor', StaticFiles(directory=ROOT/'node_modules'/'three'), name='vendor')

if __name__ == '__main__':
    import uvicorn
    SERVER = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=PORT, log_level='info'))
    SERVER.run()
