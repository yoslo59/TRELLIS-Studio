"""Loopback-only local asset studio. No remote inference or hosted dependencies."""
from pathlib import Path
from studio_workspace import finishing_source
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
import asyncio, hashlib, io, json, os, re, shutil, subprocess, sys, time, uuid
from urllib.parse import urlparse
from typing import Annotated
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from generation_options import (normalize_options, options_for_job, schema as generation_schema,
                                DEFAULT_PROFILE, DEFAULT_GENERATION, GENERATION_PRESETS, GAME_D_ADVANCED, PROFILE_ADVANCED)
from asset_taxonomy import ASSET_TYPES, SYSTEM_TYPES, schema as taxonomy_schema
from generation_profiles import ProfileStore
from studio_import import FORMATS, MAX_BYTES, find_blender, validate_glb, copy_version
from animation_edits import recover as recover_animation
from glb_data import media as model_media
from library_actions import validate_metadata,validate_ids
from pixal3d_runtime import VIEW_FILES, runtime_status as pixal3d_runtime_status

ROOT = Path(__file__).resolve().parent
PIXAL_CHARACTER_PROFILE = dict(id='character_pixal', name='Character · Pixal3D',
                               resolution=1024, triangles=10600, texture_size=2048)
PIXAL_TEXTURE_SIZES = (1024, 2048)
PIXAL_CHARACTER_OPTIONS = dict(GAME_D_ADVANCED, structure_steps=12, shape_steps=20,
                               texture_steps=12, texture_guidance=1.0)
REFERENCE_FILES = tuple(VIEW_FILES.values())
# Studio 3.1 adds typed agent access and a self-hosted developer reference.
SOURCE_REVISION=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
UI_REVISION='3.1.0-reference-card'
def gpu_description():
    try:
        value=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits'],text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).splitlines()[0]
        name,memory=value.rsplit(',',1)
        return f'{name.strip()} · {round(float(memory)/1024,1):g} GB'
    except (OSError,ValueError,subprocess.SubprocessError,IndexError):return 'Local NVIDIA compute'
GPU_DESCRIPTION=gpu_description()
PROFILES = ProfileStore(os.environ.get('TRELLIS_PROFILES_FILE', str(ROOT / 'config' / 'generation-profiles.json')))
OUTPUT = Path(os.environ.get('TRELLIS_OUTPUT_DIR', str(ROOT / 'outputs'))).resolve()
TRASH = Path(os.environ.get('TRELLIS_TRASH_DIR', str(ROOT / 'trash'))).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
PROCESS = None
ACTIVE = None
FINISH_SOURCE = None
TERRAIN_ACTIVE = None
LOG_HANDLE = None
LOCK = asyncio.Lock()
PORT = int(os.environ.get('TRELLIS_PORT', '7860'))
SERVER = None
EXPORTING = set()
EXPORT_PROCESS = None
EXPORT_TASK = None
EXPORT_LOG = None
EXPORT_ROOT = Path(os.environ.get('TRELLIS_EXPORT_DIR',str(ROOT/'exports'))).resolve()
RIG_PILOT = Path(os.environ.get('TRELLIS_RIG_PILOT_DIR', str(ROOT / 'UniRig' / 'tests' / 'tira' / 'delivery'))).resolve()

def stop_worker():
    if PROCESS is None or PROCESS.poll() is not None: return
    import psutil
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
    yield
    await STUDIO.close()
    if PROCESS is not None and PROCESS.poll() is None:
        await asyncio.to_thread(stop_worker)
    if LOG_HANDLE: LOG_HANDLE.close()
    if EXPORT_PROCESS is not None and EXPORT_PROCESS.poll() is None:
        await asyncio.to_thread(stop_export)
    if EXPORT_LOG:EXPORT_LOG.close()

app = FastAPI(title='TRELLIS Studio API', version='3.1.0', description='Local asset studio. Typed JSON contracts are under /api/v1. Computation is asynchronous; poll job/export status. UI compatibility routes remain available.', lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url='/api/openapi.json')

@app.middleware('http')
async def local_requests(request: Request, call_next):
    host = request.url.hostname
    if host not in ('127.0.0.1', 'localhost'):
        return JSONResponse({'detail': 'Local access only.'}, status_code=403)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': 'Origin rejected.'}, status_code=403)
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

def job_record(folder):
    try:
        settings = json.loads((folder / 'settings.json').read_text(encoding='utf-8'))
        status_path = folder / 'status.json'
        data = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {'state':'queued','stage':'Starting','progress':0}
        data.update(id=folder.name, settings=settings)
        data['media']=model_media(folder/'model.glb')
        data['can_refinish'] = data['state'] == 'done' and bool(finishing_source(folder, settings))
        if settings.get('asset_type') not in ('terrain_unity','imported','rigged','print'):
            try:data['generation_options']=options_for_job(settings)
            except (ValueError,TypeError):data['generation_options']=None
        metadata_path = folder / 'metadata.json'
        try: data['metadata'] = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
        except (ValueError,OSError): data['metadata'] = {}
        if not isinstance(data['metadata'],dict): data['metadata'] = {}
        data['lods']=json.loads((folder/'lod-manifest.json').read_text(encoding='utf-8')) if (folder/'lod-manifest.json').is_file() else None
        data['physics']=json.loads((folder/'physics.json').read_text(encoding='utf-8')) if (folder/'physics.json').is_file() else None
        data['presentation']=json.loads((folder/'Presentation/presentation.json').read_text(encoding='utf-8')) if (folder/'Presentation/presentation.json').is_file() else {}
        if data['state'] in ('running','queued') and not (data['state']=='queued' and 'STUDIO' in globals() and STUDIO.queued(folder.name)):
            if folder.name != TERRAIN_ACTIVE and (folder.name != ACTIVE or PROCESS is None or PROCESS.poll() is not None):
                if data.get('operation')=='animation':
                    recover_animation(folder)
                    settings=json.loads((folder/'settings.json').read_text(encoding='utf-8'));data['settings']=settings
                    result_path=folder/'result.json'
                    if result_path.is_file():data['result']=json.loads(result_path.read_text(encoding='utf-8'))
                    data.update(state='done',stage='Model preserved after interruption',progress=100,operation_error='Animation editing was interrupted; the previous version has been preserved.')
                    write_json(status_path,{k:v for k,v in data.items() if k not in ('id','settings','metadata')})
                else:data.update(state='error', stage='Generation interrupted', error='The process stopped. Check the log and try again.')
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

def pixal3d_capabilities():
    return {**pixal3d_runtime_status(ROOT), 'profile': PIXAL_CHARACTER_PROFILE,
            'texture_sizes': PIXAL_TEXTURE_SIZES}


@app.get('/api/status')
def status():
    import psutil
    ready = (ROOT / 'models' / 'trellis2' / 'pipeline.json').exists() and (ROOT / 'engine' / 'MODELS' / 'dinov3' / 'model.safetensors').exists()
    resolutions,textures=generation_capabilities()
    pixal3d=pixal3d_capabilities()
    return dict(ready=ready, gpu=GPU_DESCRIPTION, ram_free_gib=round(psutil.virtual_memory().available/1024**3,1),
                active=ACTIVE if PROCESS is not None and PROCESS.poll() is None else TERRAIN_ACTIVE,
                resolution=DEFAULT_GENERATION['resolution'], default_profile=DEFAULT_PROFILE, local=True, studio_version='3.1.0',source_revision=SOURCE_REVISION,ui_revision=UI_REVISION,multi_view=pixal3d['ready'],pixal3d=pixal3d,
                features=['library','metadata','trash','blender_export','uv_preview','resolution_choice','advanced_controls','generation_profiles','model_import','unirig','print_copy','asset_taxonomy','comparison','diagnostics','target_exports','batch_actions','generation_queue','collections','version_history','lods','game_preparation','motion_tools','presentation','collection_transfer','rest_api_v1','mcp','refinish','pixal3d_multiview','pixal3d_face_projection'], texture_sizes=textures,supported_resolutions=resolutions)

@app.get('/api/components')
def components():
    try: runtime=json.loads((ROOT/'logs'/'components.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError): runtime={}
    return dict(blender=bool(find_blender()), import_formats=list(FORMATS), max_import_bytes=MAX_BYTES,
                unirig=runtime.get('unirig',{'ready':False,'message':'Run Open TRELLIS.cmd to check UniRig.'}),
                rig_pilot=(RIG_PILOT/'tira_animated.glb').is_file())

def launch_worker(folder, script, source_id=None):
    global PROCESS, ACTIVE, LOG_HANDLE, FINISH_SOURCE
    if LOG_HANDLE: LOG_HANDLE.close()
    LOG_HANDLE=(folder/'generation.log').open('a' if script=='animation_worker.py' else 'w',encoding='utf-8')
    env=os.environ.copy();env['PYTHONUTF8']='1'
    PROCESS=subprocess.Popen([sys.executable,'-u',str(ROOT/script),str(folder)],cwd=ROOT,env=env,
                             stdout=LOG_HANDLE,stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    ACTIVE=folder.name
    FINISH_SOURCE = source_id

def require_idle():
    if TERRAIN_ACTIVE or (PROCESS is not None and PROCESS.poll() is None):
        raise HTTPException(409,'Wait for the current computation to finish.')

@app.post('/api/import',status_code=201)
async def import_model(model: UploadFile=File(...), name: str=Form('')):
    async with LOCK:
        require_idle()
        if find_blender() is None: raise HTTPException(503,'Blender is required to import a model.')
        suffix=Path(model.filename or '').suffix.lower()
        if suffix not in FORMATS: raise HTTPException(400,'Choose a model GLB, FBX, OBJ, STL ou PLY.')
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

@app.post('/api/jobs/{identifier}/rig',status_code=201)
async def rig_model(identifier: str,request: Request):
    try:
        raw=await request.body();options=json.loads(raw) if raw else {}
        if not isinstance(options,dict):raise ValueError()
        rotation=options.get('rotation',[0,0,0]);height=float(options.get('height_m',1.7));offset=float(options.get('offset_m',0))
        if len(rotation)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not -180<=v<=180 for v in rotation) or not .2<=height<=3 or not -1<=offset<=1:raise ValueError()
        options=dict(rotation=rotation,height_m=height,offset_m=offset)
    except (ValueError,TypeError,KeyError):raise HTTPException(400,'Invalid rig rotation, height or offset.')
    async with LOCK:
        require_idle();source=job_path(identifier);record=job_record(source)
        if record['state']!='done' or not (source/'model.glb').is_file(): raise HTTPException(409,'Choose a completed model.')
        if record['settings'].get('asset_type')=='terrain_unity': raise HTTPException(400,'Choose a character or mesh object.')
        if not components()['unirig'].get('ready') or not find_blender(): raise HTTPException(503,'UniRig or Blender is unavailable. Run Open TRELLIS.cmd and check its diagnostic log.')
        destination=OUTPUT/uuid.uuid4().hex;destination.mkdir()
        shutil.copy2(source/'model.glb',destination/'source.glb')
        if (source/'input.png').exists(): shutil.copy2(source/'input.png',destination/'input.png')
        write_json(destination/'settings.json',dict(name=record['settings']['name']+'-rig',asset_type='rigged',source_kind='unirig',animation_clips=[],rig_options=options,
                   created=time.time(),parent_id=identifier,height_m=record['settings'].get('height_m',0),texture_size=record['settings'].get('texture_size',0)))
        write_json(destination/'metadata.json',dict(display_name=(record['metadata'].get('display_name',record['settings']['name'])+' · rig')[:80],review_state='review',favorite=False,tags=['UniRig']))
        write_json(destination/'status.json',dict(state='queued',stage='Preparing rig',progress=2))
        launch_worker(destination,'rig_worker.py')
        return {'id':destination.name}

from retarget_catalogue import ANIMATIONS as ANIMATION_LIBRARY

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
    return dict(animations=ANIMATION_LIBRARY,min_duration=2,max_duration=5,step=.5,
                description='Authored Quaternius CC0 animations, retargeted to your character.')

@app.get('/api/motion-source/{pack}')
def motion_source(pack: str):
    recipe=next((clip for clip in ANIMATION_LIBRARY if clip['source_pack']==pack),None)
    if recipe is None:raise HTTPException(404)
    return FileResponse(ROOT/recipe['source_model'],media_type='model/gltf-binary')

@app.post('/api/jobs/{identifier}/animations',status_code=202)
async def add_animation(identifier: str,request: Request):
    try:
        data=await request.json();clip=data['clip'];duration=float(data['duration'])
        if clip not in {a['id'] for a in ANIMATION_LIBRARY} or not 2<=duration<=5 or duration*2!=int(duration*2):raise ValueError()
    except (ValueError,TypeError,KeyError,OverflowError):raise HTTPException(400,'Choose a motion and a duration from 2 to 5 seconds, in 0.5-second steps.')
    async with LOCK:
        require_idle();source=job_path(identifier);record=job_record(source)
        if record['state']!='done' or not (source/'model.glb').is_file():raise HTTPException(409,'Choose a completed model.')
        refresh_export()
        if identifier in EXPORTING:raise HTTPException(409,'Wait for this model’s export to finish.')
        glb=validate_glb(source/'model.glb')
        if not glb.get('skins'):raise HTTPException(409,'Add a rig to the model first.')
        if record.get('result',{}).get('rigged') and not record['result'].get('humanoid_mapping'):
            raise HTTPException(422,'This skeleton could not be identified as humanoid. Standard humanoid motions do not fit this character.')
        clips=[c for c in record['settings'].get('animation_clips',[]) if c['id']!=clip]+[dict(id=clip,duration=duration)]
        if not find_blender():raise HTTPException(503,'Blender is unavailable.')
        from studio_workspace import checkpoint
        await asyncio.to_thread(checkpoint,source,'Before adding animation','animation')
        write_json(source/'animation-request.json',dict(settings=record['settings'],result=record.get('result',{}),clips=clips))
        write_json(source/'status.json',dict(state='queued',stage='Adding animation',progress=5,operation='animation',result=record.get('result',{})))
        launch_worker(source,'animation_worker.py')
        return {'id':identifier}

@app.get('/api/generation-options')
def generation_options_schema():
    return generation_schema()

@app.get('/api/profiles')
def profiles_list():
    try: return {'default_profile': DEFAULT_PROFILE, 'profiles': PROFILES.list()}
    except ValueError as error: raise HTTPException(422, str(error))

async def save_profile(request, identifier=None):
    if identifier in GENERATION_PRESETS: raise HTTPException(403, 'This built-in profile is protected. Save a custom copy instead.')
    async with LOCK:
        try:
            raw = await request.body()
            if len(raw) > 16000: raise ValueError('Profile is too large.')
            return PROFILES.save(json.loads(raw), identifier)
        except KeyError: raise HTTPException(404, 'Profile not found.')
        except (ValueError, TypeError) as error: raise HTTPException(400, str(error))

@app.post('/api/profiles', status_code=201)
async def create_profile(request: Request):
    return await save_profile(request)

@app.put('/api/profiles/{identifier}')
async def update_profile(identifier: str, request: Request):
    return await save_profile(request, identifier)

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
        try: return await asyncio.to_thread(prepare_blender,folder)
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

@app.post('/api/exports',status_code=202)
async def create_export(request: Request):
    global EXPORT_PROCESS,EXPORT_TASK,EXPORT_LOG
    from target_export import TARGETS
    async with LOCK:
        try:
            data=await request.json();target=data.get('target');ids=validate_ids(data.get('ids'),500 if target=='library' else 50)
            if target not in TARGETS:raise ValueError('Unknown export target.')
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
        write_json(task/'request.json',dict(ids=ids,target=target,created=time.time(),include_revisions=bool(data.get('include_revisions')),workspace=STUDIO.workspace.data,profiles=PROFILES.list()))
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
    path=job_path(identifier)/'rig-preview.json'
    if not path.is_file():return dict(ready=False)
    return dict(ready=True,**json.loads(path.read_text(encoding='utf-8')))

@app.get('/api/jobs/{identifier}/rig-preview-model')
def rig_preview_model(identifier: str):
    folder=job_path(identifier)
    path=folder/'rig-work'/'input.glb'
    if not path.is_file():path=folder/'source.glb'
    if not path.is_file():raise HTTPException(404)
    return FileResponse(path,media_type='model/gltf-binary')

async def read_generation_image(upload, label='Image', restrict_formats=False):
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
            return source
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise HTTPException(400, f'{label} could not be read: {error}') from error


@app.post('/api/generate-multiview', status_code=202)
async def generate_multiview(
    image: Annotated[UploadFile, File()],
    left_image: Annotated[UploadFile | None, File()] = None,
    back_image: Annotated[UploadFile | None, File()] = None,
    right_image: Annotated[UploadFile | None, File()] = None,
    side_angle: Annotated[int, Form()] = 40,
    name: Annotated[str, Form()] = 'Character',
    height_m: Annotated[float, Form()] = 1.7,
    seed: Annotated[int, Form()] = 42,
    triangles: Annotated[int, Form()] = 0,
    texture_size: Annotated[int, Form()] = 0,
    parent_id: Annotated[str, Form()] = '',
    reference_prompt: Annotated[str, Form()] = '',
    keep_finishing_source: Annotated[bool, Form()] = False,
    reproject_face: Annotated[bool, Form()] = False,
    queue_priority: Annotated[int, Form()] = 0,
):
    if (side_angle not in (40, 90) or not 0.01 <= height_m <= 100
        or not 0 <= seed <= 2147483647 or not -2 <= queue_priority <= 2
        or (triangles and not 1000 <= triangles <= 500000)
        or (texture_size and texture_size not in PIXAL_TEXTURE_SIZES)
        or len(name) > 80 or len(reference_prompt) > 6000):
        raise HTTPException(400, 'Invalid character generation settings.')
    if not any((left_image, back_image, right_image)):
        raise HTTPException(400, 'Add at least one side or back image to the front view.')
    runtime = pixal3d_capabilities()
    if not runtime['ready']:
        raise HTTPException(503, runtime['message'])
    if reproject_face and not runtime['face_projection']['ready']:
        raise HTTPException(503, runtime['face_projection']['message'])
    view_uploads = (
        ('front', 0, image),
        ('left', side_angle, left_image),
        ('back', 180, back_image),
        ('right', -side_angle, right_image),
    )
    views, sources = [], []
    for role, azimuth, upload in view_uploads:
        if upload is None:
            continue
        source = await read_generation_image(upload, f'{role.title()} view', restrict_formats=True)
        if source.getchannel('A').getextrema()[1] == 0:
            raise HTTPException(400, f'{role.title()} view is fully transparent.')
        filename = VIEW_FILES[role]
        views.append(dict(role=role, azimuth=azimuth, file=filename))
        sources.append((filename, source))
    profile = PIXAL_CHARACTER_PROFILE
    safe_name = re.sub(r'[^A-Za-z0-9_-]+', '-', name.strip()).strip('-')[:50] or 'Character'
    settings = dict(name=safe_name, engine='pixal3d', preset=profile['id'],
                    profile=dict(id=profile['id'], name=profile['name']),
                    asset_type='humanoid', height_m=height_m, seed=seed,
                    triangles=triangles or profile['triangles'],
                    texture_size=texture_size or profile['texture_size'],
                    resolution=profile['resolution'], pipeline_type='pixal3d_1024_multiview',
                    generation_options=PIXAL_CHARACTER_OPTIONS, settings_version=2, created=time.time(),
                    views=views, side_angle=side_angle, keep_finishing_source=keep_finishing_source)
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
            for filename, source in sources:
                source.save(staged / filename)
            write_json(staged / 'settings.json', settings)
            write_json(staged / 'metadata.json', dict(
                display_name=name.strip() or safe_name, category='Characters',
                reference_prompt=reference_prompt.strip(), review_state='review', favorite=False, tags=[]))
            write_json(staged / 'status.json', dict(state='queued', stage='Waiting in queue', progress=0))
            staged.rename(folder)
        try:
            STUDIO.workspace.enqueue(identifier, queue_priority)
        except OSError as error:
            write_json(folder / 'status.json', dict(state='error', stage='Unable to save queue',
                                                    progress=0, error=str(error)))
            raise HTTPException(503, 'Unable to save the generation queue.') from error
    return {'id': identifier, 'queued': True}


@app.post('/api/generate')
async def generate(image: UploadFile = File(...), name: str = Form('Object'), preset: str = Form(DEFAULT_PROFILE),
                   height_m: float = Form(1), seed: int = Form(42), remove_bg: bool = Form(True),
                   triangles: int = Form(0), asset_type: str = Form('object'), texture_size: int = Form(0),
                   reference_prompt: str = Form(''), resolution: int = Form(DEFAULT_GENERATION['resolution']), advanced_settings: str = Form(''),
                   keep_finishing_source: Annotated[bool,Form()]=False,
                   profile_id: str = Form(''), parent_id: Annotated[str,Form()]='', queue_only: Annotated[bool,Form()]=False, queue_priority: Annotated[int,Form()]=0, queue_hold: Annotated[bool,Form()]=False):
    if (preset not in GENERATION_PRESETS or not 0.01 <= height_m <= 100 or not 0 <= seed <= 2147483647
        or (triangles!=0 and not 1000<=triangles<=500000) or asset_type not in ASSET_TYPES
        or texture_size not in (0,1024,2048,4096) or resolution not in (512,1024,1536) or len(reference_prompt)>6000 or len(name)>80):
        raise HTTPException(400, 'Invalid settings.')
    try:
        if len(advanced_settings)>12000:raise ValueError('Advanced settings are too large.')
        incoming=json.loads(advanced_settings) if advanced_settings else {}
        if not isinstance(incoming,dict):raise ValueError('Malformed advanced settings.')
        advanced=normalize_options({**PROFILE_ADVANCED.get(preset,{}),**incoming},GENERATION_PRESETS[preset][0])
    except (ValueError,TypeError) as error:
        raise HTTPException(400,str(error))
    profile = None
    if profile_id:
        try: profile = next((p for p in PROFILES.list() if p['id'] == profile_id), None)
        except ValueError as error: raise HTTPException(422, str(error))
        if profile is None: raise HTTPException(400, 'Profile not found; reload profiles.')
    async with LOCK:
        if parent_id:job_path(parent_id)
        if not queue_only and (TERRAIN_ACTIVE or (PROCESS is not None and PROCESS.poll() is None)):
            raise HTTPException(409, 'A generation is already running; add the next image to the queue.')
        if type(queue_priority) is not int or not -2<=queue_priority<=2:raise HTTPException(400,'Invalid queue priority.')
        if not status()['ready']: raise HTTPException(503, 'Model weights are not installed yet.')
        supported,textures=generation_capabilities()
        if resolution not in supported:raise HTTPException(400,'This 3D resolution is not available or validated in this installation.')
        if texture_size and texture_size not in textures:raise HTTPException(400,'This texture size is not validated in this installation.')
        source = await read_generation_image(image)
        identifier = uuid.uuid4().hex
        folder = OUTPUT / identifier
        folder.mkdir()
        source.save(folder / 'input.png')
        safe_name = re.sub(r'[^A-Za-z0-9_-]+', '-', name.strip()).strip('-')[:50] or 'Objet'
        steps, default_triangles, texture = GENERATION_PRESETS[preset]
        texture = texture_size or texture
        triangles = triangles or default_triangles
        settings = dict(name=safe_name, preset=preset, height_m=height_m, seed=seed, remove_bg=remove_bg,
                        steps=steps, triangles=triangles, texture_size=texture, created=time.time(), asset_type=asset_type,
                        resolution=resolution,pipeline_type={512:'512',1024:'1024_cascade',1536:'1536_cascade'}[resolution],
                        generation_options=advanced,settings_version=2)
        if keep_finishing_source: settings['keep_finishing_source'] = True
        if profile: settings['profile'] = dict(id=profile['id'], name=profile['name'])
        if parent_id:settings['parent_id']=parent_id
        (folder / 'settings.json').write_text(json.dumps(settings, indent=2), encoding='utf-8')
        write_json(folder/'metadata.json',dict(display_name=name.strip()[:80] or safe_name,
                   reference_prompt=reference_prompt.strip(),review_state='review',favorite=False,tags=[]))
        if queue_only:
            write_json(folder/'status.json',dict(state='queued',stage='Waiting in queue',progress=0))
            STUDIO.workspace.enqueue(identifier,queue_priority,queue_hold)
            return {'id':identifier,'queued':True}
        launch_worker(folder, 'worker.py')
        return {'id': identifier}

@app.post('/api/jobs/{identifier}/cancel')
async def cancel(identifier: str):
    global LOG_HANDLE
    folder = job_path(identifier)
    record=job_record(folder)
    if record['state']=='queued' and STUDIO.queued(identifier):
        for entry in STUDIO.workspace.queue['items']:
            if entry['id']==identifier:entry['cancelled']=True
        STUDIO.workspace.save_queue();write_json(folder/'status.json',dict(state='cancelled',stage='Removed from generation queue',progress=0));return {'ok':True}
    if ACTIVE == identifier and PROCESS is not None and PROCESS.poll() is None:
        await asyncio.to_thread(stop_worker)
        if LOG_HANDLE:
            LOG_HANDLE.close()
            LOG_HANDLE = None
        if record.get('operation')=='animation':
            recover_animation(folder)
            result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
            write_json(folder/'status.json',dict(state='done',stage='Model preserved',progress=100,result=result,operation_error='Animation update cancelled.'))
        else:(folder / 'status.json').write_text(json.dumps({'state':'cancelled','stage':'Generation cancelled','progress':0}), encoding='utf-8')
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
    if filename not in (*REFERENCE_FILES,'prepared.png','conditioning.png','model.glb','model.stl','print-report.json','Unity.zip','Blender.zip','settings.json','result.json','heightmap.png','heightmap.raw','original.glb','original.fbx','original.obj','original.stl','original.ply') or not (folder / filename).exists():
        raise HTTPException(404)
    download_name = 'TRELLIS-' + json.loads((folder/'settings.json').read_text(encoding='utf-8'))['name']
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
    return FileResponse(ROOT / 'web' / 'studio.html',
                        headers={'Cache-Control': 'no-cache, must-revalidate'})

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
async def shutdown():
    if SERVER is not None:
        asyncio.get_running_loop().call_later(0.3, setattr, SERVER, 'should_exit', True)
    return {'ok': True}

from studio_api import install
STUDIO=install(app,globals())
from studio_rest import install as install_rest
install_rest(app,globals())

app.mount('/web', StaticFiles(directory=ROOT/'web'), name='web')
app.mount('/vendor', StaticFiles(directory=ROOT/'node_modules'/'three'), name='vendor')

if __name__ == '__main__':
    import uvicorn
    SERVER = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=PORT, log_level='info'))
    SERVER.run()
