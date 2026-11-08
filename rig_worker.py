"""Studio rig worker: WSL inference, then local humanoid animation authoring."""
from pathlib import Path
import json, os, shutil, subprocess, sys, time, traceback
from studio_import import find_blender, validate_glb
from rig_landmarks import validate_rig_options

ROOT=Path(__file__).resolve().parent;JOB=Path(sys.argv[1]).resolve();started=time.time()
def status(state,stage,progress,**extra):
    path=JOB/'status.tmp';path.write_text(json.dumps(dict(state=state,stage=stage,progress=progress,elapsed=round(time.time()-started,1),**extra),ensure_ascii=False),encoding='utf-8');path.replace(JOB/'status.json')
def linux_path(path):
    return subprocess.check_output(['wsl.exe','-d',distro,'--exec','wslpath','-a','-u',str(path)],encoding='utf-8').strip()
try:
    config_path=ROOT/'config'/'runtime.json'
    config=json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
    distro=config.get('wsl_distribution','Ubuntu-22.04')
    settings=json.loads((JOB/'settings.json').read_text(encoding='utf-8'))
    if settings.get('rig_options'):
        settings['rig_options']=validate_rig_options(settings['rig_options'])
    data=validate_glb(JOB/'source.glb');work=JOB/'rig-work';work.mkdir(exist_ok=True)
    rig_input=JOB/'source.glb'
    if settings.get('rig_options'):
        status('running','Character orientation and height',4)
        subprocess.run([str(find_blender()),'--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(ROOT/'prepare_rig.py'),'--',str(JOB)],stdout=sys.stdout,stderr=subprocess.STDOUT,check=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        rig_input=work/'input.glb'
    if data.get('skins'):
        if settings.get('rig_options',{}).get('landmarks'):
            raise ValueError('Landmark placement requires an unrigged model; existing skin weights cannot be silently reused.')
        status('running','Squelette existant conservé · préparation des clips',80)
        shutil.copy2(rig_input,work/'rigged.glb')
        rig_engine='existing'
    else:
        status('running','Starting UniRig',5)
        command=['wsl.exe','-d',distro,'--exec','setsid','--wait','bash',linux_path(ROOT/'UniRig'/'run-model.sh'),linux_path(JOB)]
        subprocess.run(command,stdout=sys.stdout,stderr=subprocess.STDOUT,check=True,timeout=2400,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        rig_engine='UniRig'
    status('running','Preparing standard animations',88)
    subprocess.run([str(find_blender()),'--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(ROOT/'standard_animations.py'),'--',str(JOB)],stdout=sys.stdout,stderr=subprocess.STDOUT,check=True,timeout=240,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    data=validate_glb(JOB/'model.glb')
    if not data.get('skins'): raise RuntimeError('Le résultat ne contient aucun squelette skinné.')
    result=json.loads((JOB/'result.json').read_text(encoding='utf-8'));result['rig_engine']=rig_engine
    landmarks_report=work/'landmarks-report.json'
    if landmarks_report.is_file():result['rig_landmarks']=json.loads(landmarks_report.read_text(encoding='utf-8'))
    (JOB/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    status('done','Rig and animations ready' if result.get('animation_set') else 'Rig ready' if result.get('humanoid_mapping') else 'Rig ready · character mapping needs adjustment',100,result=result)
except Exception as error:
    pid_path=JOB/'rig-work'/'runtime.pid'
    if pid_path.is_file():
        try:
            pid=int(pid_path.read_text().strip())
            if pid>1:subprocess.run(['wsl.exe','-d',distro,'--exec','kill','-TERM','--','-'+str(pid)],capture_output=True,timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    traceback.print_exc();status('error','Rig interrupted',0,error=str(error))
