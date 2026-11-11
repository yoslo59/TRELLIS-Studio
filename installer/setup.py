"""Resumable Windows installation. Uses only the standard library until venvs exist."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,os,platform,shutil,subprocess,sys,tempfile,time,urllib.request,zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
MANIFEST=ROOT/'installer/manifest.json'

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024**2),b''):h.update(block)
    return h.hexdigest()

def run(*args,**kwargs):
    print('>',*map(str,args),flush=True)
    return subprocess.run(list(map(str,args)),cwd=ROOT,check=True,**kwargs)

def fetch(entry,target):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.is_file() and (not entry.get('bytes') or target.stat().st_size==entry['bytes']) and digest(target)==entry['sha256']:
        print('Verified:',target.relative_to(ROOT),flush=True);return
    if target.exists():raise RuntimeError('Existing file differs from the pinned download; preserved: '+str(target))
    part=target.with_name(target.name+'.part')
    for attempt in range(4):
        try:
            offset=part.stat().st_size if part.exists() else 0
            request=urllib.request.Request(entry['url'],headers={'User-Agent':'TRELLIS-Studio/3.1','Range':f'bytes={offset}-'})
            with urllib.request.urlopen(request,timeout=120) as response:
                append=offset>0 and response.status==206
                print('Downloading:',target.name,('(resuming)' if append else ''),flush=True)
                with part.open('ab' if append else 'wb') as out:
                    shutil.copyfileobj(response,out,8*1024**2)
            if entry.get('bytes') and part.stat().st_size!=entry['bytes']:raise RuntimeError('Incomplete download: '+target.name)
            if digest(part)!=entry['sha256']:raise RuntimeError('Checksum mismatch. Remove this .part file before retrying: '+str(part))
            part.replace(target);return
        except Exception:
            if attempt==3:raise
            time.sleep(2)

def extract(archive,target,strip_root=False):
    target=Path(target);base=target.resolve();target.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        infos=package.infolist()
        if len(infos)>50000 or sum(i.file_size for i in infos)>8*1024**3:raise RuntimeError('Archive limits exceeded.')
        prefixes={PurePosixPath(i.filename).parts[0] for i in infos if i.filename}
        if strip_root and len(prefixes)!=1:raise RuntimeError('Expected one source archive root.')
        for info in infos:
            parts=PurePosixPath(info.filename).parts
            if '\\' in info.filename or '..' in parts or any(':' in p for p in parts) or PurePosixPath(info.filename).is_absolute() or (info.external_attr>>16)&0o170000==0o120000:raise RuntimeError('Unsafe archive entry.')
            if strip_root:parts=parts[1:]
            if not parts:continue
            path=target.joinpath(*parts)
            if not path.resolve().is_relative_to(base):raise RuntimeError('Archive path escaped its target.')
            if info.is_dir():path.mkdir(parents=True,exist_ok=True);continue
            path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                if not path.is_file():raise RuntimeError('Existing extraction path is not a regular file: '+str(path))
                h=hashlib.sha256()
                with package.open(info) as source:
                    for block in iter(lambda:source.read(8*1024**2),b''):h.update(block)
                if digest(path)==h.hexdigest():continue
                raise RuntimeError('Existing extracted file differs and was preserved: '+str(path))
            with package.open(info) as source,path.open('wb') as out:shutil.copyfileobj(source,out)

def source(name,entry):
    target=ROOT/entry['destination'];marker=target/'.studio-source.json'
    if target.exists():
        if marker.is_file() and json.loads(marker.read_text()).get('sha256')==entry['sha256']:return
        # Adopt this workstation's already validated upstream installation only.
        if name=='engine' and (target/'.git').is_dir():
            head=subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True).strip()
            patches=ROOT/'runtime/engine-overrides/o_voxel'
            if head==entry['commit'] and all((target/'o-voxel/o_voxel'/p.name).is_file() and digest(target/'o-voxel/o_voxel'/p.name)==digest(p) for p in patches.glob('*.py')):
                marker.write_text(json.dumps(entry,indent=2));return
        if name=='unirig':
            provenance=ROOT/'UniRig/source-provenance.json'
            if provenance.is_file() and json.loads(provenance.read_text()).get('commit')==entry['commit']:
                marker.write_text(json.dumps(entry,indent=2));return
        raise RuntimeError('Existing unrecognised source directory was preserved: '+str(target))
    archive=ROOT/'.downloads'/(name+'-'+entry['commit']+'.zip');fetch(entry,archive)
    with tempfile.TemporaryDirectory(prefix='source-',dir=ROOT/'.downloads') as staging:
        extract(archive,Path(staging)/'source',strip_root=True)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.move(str(Path(staging)/'source'),str(target))
    if name=='engine':
        for path in (ROOT/'runtime/engine-overrides/o_voxel').glob('*.py'):
            shutil.copy2(path,target/'o-voxel/o_voxel'/path.name)
    marker.write_text(json.dumps(entry,indent=2))

def download_assets(manifest,rig=True):
    extracted=manifest.get('extracted_files',[])
    def ready(item):
        path=ROOT/item['path']
        return path.is_file() and path.stat().st_size==item['bytes'] and digest(path)==item['sha256']
    complete={name:all(ready(i) for i in extracted if i['archive']==name) for name in {i['archive'] for i in extracted}}
    for entry in manifest['assets']:
        if entry['group']=='rig' and not rig:continue
        if complete.get(entry['path']):
            print('Verified extracted assets:',entry['path'],flush=True);continue
        fetch(entry,ROOT/entry['path'])
    for archive in complete:
        if not complete[archive]:
            destination=next(i['destination'] for i in extracted if i['archive']==archive)
            extract(ROOT/archive,ROOT/destination)
    for item in extracted:
        if not ready(item):raise RuntimeError('Extracted asset verification failed: '+item['path'])
    pipeline=ROOT/'models/trellis2/pipeline.json'
    if not pipeline.exists():shutil.copy2(ROOT/'installer/pipeline.json',pipeline)

def bundled_web():
    if not (ROOT/'bundled-web.json').is_file():return False
    pinned=json.loads((ROOT/'package.json').read_text())['dependencies']
    for name,version in pinned.items():
        package=ROOT/'node_modules'/name/'package.json'
        if not package.is_file() or json.loads(package.read_text())['version']!=version:
            raise RuntimeError('Bundled viewer is incomplete. Run the Windows Setup again.')
    if not (ROOT/'node_modules/three/build/three.module.js').is_file() or not (ROOT/'node_modules/swagger-ui-dist/swagger-ui-bundle.js').is_file():
        raise RuntimeError('Bundled viewer assets are missing. Run the Windows Setup again.')
    return True


def ensure_web():
    if not bundled_web():run('npm.cmd','ci','--omit=dev','--ignore-scripts')


def ensure_venvs(uv):
    for environment in ('.venv','.mcp-venv'):
        target=ROOT/environment
        if not (target/'Scripts/python.exe').exists():run(uv,'venv','--python','3.11',target)
    return ROOT/'.venv/Scripts/python.exe'


def record_workshop():
    paths=('installer/setup.py','requirements-windows.txt','requirements-mcp-lock.txt')
    record={path:digest(ROOT/path) for path in paths}
    target=ROOT/'.studio-workshop-ready'
    stage=target.with_suffix('.pending')
    stage.write_text(json.dumps(record,sort_keys=True),encoding='utf-8')
    stage.replace(target)


def install_workshop(uv):
    # The workshop includes the CPU QR encoder for Server two-factor setup.
    names=set('annotated-doc annotated-types anyio click colorama fastapi h11 idna numpy opencv-python-headless opentelemetry-api pillow psutil pydantic pydantic_core python-multipart scipy starlette trimesh typing-inspection typing_extensions uvicorn'.split())
    requirements=[line.strip() for line in (ROOT/'requirements-windows.txt').read_text().splitlines()
                  if line.partition('==')[0].strip() in names]
    if len(requirements)!=len(names):raise RuntimeError('Workshop dependency list does not match the Windows lock.')
    python=ensure_venvs(uv)
    run(uv,'pip','install','--python',python,*requirements)
    run(uv,'pip','install','--python',ROOT/'.mcp-venv/Scripts/python.exe','-r',ROOT/'requirements-mcp-lock.txt')
    ensure_web()
    verify(rig=False,workshop_only=True)
    record_workshop()


def verify(rig=True,workshop_only=False):
    python=ROOT/'.venv/Scripts/python.exe'
    run(python,'-c',"import cv2,fastapi,uvicorn,numpy,psutil,scipy,trimesh; from PIL import Image; from studio_rest import install; from retarget_catalogue import ANIMATIONS, BY_ID; assert callable(cv2.QRCodeEncoder_create); assert len(ANIMATIONS)==len(BY_ID)==84; print('Workshop libraries, QR encoder and animation catalogue ready')")
    run(ROOT/'.mcp-venv/Scripts/python.exe','-c','from mcp.server import MCPServer;import httpx;print("MCP ready")')
    if not bundled_web():run('node','--check','web/studio.js')
    if workshop_only:return
    run(python,'-c',"import torch,cumesh,flex_gemm,rembg; assert torch.cuda.is_available(),'CUDA is unavailable'; print('GPU:',torch.cuda.get_device_name());print('TORCH:',torch.__version__)")
    check="from studio_import import find_blender; from retarget_catalogue import ANIMATIONS, ROOT; assert find_blender(),'Blender not found'; assert all((ROOT / a['source_model']).is_file() for a in ANIMATIONS),'Animation source missing'; print(f'Blender and {len(ANIMATIONS)} animations ready')"
    run(python,'-c',check)
    if rig:
        settings=json.loads((ROOT/'config/runtime.json').read_text(encoding='utf-8-sig'))
        linux=subprocess.check_output(['wsl.exe','-d',settings['wsl_distribution'],'--exec','wslpath','-a','-u',str(ROOT/'UniRig/check-runtime.sh')],text=True).strip()
        run('wsl.exe','-d',settings['wsl_distribution'],'--exec','bash',linux)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uv',type=Path);parser.add_argument('--skip-rig',action='store_true')
    parser.add_argument('--workshop-only',action='store_true',help='Install the local studio without GPU libraries, engine sources or model downloads.')
    parser.add_argument('--plan',action='store_true');parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--accept-dinov3-license',action='store_true');parser.add_argument('--wsl-distribution',default='Ubuntu-22.04')
    args=parser.parse_args();manifest=json.loads(MANIFEST.read_text())
    if args.plan:
        print(json.dumps(dict(sources=manifest['sources'],downloads=len(manifest['assets']),download_bytes=sum(a['bytes'] for a in manifest['assets'] if not(args.skip_rig and a['group']=='rig')),rig=not args.skip_rig,personal_data_included=False),indent=2));return
    if os.name!='nt' or platform.machine().lower() not in ('amd64','x86_64'):raise RuntimeError('This installer targets 64-bit Windows. See INSTALLATION.md.')
    if args.verify_only:verify(not args.skip_rig and not args.workshop_only,args.workshop_only);return
    if not args.uv or not args.uv.is_file():raise RuntimeError('Run Install TRELLIS Studio.cmd to provision uv/Python first.')
    if not bundled_web():
        for tool in ('node','npm.cmd'):
            if not shutil.which(tool):raise RuntimeError('Missing prerequisite: '+tool+'. See INSTALLATION.md.')
    if args.workshop_only:
        install_workshop(args.uv)
        print('WORKSHOP READY. Generation, rigging and Blender-dependent operations require their optional components.',flush=True)
        return
    if not args.accept_dinov3_license:raise RuntimeError('Read licenses/DINOv3-LICENSE.md and explicitly accept it in Install TRELLIS Studio.cmd.')
    minimum=40 if args.skip_rig else 60
    if shutil.disk_usage(ROOT).free<minimum*1024**3:raise RuntimeError(f'Keep at least {minimum} GiB free before installing. 80 GiB is recommended for a full setup.')
    if not shutil.which('nvidia-smi'):raise RuntimeError('Missing prerequisite: nvidia-smi. See INSTALLATION.md.')
    from studio_import import find_blender
    if not find_blender():raise RuntimeError('Install Blender 5.2 LTS before continuing. See INSTALLATION.md.')
    run('nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader')
    if not args.skip_rig:run('wsl.exe','-d',args.wsl_distribution,'--exec','true')
    source('engine',manifest['sources']['engine'])
    if not args.skip_rig:source('unirig',manifest['sources']['unirig'])
    python=ensure_venvs(args.uv)
    run(args.uv,'pip','install','--python',python,'torch==2.8.0','torchvision==0.23.0','xformers==0.0.32.post2','--index-url','https://download.pytorch.org/whl/cu128')
    run(args.uv,'pip','install','--python',python,'-r',ROOT/'requirements-windows.txt')
    wheels=ROOT/'engine/whl'
    for filename in ('cumesh-0.0.1-cp311-cp311-win_amd64.whl','flex_gemm-0.0.1-cp311-cp311-win_amd64.whl','o_voxel-0.0.1-cp311-cp311-win_amd64.whl'):
        run(args.uv,'pip','install','--python',python,'--no-deps',wheels/filename)
    run(args.uv,'pip','install','--python',ROOT/'.mcp-venv/Scripts/python.exe','-r',ROOT/'requirements-mcp-lock.txt')
    ensure_web()
    download_assets(manifest,not args.skip_rig)
    config=ROOT/'config/runtime.json';config.parent.mkdir(exist_ok=True)
    if not config.exists():config.write_text(json.dumps(dict(wsl_distribution=args.wsl_distribution),indent=2))
    if not args.skip_rig:
        linux=subprocess.check_output(['wsl.exe','-d',args.wsl_distribution,'--exec','wslpath','-a','-u',str(ROOT/'installer/install_unirig.sh')],text=True).strip()
        run('wsl.exe','-d',args.wsl_distribution,'--exec','bash',linux)
    verify(not args.skip_rig)
    record_workshop()
    print('INSTALLATION COMPLETE. Open TRELLIS.cmd launches Studio and MCP.',flush=True)

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,subprocess.CalledProcessError,ValueError) as error:
        print('INSTALLATION STOPPED:',error,file=sys.stderr);sys.exit(1)
