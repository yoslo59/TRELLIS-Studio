"""Documented JSON API over the same transactional services used by Studio's UI."""
from pathlib import Path
from contextlib import ExitStack
import inspect,io,json,time,uuid,hashlib,os
from fastapi import APIRouter,UploadFile,File,HTTPException,Query
from fastapi.responses import FileResponse,HTMLResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.requests import Request
from starlette.routing import Match
from api_contract import *
from studio_workspace import read,write
from studio_import import FORMATS,validate_glb
from PIL import Image

def install(app,g):
    root=g['ROOT'];uploads=g['STUDIO'].workspace.root/'uploads'
    server_mode = bool(g.get('WORKSPACE_CONFIG'))
    responses = {
        400: {'description': 'Invalid input or unsupported model.'},
        404: {'description': 'Item not found.'},
        409: {'description': 'Operation conflicts with current state.'},
    }
    if server_mode:
        responses.update({
            401: {'description': 'Sign in or send a valid personal API key.'},
            402: {'description': 'No generation credits available.'},
            403: {'description': 'Account security setup or origin check failed.'},
            413: {'description': 'Request exceeds the file size or available disk quota.'},
            507: {'description': 'The server has insufficient disk space.'},
        })
    router = APIRouter(prefix='/api/v1', responses=responses)

    async def legacy(method,path,body=None,**extra):
        payload=json.dumps(body if body is not None else {},allow_nan=False).encode()
        async def receive():return {'type':'http.request','body':payload,'more_body':False}
        scope={'type':'http','method':method,'path':path,'root_path':'','query_string':b'','headers':[(b'content-type',b'application/json')],'app':app}
        request=Request(scope,receive)
        for route in app.routes:
            match,child=route.matches(scope)
            if match is Match.FULL:
                kwargs={**child.get('path_params',{}),**extra}
                if 'request' in inspect.signature(route.endpoint).parameters:kwargs['request']=request
                result=route.endpoint(**kwargs)
                return await result if inspect.isawaitable(result) else result
        raise HTTPException(404,'Operation unavailable.')

    def accepted(result):
        identifier=result['id'];record=g['job_record'](g['job_path'](identifier))
        return dict(id=identifier,state=record['state'],status_url='/api/v1/jobs/'+identifier)

    def upload(identifier,kind=None):
        folder=uploads/identifier;entry=read(folder/'upload.json')
        if not entry or kind and entry['kind']!=kind:raise HTTPException(400,'Upload a compatible source first.')
        return folder/entry['file'],entry

    def reference_files(data, stack):
        references = {
            'image': data.upload_id, 'left_image': data.left_upload_id,
            'back_image': data.back_upload_id, 'right_image': data.right_upload_id,
        }
        files = {}
        for field, identifier in references.items():
            if identifier:
                path, entry = upload(identifier, 'image')
                stream = stack.enter_context(path.open('rb'))
                files[field] = UploadFile(file=stream, filename=entry['name'])
        return files

    @router.get('/status',tags=['Studio'],summary='Studio readiness and active computation')
    def status():
        result = {**g['status'](), 'api_version':'1', 'documentation':'/developers'}
        if not g.get('WORKSPACE_CONFIG'):
            result['mcp_url'] = f"http://127.0.0.1:{int(os.environ.get('TRELLIS_MCP_PORT','7861'))}/mcp"
        return result

    @router.get('/profiles',tags=['Studio'],summary='Built-in and custom generation profiles')
    def profiles():return g['profiles_list']()

    @router.get('/taxonomy',tags=['Studio'],summary='Supported categories and asset types')
    def taxonomy():return g['asset_taxonomy']()

    @router.get('/animations',tags=['Animation'],summary='Available authored humanoid motions')
    def animations():return g['animation_library']()

    @router.get('/client-config',tags=['Studio'],summary='STDIO configuration for this installation',include_in_schema=not server_mode)
    def client_config():
        python=root/'.mcp-venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        return {'mcpServers':{'trellis-studio':{'command':str(python),'args':[str(root/'trellis_mcp.py')],
            'cwd':str(root),'env':{'TRELLIS_API_URL':f"http://127.0.0.1:{g['PORT']}"}}}}

    @router.post('/uploads',tags=['Sources'],status_code=201,summary='Upload an image or self-contained GLB' if server_mode else 'Upload an image, 3D model or collection archive')
    async def source_upload(file:UploadFile=File(...)):
        suffix=Path(file.filename or '').suffix.lower();kind='image' if suffix in ('.png','.jpg','.jpeg','.webp') else 'model' if suffix in FORMATS else 'archive' if suffix=='.zip' else None
        if g.get('WORKSPACE_CONFIG') and kind in ('model','archive') and suffix != '.glb':
            raise HTTPException(400,'Server imports require a self-contained GLB.')
        if not kind:raise HTTPException(400,'Use PNG, JPG, WebP, GLB, FBX, OBJ, STL, PLY or collection ZIP.')
        # Large collection archives use the existing streaming transfer endpoint.
        if kind=='archive':
            result=await legacy('POST','/api/transfers/inspect',file=file)
            return dict(kind='archive',**result)
        limit=(25 if kind=='image' else 500)*1024**2;identifier=uuid.uuid4().hex;folder=uploads/identifier;folder.mkdir(parents=True);target=folder/('source'+suffix);size=0;digest=hashlib.sha256()
        try:
            with target.open('wb') as stream:
                while block:=await file.read(1024**2):
                    size+=len(block)
                    if size>limit:raise HTTPException(413,'Source exceeds its upload size limit.')
                    digest.update(block);stream.write(block)
            if not size:raise HTTPException(400,'The source file is empty.')
            if kind=='image':
                with Image.open(target) as image:
                    if image.width*image.height>40_000_000:raise ValueError('Image exceeds 40 megapixels.')
                    image.verify()
            elif suffix=='.glb':validate_glb(target)
            entry=dict(id=identifier,kind=kind,file=target.name,name=Path(file.filename).name,bytes=size,sha256=digest.hexdigest(),created=time.time());write(folder/'upload.json',entry)
            return {k:v for k,v in entry.items() if k!='file'}
        except (OSError,ValueError) as error:raise HTTPException(400,str(error))
        finally:await file.close()

    @router.post('/references/preview', tags=['Sources'], response_model=ReferencePreviewResult,
                 summary='Preview framing and background removal from uploaded images',
                 description='Returns original, cutout and framed PNG previews, each at most 240 × 240 pixels.')
    async def preview_reference(data: ReferencePreview):
        recipe = data.model_dump(include={'reference_edits'}, exclude_none=True)
        with ExitStack() as stack:
            return await g['prepare_reference_preview'](
                **reference_files(data, stack), engine=data.engine, remove_bg=data.remove_bg,
                reference_edits=json.dumps(recipe.get('reference_edits', {})), preview_size=240)

    @router.post('/generations',tags=['Generation'],status_code=202,response_model=JobAccepted,summary='Queue a generation from an uploaded image')
    async def generate(data:Generation):
        if set(data.overrides)-{'triangles','texture_size','resolution','remove_bg','advanced_settings','asset_type','reference_prompt','parent_id','keep_finishing_source','repair_surface'}:raise HTTPException(400,'Unsupported recipe override.')
        source,entry=upload(data.upload_id,'image');spec=dict(name=data.name,profile_id=data.profile_id,height_m=data.height_m,seed=data.seed,priority=data.priority,**data.overrides)
        spec.update(data.model_dump(include={'reference_edits'}, exclude_none=True))
        with source.open('rb') as stream:
            result=await legacy('POST','/api/queue/batch',images=[UploadFile(file=stream,filename=entry['name'])],specifications=json.dumps([spec]))
        return accepted(dict(id=result['ids'][0]))

    @router.post('/generations/multiview', tags=['Generation'], status_code=202,
                 response_model=JobAccepted, summary='Queue a Pixal3D character using a supported profile')
    async def generate_multiview(data: MultiViewGeneration):
        recipe = data.model_dump(exclude={'upload_id', 'left_upload_id', 'back_upload_id',
                                          'right_upload_id', 'priority'}, exclude_none=True)
        recipe['queue_priority'] = data.priority
        reference_edits = recipe.pop('reference_edits', None)
        if reference_edits:
            recipe['reference_edits'] = json.dumps(reference_edits)
        with ExitStack() as stack:
            return accepted(await legacy('POST', '/api/generate-multiview',
                                         **recipe, **reference_files(data, stack)))

    @router.post('/imports',tags=['Sources'],status_code=202,response_model=JobAccepted,summary='Import an uploaded model into the library')
    async def import_model(data:ImportModel):
        source,entry=upload(data.upload_id,'model')
        with source.open('rb') as stream:result=await legacy('POST','/api/import',model=UploadFile(file=stream,filename=entry['name']),name=data.name)
        return accepted(result)

    @router.get('/models',tags=['Models'],response_model=ModelPage,summary='Search and paginate the local library')
    def models(query:str='',state:str|None=None,folder_id:str|None=None,animated:bool|None=None,offset:int=Query(0,ge=0),limit:int=Query(24,ge=1,le=100)):
        records=g['jobs']();q=query.casefold()
        if folder_id is not None:
            allowed={folder_id}
            for _ in range(500):
                added={f['id'] for f in g['STUDIO'].workspace.data['folders'] if f['parent_id'] in allowed}-allowed
                if not added:break
                allowed.update(added)
            records=[r for r in records if r['metadata'].get('folder_id','') in allowed]
        records=[r for r in records if (not q or q in (r['metadata'].get('display_name',r['settings']['name'])+' '+' '.join(r['metadata'].get('tags',[]))).casefold()) and (state is None or r['state']==state) and (animated is None or bool((r.get('media') or {}).get('animated'))==animated)]
        summaries=[]
        for r in records[offset:offset+limit]:
            summaries.append(dict(id=r['id'],name=r['metadata'].get('display_name',r['settings']['name']),state=r['state'],progress=r.get('progress',0),metadata=r['metadata'],media=r.get('media',{}),parent_id=r['settings'].get('parent_id'),status_url='/api/v1/jobs/'+r['id'],model_url='/api/v1/models/'+r['id']+'/files/model.glb'))
        return dict(total=len(records),offset=offset,limit=limit,models=summaries)

    @router.get('/jobs/{identifier}',tags=['Models'],summary='Read job progress, recipe, rig, clips and prepared resources')
    def job(identifier:Identifier):return g['job'](identifier)

    @router.post('/jobs/{identifier}/cancel',tags=['Generation'],summary='Cancel one computation or queued generation')
    async def cancel(identifier:Identifier):return await g['cancel'](identifier)

    @router.patch('/models/{identifier}/metadata',tags=['Models'],summary='Update classification, name, review, tags or notes')
    async def metadata(identifier:Identifier,data:Metadata):return await legacy('PATCH',f'/api/jobs/{identifier}/metadata',data.model_dump(exclude_none=True))

    @router.delete('/models/{identifier}',tags=['Models'],summary='Move a model to restorable trash')
    async def trash(identifier:Identifier):return await g['trash_job'](identifier)

    @router.get('/trash',tags=['Models'],summary='List restorable models')
    def trash_list():return g['trash_list']()

    @router.post('/trash/{identifier}/restore',tags=['Models'],summary='Restore a model from trash')
    async def restore(identifier:Identifier):return await g['restore_job'](identifier)

    @router.get('/models/{identifier}/diagnostics',tags=['Preparation'],summary='Measure topology, UVs, dimensions, textures and animation')
    async def diagnostics(identifier:Identifier,force:bool=False):return await g['diagnostics'](identifier,force)

    @router.get('/models/{identifier}/diagnostics/report', tags=['Preparation'],
                summary='Download the saved diagnostics report')
    def diagnostics_report(identifier: Identifier):
        return g['diagnostics_report'](identifier)

    @router.post('/models/{identifier}/refinish', tags=['Preparation'], status_code=202,
                 response_model=JobAccepted, summary='Queue a linked finishing or hole-repair variant from cached geometry')
    async def refinish(identifier: Identifier, data: Refinish):
        return accepted(await legacy('POST', f'/api/jobs/{identifier}/refinish',
                                     data.model_dump(exclude_none=True)))

    @router.get('/models/{identifier}/finishing-board', tags=['Preparation'],
                response_model=FinishingOptions, summary='Read the cached source and supported finishing budgets')
    async def finishing_board(identifier: Identifier):
        return await legacy('GET', f'/api/jobs/{identifier}/finishing-board')

    @router.post('/models/{identifier}/finishing-board', tags=['Preparation'], status_code=202,
                 response_model=FinishingBatchAccepted, summary='Queue up to three distinct finishing recipes')
    async def finishing_batch(identifier: Identifier, data: FinishingBatch):
        return await legacy('POST', f'/api/jobs/{identifier}/finishing-board',
                            data.model_dump(exclude_none=True))

    @router.patch('/models/{identifier}/finishing-board', tags=['Preparation'],
                  response_model=FinishingSelection, summary='Select a completed variant or clear the selection')
    async def finishing_selection(identifier: Identifier, data: FinishingSelection):
        return await legacy('PATCH', f'/api/jobs/{identifier}/finishing-board', data.model_dump())

    @router.get('/models/{identifier}/files/{filename}',tags=['Models'],summary='Download a model, source image or recipe')
    async def files(identifier: Identifier, filename: Literal[
        'model.glb', 'model.stl', 'settings.json', 'result.json', 'print-report.json',
        'input.png', 'input-left.png', 'input-back.png', 'input-right.png',
        'prepared.png', 'prepared-left.png', 'prepared-back.png', 'prepared-right.png', 'conditioning.png',
    ]):
        return await g['files'](identifier, filename)

    @router.get('/models/{identifier}/presentation/{filename}', tags=['Models'],
                summary='Download a saved thumbnail, capture or turntable')
    async def presentation_file(
        identifier: Identifier,
        filename: Literal['thumbnail.png', 'capture.png', 'turntable.webm', 'library-turntable.webm'],
        v: str | None = None,
    ):
        return await legacy('GET', f'/api/jobs/{identifier}/presentation/{filename}', v=v)

    @router.post('/models/{identifier}/versions',tags=['History'],status_code=202,response_model=JobAccepted,summary='Duplicate an imported, rigged or printing model as a linked version')
    async def version(identifier:Identifier):return accepted(await g['import_version'](identifier))

    @router.post('/models/{identifier}/lods',tags=['Preparation'],status_code=202,response_model=JobAccepted,summary='Create a linked copy with automatic LODs')
    async def lod(identifier:Identifier,data:LOD):return accepted(await legacy('POST',f'/api/jobs/{identifier}/prepare/lod',data.model_dump()))

    @router.post('/models/{identifier}/game-copy',tags=['Preparation'],status_code=202,response_model=JobAccepted,summary='Prepare pivots, scale and collisions on a linked copy')
    async def game(identifier:Identifier,data:Game):return accepted(await legacy('POST',f'/api/jobs/{identifier}/prepare/game',data.model_dump()))

    @router.post('/models/{identifier}/print-copy',tags=['Preparation'],status_code=202,response_model=JobAccepted,summary='Create a dedicated printing mesh')
    async def printing(identifier:Identifier,data:PrintCopy):return accepted(await legacy('POST',f'/api/jobs/{identifier}/print-copy',data.model_dump()))

    @router.post('/models/{identifier}/rig',tags=['Animation'],status_code=202,response_model=JobAccepted,summary='Generate a UniRig skeleton on a linked copy')
    async def rig(identifier:Identifier,data:Rig):return accepted(await legacy('POST',f'/api/jobs/{identifier}/rig',data.model_dump(exclude_none=True)))

    @router.post('/models/{identifier}/animations',tags=['Animation'],status_code=202,response_model=JobAccepted,summary='Add or update one library motion on the same model',include_in_schema=not server_mode)
    async def animation(identifier:Identifier,data:Animation):return accepted(await legacy('POST',f'/api/jobs/{identifier}/animations',data.model_dump()))

    @router.post('/models/{identifier}/motion-edits',tags=['Animation'],status_code=202,response_model=JobAccepted,summary='Edit a clip loop, duration or root motion with automatic checkpoint',include_in_schema=not server_mode)
    async def motion(identifier:Identifier,data:Motion):return accepted(await legacy('POST',f'/api/jobs/{identifier}/prepare/motion',data.model_dump()))

    @router.get('/models/{identifier}/history',tags=['History'])
    async def history(identifier:Identifier):return await legacy('GET',f'/api/jobs/{identifier}/history')

    @router.post('/models/{identifier}/checkpoints',tags=['History'],status_code=201)
    async def checkpoint(identifier:Identifier,data:Checkpoint):return await legacy('POST',f'/api/jobs/{identifier}/checkpoints',data.model_dump())

    @router.post('/models/{identifier}/restore-version',tags=['History'],status_code=201,response_model=JobAccepted)
    async def restore_version(identifier:Identifier,data:RestoreVersion):return accepted(await legacy('POST',f'/api/jobs/{identifier}/restore-version',data.model_dump()))

    @router.get('/queue',tags=['Generation'])
    async def queue():return await legacy('GET','/api/queue')

    @router.patch('/queue',tags=['Generation'],summary='Pause or resume scheduling; active work is not interrupted')
    async def queue_control(data:QueueControl):return await legacy('PATCH','/api/queue',data.model_dump())

    @router.patch('/queue/{identifier}',tags=['Generation'],summary='Change queue priority' if server_mode else 'Change priority or retry a failed/cancelled task in place')
    async def queue_edit(identifier:Identifier,data:QueueEdit):return await legacy('PATCH',f'/api/queue/{identifier}',data.model_dump(exclude_none=True))

    @router.post('/queue/{identifier}/retry', tags=['Generation'], status_code=202,
                 response_model=JobRetryAccepted, summary='Queue a corrected attempt of a failed or cancelled job')
    async def retry_job(identifier: Identifier, data: JobRetry):
        result = await legacy('POST', f'/api/queue/{identifier}/retry', data.model_dump(exclude_none=True))
        return {**accepted(result), 'retry_of': result['retry_of'], 'paused': result['paused']}

    @router.get('/workspace',tags=['Collections'])
    async def workspace():return await legacy('GET','/api/workspace')

    @router.post('/folders',tags=['Collections'],status_code=201)
    async def folder(data:Folder):return await legacy('POST','/api/workspace/folders',data.model_dump())

    @router.post('/folders/assign',tags=['Collections'])
    async def assign(data:Assign):return await legacy('POST','/api/workspace/assign',data.model_dump())

    @router.post('/views',tags=['Collections'],status_code=201)
    async def view(data:SavedView):return await legacy('POST','/api/workspace/views',data.model_dump())

    @router.post('/exports',tags=['Export & transfer'],status_code=202,summary='Prepare an asynchronous export or collection archive')
    async def export(data:Export):return await g['create_export'](Request({'type':'http','method':'POST','headers':[]},receive_json(data.model_dump())))

    @router.get('/exports/{identifier}',tags=['Export & transfer'])
    def export_status(identifier:Identifier):return g['export_status'](identifier)

    @router.get('/exports/{identifier}/download',tags=['Export & transfer'])
    def export_download(identifier:Identifier):return g['export_download'](identifier)

    @router.post('/exports/{identifier}/cancel',tags=['Export & transfer'])
    async def export_cancel(identifier:Identifier):return await g['cancel_export'](identifier)

    @router.post('/transfers/{identifier}/restore',tags=['Export & transfer'],status_code=201,include_in_schema=not server_mode)
    async def transfer_restore(identifier:Identifier,data:RestoreCollection):return await legacy('POST',f'/api/transfers/{identifier}/restore',data.model_dump())

    # The UI routes stay functional; the public contract documents v1 only.
    for route in app.routes:
        if getattr(route,'path','')=='/' or getattr(route,'path','').startswith('/api/'):route.include_in_schema=False
    app.include_router(router)
    if server_mode:
        workspace_openapi = app.openapi

        def server_openapi():
            schema = workspace_openapi()
            schema['info']['title'] = 'TRELLIS Server API'
            schema['info']['description'] = ('Versioned API for the personal library. Calls use the rights, disk quota and generation credits of the authenticated account. '
                                              'Create a personal API key in My account → Security → API keys. The browser reference can use the signed-in session.')
            schema.setdefault('components', {}).setdefault('securitySchemes', {})['TrellisApiKey'] = {
                'type': 'http', 'scheme': 'bearer', 'description': 'Personal TRELLIS Server API key.',
            }
            schema['security'] = [{'TrellisApiKey': []}]
            return schema

        app.openapi = server_openapi
    app.mount('/reference-assets',StaticFiles(directory=root/'node_modules/swagger-ui-dist'),name='api-reference-assets')
    @app.get('/api/docs',include_in_schema=False)
    def reference():
        response=get_swagger_ui_html(openapi_url='/api/openapi.json',title='TRELLIS Studio · API reference',swagger_js_url='/reference-assets/swagger-ui-bundle.js',swagger_css_url='/reference-assets/swagger-ui.css',swagger_favicon_url='/web/trellis-mark.svg',swagger_ui_parameters={'docExpansion':'none','defaultModelsExpandDepth':-1,'filter':True,'displayRequestDuration':True,'tryItOutEnabled':False})
        return HTMLResponse(response.body.decode().replace('</head>','<link rel="stylesheet" href="/web/api-reference.css"></head>'))
    @app.get('/developers',include_in_schema=False)
    def developers():return FileResponse(root/'web/developers.html',media_type='text/html')
    @app.get('/docs',include_in_schema=False)
    def old_docs():return RedirectResponse('/api/docs')
    @app.get('/openapi.json',include_in_schema=False)
    def old_schema():return RedirectResponse('/api/openapi.json')

def receive_json(data):
    async def receive():return {'type':'http.request','body':json.dumps(data).encode(),'more_body':False}
    return receive
