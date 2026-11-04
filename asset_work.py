"""Private copy preparation / transactional clip editing, never a live Blender scene."""
from pathlib import Path
import json,re,shutil,subprocess,sys,time,traceback,uuid
from studio_import import find_blender,validate_glb
from studio_workspace import read,write,checkpoint
from animation_edits import FILES,recover
from lod_animations import prepare as prepare_lod_clips

ROOT=Path(__file__).resolve().parent;JOB=Path(sys.argv[1]).resolve();request=read(JOB/'work-request.json');started=time.time();editing=request['operation']=='motion';stage=JOB/('.animation-'+uuid.uuid4().hex) if editing else JOB
def status(state,label,progress,**extra):write(JOB/'status.json',dict(state=state,stage=label,progress=progress,operation='animation' if editing else request['operation'],elapsed=round(time.time()-started,1),result=request.get('result'),**extra))
try:
    if editing:
        stage.mkdir();(stage/'backup').mkdir();shutil.copy2(JOB/'model.glb',stage/'source.glb');write(stage/'settings.json',request['settings']);write(stage/'work-request.json',request)
    status('running','Preparing '+{'lod':'detail levels','game':'game copy','motion':'animation edit'}[request['operation']],8)
    subprocess.run([str(find_blender()),'--background','--factory-startup','--disable-autoexec','--threads','4','--python-exit-code','1','--python',str(ROOT/'work_blender.py'),'--',str(stage)],check=True,timeout=1800,stdout=sys.stdout,stderr=subprocess.STDOUT)
    validate_glb(stage/'model.glb');result={**request.get('result',{}),**read(stage/'work-result.json'),'revision':time.time_ns()};write(stage/'result.json',result)
    settings=read(stage/'settings.json')
    if editing:
        edited={**request['options'],'source_clip':request['options']['clip'],'clip':result['motion_report']['clip']}
        match=re.match(r'^TS_(.+)_[0-9.]+s$',request['options']['clip'])
        if match:
            for c in settings.get('animation_clips',[]):
                if c['id']==match[1]:c['duration']=edited['duration']
            for c in result.get('animation_set',[]):
                if c['id']==match[1]:c.update(clip=edited['clip'],duration_s=edited['duration'])
            write(stage/'result.json',result)
        edits=[e for e in settings.get('motion_edits',[]) if e['clip'] not in (edited['clip'],edited['source_clip'])]+[edited];settings['motion_edits']=edits;write(stage/'settings.json',settings)
        checkpoint(JOB,'Before motion edit','motion')
        edited_files=(*FILES,'motion-report.json',*prepare_lod_clips(JOB,stage))
        for file in edited_files:
            if (JOB/file).is_file():(stage/'backup'/file).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(JOB/file,stage/'backup'/file)
        write(JOB/'animation-transaction.json',dict(stage=stage.name,files=list(edited_files),missing=[f for f in edited_files if not (JOB/f).is_file()]))
        for file in edited_files:(stage/file).replace(JOB/file)
        (JOB/'animation-transaction.json').unlink()
    request['result']=result;status('done',{'lod':'LOD set ready','game':'Game copy ready','motion':'Animation edit saved'}[request['operation']],100)
except Exception as error:
    traceback.print_exc()
    if editing:recover(JOB);status('done','Previous model preserved',100,operation_error=str(error))
    else:status('error','Preparation failed',0,error=str(error))
finally:
    if editing and stage.resolve().parent==JOB and stage.name.startswith('.animation-'):shutil.rmtree(stage,ignore_errors=True)
