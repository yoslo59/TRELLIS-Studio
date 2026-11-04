"""Data-only, hashed collection archives; restoring always allocates fresh IDs."""
from pathlib import Path,PurePosixPath
import hashlib,json,re,shutil,stat,uuid,zipfile
from studio_workspace import read,write,ID
from studio_import import validate_glb

EXTENSIONS={'.json','.glb','.blend','.png','.jpg','.jpeg','.webp','.stl','.fbx','.obj','.ply','.raw','.mtl','.webm'}
EXCLUDED={'Blender','rig-work','before-animations','__pycache__'}
MAX_TOTAL=12*1024**3
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(4*1024*1024):h.update(block)
    return h.hexdigest()
def build(sources,destination,workspace,profiles,include_revisions=False,notify=lambda *args:None):
    sources=Path(sources);manifest=dict(format='trellis-collection',version=1,studio_version='3.0.0',models=[],workspace=workspace,profiles=profiles,files=[])
    folders=[p for p in sources.iterdir() if p.is_dir() and ID.fullmatch(p.name)]
    used={read(p/'metadata.json',{}).get('folder_id','') for p in folders};catalog={f['id']:f for f in workspace.get('folders',[])}
    for _ in range(500):
        parents={catalog[i].get('parent_id','') for i in used if i in catalog}-used
        if not parents:break
        used.update(parents)
    manifest['workspace']={**workspace,'folders':[f for f in workspace.get('folders',[]) if f['id'] in used],'views':[v for v in workspace.get('views',[]) if v.get('folder_id') in used|{'*',''}]}
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as archive:
        for index,folder in enumerate(folders):
            settings=read(folder/'settings.json');metadata=read(folder/'metadata.json',{})
            manifest['models'].append(dict(id=folder.name,name=metadata.get('display_name',settings['name']),parent_id=settings.get('parent_id','')))
            for path in folder.rglob('*'):
                relative=path.relative_to(folder)
                if not path.is_file() or path.is_symlink() or any(part in EXCLUDED or part.startswith('.') for part in relative.parts) or path.suffix.lower() not in EXTENSIONS:continue
                if not include_revisions and relative.parts[0]=='Revisions':continue
                file='models/'+folder.name+'/'+relative.as_posix();size=path.stat().st_size
                manifest['files'].append(dict(path=file,bytes=size,sha256=digest(path)));archive.write(path,file)
            notify((index+1)/max(1,len(folders))*.9,'Packing '+manifest['models'][-1]['name'])
        archive.writestr('trellis-collection.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest

def inspect(path):
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        if len(entries)>25000 or sum(i.file_size for i in entries)>MAX_TOTAL:raise ValueError('Archive exceeds the 12 GB / 25,000 file limit.')
        names=set();folded=set()
        for info in entries:
            p=PurePosixPath(info.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in info.filename or ':' in info.filename or stat.S_ISLNK(info.external_attr>>16) or info.filename.casefold() in folded or any(part.endswith((' ','.')) or len(part)>180 or re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?',part,re.I) for part in p.parts):raise ValueError('Unsafe or duplicate archive path.')
            names.add(info.filename);folded.add(info.filename.casefold())
        if 'trellis-collection.json' not in names:raise ValueError('Choose a TRELLIS collection archive.')
        info=archive.getinfo('trellis-collection.json')
        if info.file_size>20*1024**2:raise ValueError('Archive manifest is too large.')
        data=json.loads(archive.read(info))
        if data.get('format')!='trellis-collection' or data.get('version')!=1:raise ValueError('Unsupported collection format.')
        models=data.get('models',[]);ids=[m['id'] for m in models]
        if not 1<=len(ids)<=500 or len(set(ids))!=len(ids) or any(not ID.fullmatch(i) for i in ids):raise ValueError('Invalid model list.')
        parents={m['id']:m.get('parent_id','') for m in models}
        for identifier in ids:
            seen=set();cursor=identifier
            while cursor in parents:
                if cursor in seen:raise ValueError('Cyclic model lineage.')
                seen.add(cursor);cursor=parents[cursor]
        workspace=data.get('workspace',{});folders=workspace.get('folders',[]);views=workspace.get('views',[]);profiles=data.get('profiles',[])
        if not isinstance(folders,list) or len(folders)>500 or not isinstance(views,list) or len(views)>100 or not isinstance(profiles,list) or len(profiles)>102:raise ValueError('Invalid workspace catalogue.')
        from studio_workspace import name
        catalog={f['id']:f for f in folders}
        if len(catalog)!=len(folders):raise ValueError('Duplicate folder IDs.')
        for f in folders:
            name(f['name']);identifier=f['id'];parent=f.get('parent_id','')
            if not ID.fullmatch(identifier) or parent and parent not in catalog:raise ValueError('Invalid folder hierarchy.')
            seen={identifier}
            while parent:
                if parent in seen:raise ValueError('Cyclic folder hierarchy.')
                seen.add(parent);parent=catalog[parent].get('parent_id','')
        for v in views:
            name(v['name'])
            if any(not isinstance(v.get(k),str) or len(v[k])>160 for k in ('query','filter','folder_id')) or v['folder_id'] not in {'*','',*catalog}:raise ValueError('Invalid saved view.')
        from generation_profiles import validate_recipe
        for p in profiles:validate_recipe(dict(name=p['name'],settings=p['settings']))
        expected={'trellis-collection.json'}
        for entry in data['files']:
            p=PurePosixPath(entry['path'])
            if len(p.parts)<3 or p.parts[0]!='models' or p.parts[1] not in ids or p.suffix.lower() not in EXTENSIONS or any(part in EXCLUDED or part.startswith('.') for part in p.parts):raise ValueError('Unsupported collection file.')
            info=archive.getinfo(entry['path'])
            if info.file_size!=entry['bytes'] or not re.fullmatch('[a-f0-9]{64}',entry['sha256']):raise ValueError('Archive file size or hash is invalid.')
            if entry['path'] in expected:raise ValueError('Duplicate manifest file.')
            expected.add(entry['path'])
        if names!=expected:raise ValueError('Archive contains unlisted files.')
        for identifier in ids:
            for required in ('settings.json','model.glb'):
                if f'models/{identifier}/{required}' not in expected:raise ValueError('A model is incomplete.')
        return data

def extract(path,target,data):
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for entry in data['files']:
            destination=(target/entry['path']).resolve()
            if not destination.is_relative_to(target):raise ValueError('Archive target rejected.')
            destination.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(entry['path']) as src,destination.open('wb') as dst:shutil.copyfileobj(src,dst,4*1024*1024)
            if digest(destination)!=entry['sha256']:raise ValueError('Archive checksum mismatch: '+entry['path'])
    for entry in data['models']:
        folder=target/'models'/entry['id'];validate_glb(folder/'model.glb');settings=read(folder/'settings.json')
        if not isinstance(settings,dict) or not isinstance(settings.get('name'),str) or len(settings['name'])>80:raise ValueError('Invalid model settings.')
    return target/'models'
