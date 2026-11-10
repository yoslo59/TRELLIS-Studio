"""A short-lived target export worker; the UI polls a separate, cancellable task."""
from pathlib import Path
import json,re,shutil,sys,time,traceback,zipfile
from target_export import export_asset,TARGETS
from studio_workspace import write

ROOT=Path(__file__).resolve().parent;TASK=Path(sys.argv[1]);request=json.loads((TASK/'request.json').read_text(encoding='utf-8'));start=time.time()
def status(state,stage,progress,**extra):
    write(TASK/'status.json',dict(state=state,stage=stage,progress=progress,target=request['target'],count=len(request['ids']),elapsed=round(time.time()-start,1),**extra));print(stage,flush=True)
try:
    target=request['target']
    if target=='library':
        from studio_transfer import build
        manifest=build(TASK/'sources',TASK/'package.zip.tmp',request['workspace'],request['profiles'],request.get('include_revisions',False),lambda fraction,label:status('running',label,round(fraction*100)))
        (TASK/'package.zip.tmp').replace(TASK/'package.zip');status('done','Collection backup ready',100,bytes=(TASK/'package.zip').stat().st_size,items=manifest['models']);sys.exit(0)
    package=TASK/'package';package.mkdir();items=[];preflight=request.get('preflight')
    for index,identifier in enumerate(request['ids']):
        source=TASK/'sources'/identifier;metadata=json.loads((source/'metadata.json').read_text(encoding='utf-8'));settings=json.loads((source/'settings.json').read_text(encoding='utf-8'))
        name=metadata.get('display_name',settings['name']);safe=re.sub('[^A-Za-z0-9_-]+','-',name).strip('-')[:60] or 'Model'
        status('running',f'Exporting {index+1}/{len(request["ids"])} · {name}',round(index/len(request['ids'])*85)+2)
        manifest=export_asset(
            source, package/(safe+'-'+identifier[:8]), target,
            lambda fraction,stage:status('running',f'{index+1}/{len(request["ids"])} · {stage}',round((index+fraction)/len(request['ids'])*85)+2),
            preflight_budget=preflight['budget'] if preflight else None,
            expected_hash=preflight['source_hashes'][identifier] if preflight else None,
        )
        items.append(dict(id=identifier,name=name,clips=len(manifest['animations']),file=safe+'-'+identifier[:8]))
    if target=='unity':
        editor=package/'Editor';editor.mkdir();shutil.copy2(ROOT/'unity/TrellisPackageImporter.cs',editor/'TrellisPackageImporter.cs')
    (package/'export-manifest.json').write_text(json.dumps(dict(target=target,recipe=TARGETS[target],models=items),indent=2),encoding='utf-8')
    status('running','Compressing export package',90)
    with zipfile.ZipFile(TASK/'package.zip.tmp','w',zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob('*'):
            if path.is_file():archive.write(path,path.relative_to(package))
    (TASK/'package.zip.tmp').replace(TASK/'package.zip')
    status('done','Export ready',100,bytes=(TASK/'package.zip').stat().st_size,items=items)
except Exception as error:
    traceback.print_exc();status('error','Export failed; original models preserved',0,error=str(error))
