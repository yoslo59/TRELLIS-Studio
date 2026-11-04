"""Append/update clips on the same library record, committing only valid exports."""
from pathlib import Path
import json,os,shutil,subprocess,sys,time,traceback,uuid
from studio_import import find_blender,validate_glb
from animation_edits import FILES,recover
from lod_animations import prepare as prepare_lod_clips

ROOT=Path(__file__).resolve().parent;JOB=Path(sys.argv[1]).resolve();started=time.time()
old=json.loads((JOB/'animation-request.json').read_text(encoding='utf-8'))
stage=JOB/('.animation-'+uuid.uuid4().hex);stage.mkdir();(stage/'rig-work').mkdir();(stage/'backup').mkdir()
def status(state,message,progress,**extra):
    path=JOB/'status.tmp';path.write_text(json.dumps(dict(state=state,stage=message,progress=progress,operation='animation',elapsed=round(time.time()-started,1),result=old.get('result'),**extra),ensure_ascii=False),encoding='utf-8');path.replace(JOB/'status.json')
try:
    status('running','Preparing animation',10)
    previous={c['id']:c for c in old['settings'].get('animation_clips',[])}
    changed=[c['id'] for c in old['clips'] if previous.get(c['id'])!=c]
    settings={**old['settings'],'animation_clips':old['clips'],'rig_options':None,'asset_type':'rigged','regenerate_ids':changed,'previous_animation_set':old.get('result',{}).get('animation_set') or [dict(id=c['id'],name=c['id'],duration_s=c['duration'],source='Existing embedded clip') for c in old['settings'].get('animation_clips',[])]}
    (stage/'settings.json').write_text(json.dumps(settings,ensure_ascii=False),encoding='utf-8')
    shutil.copy2(JOB/'model.glb',stage/'rig-work'/'rigged.glb')
    status('running','Adding motions to model',35)
    subprocess.run([str(find_blender()),'--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(ROOT/'standard_animations.py'),'--',str(stage)],stdout=sys.stdout,stderr=subprocess.STDOUT,check=True,timeout=240,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    data=validate_glb(stage/'model.glb');result=json.loads((stage/'result.json').read_text(encoding='utf-8'))
    if not result.get('humanoid_mapping') or len(result.get('animation_set',[]))!=len(old['clips']):raise ValueError('Le squelette ne permet pas d’appliquer ces motions humanoïdes.')
    result['rig_engine']=old.get('result',{}).get('rig_engine','existing');result['revision']=time.time_ns()
    (stage/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    settings.pop('regenerate_ids',None);settings.pop('previous_animation_set',None);(stage/'settings.json').write_text(json.dumps(settings,ensure_ascii=False),encoding='utf-8')
    edited_files=(*FILES,*prepare_lod_clips(JOB,stage))
    for name in edited_files:
        if (JOB/name).is_file():(stage/'backup'/name).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(JOB/name,stage/'backup'/name)
    marker=JOB/'animation-transaction.json';marker.write_text(json.dumps({'stage':stage.name,'files':edited_files}),encoding='utf-8')
    # The first pre-edit model is retained once, outside the gallery.
    original=JOB/'before-animations'
    if not original.exists():shutil.copytree(stage/'backup',original)
    for name in edited_files:(stage/name).replace(JOB/name)
    marker.unlink()
    old['result']=result
    status('done','Animations saved to model',100)
except Exception as error:
    traceback.print_exc();recover(JOB)
    status('done','Animation cancelled · model preserved',100,operation_error=str(error))
finally:
    if stage.resolve().parent==JOB and stage.name.startswith('.animation-'):shutil.rmtree(stage,ignore_errors=True)
