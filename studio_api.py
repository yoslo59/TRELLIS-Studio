"""Studio 3 workspace services. All state is local and scoped to the library."""
from pathlib import Path
import asyncio,copy,json,math,os,re,shutil,time,uuid
from fastapi import HTTPException,Request,UploadFile,File,Form
from fastapi.responses import FileResponse
from PIL import Image
from studio_workspace import Workspace,read,write,name,checkpoint,revisions,ID,SNAPSHOT_FILES,SNAPSHOT_DIRS,finishing_source
from library_actions import validate_ids
from glb_data import read as read_glb,animation_info
from generation_options import options_for_job

def install(app,g):
    return StudioServices(app,g)

class StudioServices:
    def __init__(self,app,g):
        self.g=g;self.output=g['OUTPUT'];self.root=g['ROOT'];self.task=None
        default=self.root/'config'/'studio-state' if self.output==self.root/'outputs' else self.output.parent/(self.output.name+'-state')
        self.workspace=Workspace(os.environ.get('TRELLIS_STATE_DIR',str(default)))
        self.register(app)
    def queued(self,identifier):return self.workspace.pending(identifier)
    def cancel_pending(self,identifier,folder):
        state=read(folder/'status.json',{})
        if state.get('state')!='queued' or not self.queued(identifier):return
        for entry in self.workspace.queue['items']:
            if entry['id']==identifier:entry['cancelled']=True
        self.workspace.save_queue()
        write(folder/'status.json',dict(state='cancelled',stage='Removed from generation queue',progress=0))
    async def start(self):
        # A stopped calculation cannot resume inside a GPU phase. Retain every input
        # and require an explicit retry; pending jobs can continue after Resume.
        interrupted=False
        for entry in self.workspace.queue['items']:
            folder=self.output/entry['id'];state=read(folder/'status.json',{})
            if state.get('state')=='running':write(folder/'status.json',dict(state='error',stage='Interrupted · retry available',progress=0,error='The studio stopped during this calculation. Retry starts it from its saved recipe.'));interrupted=True
        if interrupted:self.workspace.queue['paused']=True;self.workspace.save_queue()
        self.task=asyncio.create_task(self.scheduler())
    async def close(self):
        if self.task:self.task.cancel()
    async def scheduler(self):
        while True:
            await asyncio.sleep(1)
            if self.workspace.queue.get('paused'):continue
            async with self.g['LOCK']:
                process=self.g['PROCESS']
                if self.g['TERRAIN_ACTIVE'] or process is not None and process.poll() is None:continue
                for item in self.workspace.ordered():
                    folder=self.output/item['id'];state=read(folder/'status.json',{})
                    if item.get('cancelled') or item.get('held') or state.get('state')!='queued':continue
                    write(folder/'status.json',dict(state='running',stage='Starting queued generation',progress=1))
                    try:self.g['launch_worker'](folder,'worker.py')
                    except OSError as error:write(folder/'status.json',dict(state='error',stage='Unable to launch generation',progress=0,error=str(error)))
                    break
    def available(self,identifier):
        folder=self.g['job_path'](identifier);record=self.g['job_record'](folder);self.g['refresh_export']()
        if record['state']!='done' or self.g['active_job'](identifier) or identifier in self.g['EXPORTING']:raise HTTPException(409,'Wait for this model to finish computing or exporting.')
        return folder,record
    def register(self,app):
        @app.get('/api/workspace')
        def workspace():return self.workspace.data
        @app.post('/api/workspace/folders',status_code=201)
        @app.put('/api/workspace/folders/{identifier}')
        async def folder_save(request:Request,identifier:str=None):
            async with self.g['LOCK']:
                try:return self.workspace.save_folder(await request.json(),identifier)
                except (ValueError,TypeError,KeyError) as e:raise HTTPException(400,str(e))
        @app.delete('/api/workspace/folders/{identifier}')
        async def folder_delete(identifier:str):
            async with self.g['LOCK']:
                try:self.workspace.folder(identifier)
                except ValueError as e:raise HTTPException(404,str(e))
                if any(f['parent_id']==identifier for f in self.workspace.data['folders']) or any(j['metadata'].get('folder_id')==identifier for j in self.g['jobs']()+self.g['trash_list']()):raise HTTPException(409,'Move models and child folders before removing this folder.')
                self.workspace.data['folders']=[f for f in self.workspace.data['folders'] if f['id']!=identifier];self.workspace.data['views']=[v for v in self.workspace.data['views'] if v['folder_id']!=identifier];self.workspace.save();return dict(ok=True)
        @app.post('/api/workspace/views',status_code=201)
        async def view_save(request:Request):
            async with self.g['LOCK']:
                try:return self.workspace.save_view(await request.json())
                except (ValueError,TypeError) as e:raise HTTPException(400,str(e))
        @app.delete('/api/workspace/views/{identifier}')
        async def view_delete(identifier:str):
            self.workspace.data['views']=[v for v in self.workspace.data['views'] if v['id']!=identifier];self.workspace.save();return dict(ok=True)
        @app.post('/api/workspace/assign')
        async def assign(request:Request):
            async with self.g['LOCK']:
                try:data=await request.json();ids=validate_ids(data['ids']);self.workspace.folder(data['folder_id'])
                except (ValueError,KeyError,TypeError) as e:raise HTTPException(400,str(e))
                plan=[]
                for identifier in ids:
                    folder=self.g['job_path'](identifier);path=folder/'metadata.json';plan.append((path,path.read_bytes() if path.exists() else None,{**read(path,{}),'folder_id':data['folder_id'],'updated':time.time()}))
                done=[]
                try:
                    for p,old,new in plan:write(p,new);done.append((p,old))
                except OSError:
                    for p,old in done:
                        if old is None:p.unlink(missing_ok=True)
                        else:p.write_bytes(old)
                    raise HTTPException(409,'The folder assignment failed; previous assignments were restored.')
                return dict(updated=len(ids))

        @app.get('/api/queue')
        def queue():
            items=[]
            for entry in self.workspace.ordered():
                folder=self.output/entry['id']
                if not folder.is_dir():continue
                record=self.g['job_record'](folder)
                if record:items.append({**entry,'name':record['metadata'].get('display_name',record['settings']['name']),'state':record['state'],'stage':record['stage'],'progress':record.get('progress',0),'settings':record['settings'],'error':record.get('error')})
            return dict(paused=self.workspace.queue['paused'],items=items)
        @app.patch('/api/queue')
        async def pause_queue(request:Request):
            data=await request.json()
            if type(data.get('paused')) is not bool:raise HTTPException(400,'Choose pause or resume.')
            self.workspace.queue['paused']=data['paused'];self.workspace.save_queue();return queue()
        @app.patch('/api/queue/{identifier}')
        async def edit_queue(identifier:str,request:Request):
            async with self.g['LOCK']:
                entry=next((i for i in self.workspace.queue['items'] if i['id']==identifier),None)
                if not entry:raise HTTPException(404)
                data=await request.json();folder=self.g['job_path'](identifier);state=read(folder/'status.json',{})
                if 'priority' in data:
                    if type(data['priority']) is not int or not -2<=data['priority']<=2:raise HTTPException(400,'Invalid priority.')
                    entry['priority']=data['priority']
                if data.get('retry'):
                    if state.get('state') not in ('error','cancelled'):raise HTTPException(409,'Only interrupted, failed or cancelled jobs can be retried.')
                    entry['cancelled']=False;entry['held']=False;write(folder/'status.json',dict(state='queued',stage='Waiting in queue',progress=0))
                self.workspace.save_queue();return queue()
        @app.post('/api/queue/batch',status_code=201)
        async def queue_batch(images:list[UploadFile]=File(...),specifications:str=Form(...)):
            try:
                specs=json.loads(specifications)
                if not isinstance(specs,list) or not 1<=len(specs)<=30 or len(specs)!=len(images):raise ValueError('Choose 1–30 images, with one recipe per image.')
                if any(not isinstance(s,dict) or set(s)-{'name','profile_id','height_m','seed','priority','remove_bg','triangles','texture_size','resolution','advanced_settings','asset_type','reference_prompt','parent_id','keep_finishing_source'} for s in specs):raise ValueError('Unknown queue settings.')
                for spec in specs:
                    for key,limit in [('name',80),('profile_id',80),('asset_type',80),('reference_prompt',6000),('parent_id',32)]:
                        if key in spec and (not isinstance(spec[key],str) or len(spec[key])>limit):raise ValueError('Invalid '+key+'.')
                    for key,low,high in [('seed',0,2147483647),('priority',-2,2),('triangles',0,500000),('texture_size',0,4096),('resolution',512,1536)]:
                        if key in spec and (type(spec[key]) is not int or not low<=spec[key]<=high):raise ValueError('Invalid '+key+'.')
                    if 'height_m' in spec and (type(spec['height_m']) not in (int,float) or not math.isfinite(spec['height_m']) or not .01<=spec['height_m']<=100):raise ValueError('Invalid height.')
                    if 'remove_bg' in spec and type(spec['remove_bg']) is not bool:raise ValueError('Invalid background removal setting.')
                    if 'keep_finishing_source' in spec and type(spec['keep_finishing_source']) is not bool:raise ValueError('Invalid finishing cache setting.')
                    if 'advanced_settings' in spec and not isinstance(spec['advanced_settings'],dict):raise ValueError('Invalid advanced settings.')
                    if spec.get('parent_id') and not ID.fullmatch(spec['parent_id']):raise ValueError('Invalid parent model.')
            except (ValueError,TypeError) as e:raise HTTPException(400,str(e))
            created=[]
            try:
                profiles={p['id']:p for p in self.g['PROFILES'].list()}
                if any(spec.get('profile_id','game_d') not in profiles for spec in specs):raise HTTPException(400,'Profile not found.')
                for file,spec in zip(images,specs):
                    profile=profiles.get(spec.get('profile_id','game_d'))
                    if not profile:raise HTTPException(400,'Profile not found.')
                    s=profile['settings'];params=dict(name=spec.get('name',Path(file.filename or 'Model').stem),preset=s['preset'],height_m=spec.get('height_m',1),seed=spec.get('seed',42),remove_bg=spec.get('remove_bg',s['remove_bg']),triangles=spec.get('triangles',s['triangles']),asset_type=spec.get('asset_type','object'),texture_size=spec.get('texture_size',s['texture_size']),reference_prompt=spec.get('reference_prompt',''),resolution=spec.get('resolution',s['resolution']),advanced_settings=json.dumps(spec.get('advanced_settings',s['advanced'])),profile_id=profile['id'],parent_id=spec.get('parent_id',''),queue_only=True,queue_priority=spec.get('priority',0),queue_hold=True,keep_finishing_source=spec.get('keep_finishing_source',False))
                    result=await self.g['generate'](image=file,**params);created.append(result['id'])
                async with self.g['LOCK']:
                    for entry in self.workspace.queue['items']:
                        if entry['id'] in created:entry['held']=False
                    self.workspace.save_queue()
            except Exception:
                for identifier in created:
                    entry=next(i for i in self.workspace.queue['items'] if i['id']==identifier);entry['cancelled']=True;write(self.output/identifier/'status.json',dict(state='cancelled',stage='Batch validation failed · nothing scheduled',progress=0))
                self.workspace.save_queue();raise
            finally:
                for file in images:await file.close()
            return dict(ids=created,count=len(created))

        @app.post('/api/jobs/{identifier}/refinish', status_code=202)
        async def refinish(identifier: str, request: Request):
            async with self.g['LOCK']:
                self.g['require_idle']()
                folder, record = self.available(identifier)
                source_id = finishing_source(folder, record['settings'])
                if not source_id:
                    raise HTTPException(409, 'This model has no reusable finishing source.')
                settings = dict(record['settings'])
                options = options_for_job(settings)
                try:
                    data = await request.json()
                    if not isinstance(data, dict) or set(data) - {'triangles', 'texture_size', 'mesh_mode', 'repair_holes'}:
                        raise ValueError('Only finishing settings can be changed.')
                    triangles = data.get('triangles', settings['triangles'])
                    texture_size = data.get('texture_size', settings['texture_size'])
                    mesh_mode = data.get('mesh_mode', options['mesh_mode'])
                    repair = data.get('repair_holes', options['repair_holes'])
                    if type(triangles) is not int or not 1000 <= triangles <= 500000:
                        raise ValueError('Choose a target of 1,000–500,000 triangles.')
                    if type(texture_size) is not int or texture_size not in self.g['generation_capabilities']()[1]:
                        raise ValueError('Choose a supported texture size.')
                    if mesh_mode not in ('direct', 'remesh') or type(repair) is not bool:
                        raise ValueError('Choose a valid mesh finishing method and hole setting.')
                except (ValueError, TypeError) as error:
                    raise HTTPException(400, str(error)) from error
                variant = self.output / uuid.uuid4().hex
                variant.mkdir()
                settings.pop('keep_finishing_source', None)
                settings.update(created=time.time(), parent_id=identifier, source_kind='refinish',
                                finish_source_id=source_id, triangles=triangles, texture_size=texture_size,
                                generation_options={**options, 'mesh_mode': mesh_mode, 'repair_holes': repair})
                write(variant / 'settings.json', settings)
                for filename in ('input.png', 'prepared.png', 'conditioning.png'):
                    if (folder / filename).is_file():
                        shutil.copy2(folder / filename, variant / filename)
                metadata = dict(record['metadata'])
                label = metadata.get('display_name') or settings['name']
                suffix = f' · {mesh_mode} {triangles / 1000:g}k · {texture_size // 1024}K'
                metadata.update(display_name=label[:80-len(suffix)] + suffix, review_state='review', favorite=False)
                write(variant / 'metadata.json', metadata)
                write(variant / 'status.json', dict(state='queued', stage='Preparing finishing variant', progress=1))
                try:
                    self.g['launch_worker'](variant, 'worker.py', source_id=source_id)
                except OSError as error:
                    write(variant / 'status.json', dict(state='error', stage='Unable to start finishing', progress=0, error=str(error)))
                    raise HTTPException(503, 'Unable to start finishing.') from error
                return dict(id=variant.name)

        @app.post('/api/jobs/{identifier}/prepare/{operation}',status_code=202)
        async def prepare(identifier:str,operation:str,request:Request):
            if operation not in ('lod','game','motion'):raise HTTPException(404)
            raw=await request.json()
            try:
                if operation=='lod':
                    ratios=raw.get('ratios',[.5,.25,.125])
                    if not isinstance(ratios,list) or not 1<=len(ratios)<=4 or any(type(r) not in (int,float) or not .02<=r<1 for r in ratios) or ratios!=sorted(set(ratios),reverse=True):raise ValueError('Choose 1–4 decreasing detail ratios, from 2% to 99%.')
                    options=dict(ratios=ratios)
                elif operation=='game':
                    options=dict(pivot=raw.get('pivot','ground'),collider=raw.get('collider','box'),rotation=raw.get('rotation',[0,0,0]),offset=raw.get('offset',[0,0,0]),scale=raw.get('scale',1))
                    if options['pivot'] not in ('source','ground','center') or options['collider'] not in ('none','box','convex') or type(options['scale']) not in (int,float) or not .001<=options['scale']<=100:raise ValueError('Invalid pivot, collision or scale.')
                    for field,limit in [('rotation',180),('offset',100)]:
                        if not isinstance(options[field],list) or len(options[field])!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>limit for v in options[field]):raise ValueError('Invalid '+field)
                else:
                    options=dict(clip=raw['clip'],start=float(raw.get('start',0)),end=float(raw.get('end',1)),duration=float(raw.get('duration',2.5)),loop=raw.get('loop',False),blend=float(raw.get('blend',.15)),mode=raw.get('mode','in_place'),distance=float(raw.get('distance',0)))
                    if type(options['loop']) is not bool or not 0<=options['start']<options['end']<=1 or not 2<=options['duration']<=5 or not 0<=options['blend']<=.4 or options['mode'] not in ('in_place','root_motion') or not -10<=options['distance']<=10:raise ValueError('Invalid motion settings.')
            except (ValueError,KeyError,TypeError) as e:raise HTTPException(400,str(e))
            async with self.g['LOCK']:
                self.g['require_idle']();folder,record=self.available(identifier)
                if not self.g['find_blender']():raise HTTPException(503,'Blender is required.')
                if operation=='motion':
                    data,binary=read_glb(folder/'model.glb')
                    if options['clip'] not in {c['name'] for c in animation_info(data,binary)}:raise HTTPException(400,'Choose an embedded animation.')
                    target=folder
                else:
                    target=self.output/uuid.uuid4().hex;target.mkdir();shutil.copy2(folder/'model.glb',target/'source.glb')
                    for filename in ('motion-report.json','physics.json','lod-manifest.json'):
                        if (folder/filename).is_file() and not (operation=='lod' and filename=='lod-manifest.json'):shutil.copy2(folder/filename,target/filename)
                    for dirname in ('Collisions',*(('LODs',) if operation=='game' else ())):
                        if (folder/dirname).is_dir():shutil.copytree(folder/dirname,target/dirname)
                    for filename in ('input.png',):
                        if (folder/filename).is_file():shutil.copy2(folder/filename,target/filename)
                    settings={**record['settings'],'created':time.time(),'parent_id':identifier,'source_kind':operation,'asset_type':'imported','preparation':options};write(target/'settings.json',settings)
                    metadata={**record['metadata'],'display_name':(record['metadata'].get('display_name',settings['name'])+(' · LODs' if operation=='lod' else ' · game'))[:80],'review_state':'review'};write(target/'metadata.json',metadata)
                write(target/'work-request.json',dict(operation=operation,options=options,settings=record['settings'],result=record.get('result',{})))
                write(target/'status.json',dict(state='queued',stage='Preparing model',progress=1,operation='animation' if operation=='motion' else operation,result=record.get('result',{})))
                self.g['launch_worker'](target,'asset_work.py');return dict(id=target.name)

        @app.get('/api/jobs/{identifier}/motion-report')
        def motion_report(identifier:str):return read(self.g['job_path'](identifier)/'motion-report.json',{})
        @app.get('/api/jobs/{identifier}/variants/{filename}')
        def variant_file(identifier:str,filename:str):
            if not re.fullmatch(r'LOD[0-4]\.glb',filename):raise HTTPException(404)
            path=self.g['job_path'](identifier)/'LODs'/filename
            if not path.is_file():raise HTTPException(404)
            return FileResponse(path,media_type='model/gltf-binary')
        @app.get('/api/jobs/{identifier}/history')
        def history(identifier:str):
            self.g['job_path'](identifier);jobs={j['id']:j for j in self.g['jobs']()};related={identifier};cursor=identifier
            for _ in range(500):
                parent=jobs.get(cursor,{}).get('settings',{}).get('parent_id')
                if parent not in jobs or parent in related:break
                related.add(parent);cursor=parent
            for _ in range(500):
                extra={j['id'] for j in jobs.values() if j['settings'].get('parent_id') in related}-related
                if not extra:break
                related.update(extra)
            nodes=[dict(id=j['id'],name=j['metadata'].get('display_name',j['settings']['name']),parent_id=j['settings'].get('parent_id',''),operation=j['settings'].get('source_kind','generation'),created=j['settings'].get('created',0),state=j['state'],notes=j['metadata'].get('notes',''),settings=j['settings'],snapshots=revisions(self.output/j['id'])) for j in jobs.values() if j['id'] in related]
            return dict(nodes=sorted(nodes,key=lambda n:n['created']),selected=identifier)
        @app.post('/api/jobs/{identifier}/checkpoints',status_code=201)
        async def save_checkpoint(identifier:str,request:Request):
            async with self.g['LOCK']:
                folder,_=self.available(identifier)
                try:return await asyncio.to_thread(checkpoint,folder,(await request.json()).get('label','Checkpoint'))
                except ValueError as e:raise HTTPException(400,str(e))
        @app.post('/api/jobs/{identifier}/restore-version',status_code=201)
        async def restore_version(identifier:str,request:Request):
            async with self.g['LOCK']:
                folder,record=self.available(identifier);data=await request.json();revision=data.get('revision')
                if not isinstance(revision,str) or not ID.fullmatch(revision):raise HTTPException(400,'Choose a checkpoint.')
                source=folder/'Revisions'/revision
                if not (source/'revision.json').is_file():raise HTTPException(404)
                destination=self.output/uuid.uuid4().hex;destination.mkdir()
                for file in SNAPSHOT_FILES:
                    if (source/file).is_file():shutil.copy2(source/file,destination/file)
                for dirname in SNAPSHOT_DIRS:
                    if (source/dirname).is_dir():shutil.copytree(source/dirname,destination/dirname)
                if (folder/'input.png').is_file():shutil.copy2(folder/'input.png',destination/'input.png')
                settings=read(destination/'settings.json');settings.update(parent_id=identifier,created=time.time(),restored_revision=revision,source_kind='restored');write(destination/'settings.json',settings)
                metadata=read(destination/'metadata.json',{});metadata['display_name']=(metadata.get('display_name',settings['name'])+' · restored')[:80];write(destination/'metadata.json',metadata);write(destination/'status.json',dict(state='done',stage='Checkpoint restored as new version',progress=100,result=read(destination/'result.json',{})));return dict(id=destination.name)

        @app.post('/api/jobs/{identifier}/presentation')
        async def presentation_save(identifier:str,kind:str=Form(...),recipe:str=Form('{}'),file:UploadFile=File(...)):
            folder,record=self.available(identifier)
            if kind not in ('thumbnail','capture','turntable'):raise HTTPException(400,'Unknown presentation format.')
            content=await file.read(100*1024**2+1);await file.close()
            if not content or len(content)>100*1024**2:raise HTTPException(400,'Capture limit is 100 MB.')
            try:
                settings=json.loads(recipe)
                if not isinstance(settings,dict) or len(recipe)>10000:raise ValueError('Invalid presentation settings.')
                if kind!='turntable':
                    import io
                    image=Image.open(io.BytesIO(content));image.verify()
                    if image.format!='PNG' or image.width*image.height>4096**2:raise ValueError('Choose a PNG capture up to 4096² pixels.')
                elif content[:4]!=b'\x1aE\xdf\xa3':raise ValueError('Choose a WebM turntable recording.')
            except (ValueError,OSError) as e:raise HTTPException(400,str(e))
            target=folder/'Presentation';target.mkdir(exist_ok=True);filename={'thumbnail':'thumbnail.png','capture':'capture.png','turntable':'turntable.webm'}[kind];temp=target/(filename+'.tmp');temp.write_bytes(content);temp.replace(target/filename)
            manifest=read(target/'presentation.json',{});manifest[kind]=dict(file=filename,revision=time.time_ns(),bytes=len(content),recipe=settings);write(target/'presentation.json',manifest);return manifest
        @app.get('/api/jobs/{identifier}/presentation/{filename}')
        def presentation_file(identifier:str,filename:str):
            if filename not in ('thumbnail.png','capture.png','turntable.webm'):raise HTTPException(404)
            path=self.g['job_path'](identifier)/'Presentation'/filename
            if not path.is_file():raise HTTPException(404)
            return FileResponse(path,filename=filename,media_type='video/webm' if filename.endswith('webm') else 'image/png')

        @app.post('/api/transfers/inspect',status_code=201)
        async def transfer_inspect(file:UploadFile=File(...)):
            from studio_transfer import inspect,extract
            target=self.workspace.root/'imports'/uuid.uuid4().hex;target.mkdir(parents=True);path=target/'archive.zip';total=0
            try:
                with path.open('wb') as stream:
                    while chunk:=await file.read(4*1024**2):
                        total+=len(chunk)
                        if total>12*1024**3:raise ValueError('Archive exceeds 12 GB.')
                        stream.write(chunk)
                manifest=await asyncio.to_thread(inspect,path);await asyncio.to_thread(extract,path,target/'verified',manifest);write(target/'manifest.json',manifest)
            except (ValueError,OSError,KeyError,TypeError,Exception) as e:raise HTTPException(400,'Collection archive could not be verified: '+str(e))
            finally:await file.close()
            return dict(id=target.name,models=manifest['models'],bytes=total,folders=len(manifest.get('workspace',{}).get('folders',[])),profiles=len(manifest.get('profiles',[])))
        @app.post('/api/transfers/{identifier}/restore',status_code=201)
        async def transfer_restore(identifier:str,request:Request):
            if not ID.fullmatch(identifier):raise HTTPException(404)
            target=self.workspace.root/'imports'/identifier;manifest=read(target/'manifest.json')
            if not manifest:raise HTTPException(404)
            async with self.g['LOCK']:
                if (target/'restored.json').is_file():raise HTTPException(409,'This archive has already been restored.')
                try:label=name((await request.json()).get('name','Restored collection'))
                except ValueError as e:raise HTTPException(400,str(e))
                original=copy.deepcopy(self.workspace.data);profiles_path=self.g['PROFILES'].path;profile_bytes=profiles_path.read_bytes() if profiles_path.is_file() else None;created=[]
                try:
                    root=self.workspace.save_folder(dict(name=label,parent_id=''));folders={};old_folders=manifest.get('workspace',{}).get('folders',[])
                    for f in old_folders:folders[f['id']]=uuid.uuid4().hex
                    for f in old_folders:self.workspace.data['folders'].append(dict(id=folders[f['id']],name=name(f['name']),parent_id=folders.get(f.get('parent_id'),root['id']),created=time.time()))
                    for view in manifest.get('workspace',{}).get('views',[]):self.workspace.save_view(dict(name=view['name'],query=view['query'],filter=view['filter'],folder_id=folders.get(view['folder_id'],root['id'])))
                    profile_ids={};existing=self.g['PROFILES'].list()
                    for p in manifest.get('profiles',[]):
                        same=next((v for v in existing if v['settings']==p['settings'] and v['name']==p['name']),None)
                        if same:profile_ids[p['id']]=same['id'];continue
                        if p.get('builtin'):continue
                        title=p['name'][:46];suffix=1;candidate=title
                        while any(v['name'].casefold()==candidate.casefold() for v in existing):suffix+=1;candidate=title+' (import '+str(suffix)+')'
                        saved=self.g['PROFILES'].save(dict(name=candidate,settings=p['settings']));existing.append(saved);profile_ids[p['id']]=saved['id']
                    mapping={m['id']:uuid.uuid4().hex for m in manifest['models']}
                    for item in manifest['models']:
                        source=target/'verified'/'models'/item['id'];destination=self.output/mapping[item['id']]
                        if destination.resolve().parent!=self.output:raise ValueError('Restore target rejected.')
                        shutil.copytree(source,destination);created.append(destination)
                        for p in [destination/'settings.json',*destination.glob('Revisions/*/settings.json')]:
                            settings=read(p);parent=settings.get('parent_id')
                            settings.pop('finish_source_id', None)
                            settings.pop('keep_finishing_source', None)
                            if parent:
                                if parent in mapping:settings['parent_id']=mapping[parent]
                                else:settings.pop('parent_id',None);settings['external_parent_id']=parent
                            if settings.get('profile',{}).get('id') in profile_ids:settings['profile']['id']=profile_ids[settings['profile']['id']]
                            write(p,settings)
                        for p in [destination/'metadata.json',*destination.glob('Revisions/*/metadata.json')]:
                            metadata=read(p,{});metadata['folder_id']=folders.get(metadata.get('folder_id'),root['id']);write(p,metadata)
                        write(destination/'status.json',dict(state='done',stage='Restored from collection archive',progress=100,result=read(destination/'result.json',{})))
                    self.workspace.save();write(target/'restored.json',dict(ids=list(mapping.values()),created=time.time()));return dict(ids=list(mapping.values()),folder_id=root['id'])
                except Exception as e:
                    # Only newly allocated restore folders belong to this rollback.
                    for p in created:
                        if p.resolve().parent==self.output and ID.fullmatch(p.name):shutil.rmtree(p)
                    self.workspace.data=original;self.workspace.save()
                    if profile_bytes is None:profiles_path.unlink(missing_ok=True)
                    else:profiles_path.write_bytes(profile_bytes)
                    raise HTTPException(400,'The collection could not be restored; no models were replaced: '+str(e))
