"""Studio rig worker: WSL inference, then local humanoid animation authoring."""
from pathlib import Path
import json, math, shutil, subprocess, sys, time, traceback
from studio_import import find_blender, validate_glb
from rig_landmarks import validate_rig_options
from studio_workspace import write

ROOT=Path(__file__).resolve().parent;JOB=Path(sys.argv[1]).resolve();started=time.time()
work = JOB / 'rig-work'
distro = 'Ubuntu-22.04'

def status(state,stage,progress,**extra):
    write(JOB/'status.json', dict(state=state, stage=stage, progress=progress,
                                 elapsed=round(time.time()-started,1), **extra))

def linux_path(path):
    return subprocess.check_output(['wsl.exe','-d',distro,'--exec','wslpath','-a','-u',str(path)],encoding='utf-8').strip()

def read_landmark_verification():
    try:
        report = json.loads((work / 'landmarks-report.json').read_text(encoding='utf-8'))
        verification = report['final_rig_verification']
        return verification if isinstance(verification, dict) else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def failure_status(phase, error):
    verification = read_landmark_verification() if phase == 'UniRig' else {}
    if verification.get('passed') is False:
        message = verification.get('error')
        if not isinstance(message, str) or not message.strip():
            message = 'The final rig did not preserve the requested joint landmarks.'
        details = {}
        tolerance = verification.get('tolerance_m')
        if type(tolerance) in (int, float) and math.isfinite(tolerance) and tolerance >= 0:
            details['tolerance_m'] = tolerance
        distances = verification.get('errors_m')
        if isinstance(distances, dict):
            details['errors_m'] = {
                name: distance for name, distance in distances.items()
                if type(distance) in (int, float) and math.isfinite(distance) and distance >= 0
            }
        return 'Joint landmark verification failed', dict(
            error=message, error_code='rig_landmark_verification_failed', error_details=details)
    if isinstance(error, subprocess.TimeoutExpired):
        message = f'{phase} timed out. See the generation log for details.'
    elif isinstance(error, subprocess.CalledProcessError):
        message = f'{phase} failed. See the generation log for details.'
    else:
        message = str(error) or 'See the generation log for details.'
    return 'Rig interrupted', dict(error=message)


phase = 'Rig setup'
try:
    config_path=ROOT/'config'/'runtime.json'
    config=json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
    distro=config.get('wsl_distribution',distro)
    settings=json.loads((JOB/'settings.json').read_text(encoding='utf-8'))
    if settings.get('rig_options'):
        settings['rig_options']=validate_rig_options(settings['rig_options'])
    data=validate_glb(JOB/'source.glb');work.mkdir(exist_ok=True)
    blender = find_blender()
    if not blender:
        raise RuntimeError('Blender is unavailable. Configure Blender before rigging a model.')
    rig_input=JOB/'source.glb'
    if settings.get('rig_options'):
        phase = 'Character orientation and height'
        status('running','Character orientation and height',4)
        subprocess.run([str(blender),'--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(ROOT/'prepare_rig.py'),'--',str(JOB)],stdout=sys.stdout,stderr=subprocess.STDOUT,check=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        rig_input=work/'input.glb'
    if data.get('skins'):
        if settings.get('rig_options',{}).get('landmarks'):
            raise ValueError('Landmark placement requires an unrigged model; existing skin weights cannot be silently reused.')
        status('running','Existing skeleton retained · preparing animations',80)
        shutil.copy2(rig_input,work/'rigged.glb')
        rig_engine='existing'
    else:
        phase = 'UniRig'
        status('running','Starting UniRig',5)
        command=['wsl.exe','-d',distro,'--exec','setsid','--wait','bash',linux_path(ROOT/'UniRig'/'run-model.sh'),linux_path(JOB)]
        subprocess.run(command,stdout=sys.stdout,stderr=subprocess.STDOUT,check=True,timeout=2400,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if settings.get('rig_options', {}).get('landmarks') and read_landmark_verification().get('passed') is not True:
            raise RuntimeError('UniRig did not complete joint landmark verification.')
        rig_engine='UniRig'
    phase = 'Preparing standard animations'
    status('running','Preparing standard animations',88)
    subprocess.run([str(blender),'--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(ROOT/'standard_animations.py'),'--',str(JOB)],stdout=sys.stdout,stderr=subprocess.STDOUT,check=True,timeout=240,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    data=validate_glb(JOB/'model.glb')
    if not data.get('skins'): raise RuntimeError('The result does not contain a skinned skeleton.')
    result=json.loads((JOB/'result.json').read_text(encoding='utf-8'));result['rig_engine']=rig_engine
    landmarks_report=work/'landmarks-report.json'
    if landmarks_report.is_file():result['rig_landmarks']=json.loads(landmarks_report.read_text(encoding='utf-8'))
    (JOB/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    status('done','Rig and animations ready' if result.get('animation_set') else 'Rig ready' if result.get('humanoid_mapping') else 'Rig ready · character mapping needs adjustment',100,result=result)
except Exception as error:
    stage, details = failure_status(phase, error)
    pid_path=work/'runtime.pid'
    if pid_path.is_file():
        try:
            pid=int(pid_path.read_text().strip())
            if pid>1:subprocess.run(['wsl.exe','-d',distro,'--exec','kill','-TERM','--','-'+str(pid)],capture_output=True,timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    traceback.print_exc()
    status('error', stage, 0, **details)
