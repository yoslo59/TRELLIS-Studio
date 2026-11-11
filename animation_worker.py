"""Author motions and publish only a validated animation set."""
from collections import Counter
from pathlib import Path
import json, shutil, subprocess, sys, time, traceback, uuid
from studio_import import animation_recipe, animation_recipes, find_blender, validate_glb
from animation_edits import FILES, recover
from glb_data import remove_animation
from lod_animations import prepare as prepare_lod_clips
from studio_workspace import write

ROOT = Path(__file__).resolve().parent
JOB = Path(sys.argv[1]).resolve()
started = time.time()
request = json.loads((JOB/'animation-request.json').read_text(encoding='utf-8'))
previous_settings = request['settings']
previous_result = request.get('result') or {}
linked = request.get('linked') is True
defer_publish = request.get('defer_publish') is True
removing = 'remove_clip' in request or 'remove_index' in request
stage = JOB / ('.animation-' + uuid.uuid4().hex)
stage.mkdir()
(stage/'rig-work').mkdir()


def status(state, message, progress, **extra):
    record = dict(state=state, stage=message, progress=progress,
                  operation='animation_copy' if linked else 'animation',
                  elapsed=round(time.time()-started, 1))
    if not linked:
        record['result'] = previous_result
    write(JOB/'status.json', {**record, **extra})


def clip_names(model):
    return Counter(clip['name'] for clip in model.get('animations', []) if clip.get('name'))


def verify_clips(source, model, authored, changed):
    names = clip_names(model)
    retained = clip_names(source)
    for name in list(retained):
        recipe = animation_recipe(name)
        if recipe and recipe['id'] in changed:
            del retained[name]
    if retained - names:
        raise ValueError('The animation export did not preserve the existing clips.')
    added = set()
    for item in authored:
        if item['id'] in changed and item.get('clip') in names:
            added.add(item['id'])
    if set(changed) != added:
        raise ValueError('The animation export is missing an authored clip.')


def run_blender(script, timeout):
    blender = find_blender()
    if not blender:
        raise RuntimeError('Blender is unavailable.')
    subprocess.run(
        [str(blender), '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
         '--python', str(ROOT / script), '--', str(stage)],
        stdout=sys.stdout, stderr=subprocess.STDOUT, check=True, timeout=timeout,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if not (stage / 'model.blend').is_file():
        raise ValueError('Blender did not save the edited model.')


def animation_metadata(model, authored):
    by_name = {item.get('clip'): item for item in authored}
    result = []
    for clip in model.get('animations', []):
        name = clip.get('name')
        recipe = animation_recipe(name)
        if recipe:
            result.append(by_name.get(name, dict(id=recipe['id'], name=recipe['id'], clip=name,
                                                duration_s=recipe['duration'], source='Existing embedded clip')))
    return result


try:
    status('running', 'Preparing animations', 10)
    source = JOB / ('source.glb' if linked else 'model.glb')
    source_data = validate_glb(source)
    settings = dict(previous_settings)
    previous_clips = previous_result.get('animation_set') or []
    authored = {}
    if removing:
        status('running', 'Removing the selected animation', 35)
        remove_animation(source, stage / 'model.glb', request.get('remove_clip'), request.get('remove_index'))
        run_blender('animation_blend.py', 240)
        model = validate_glb(stage / 'model.glb')
    else:
        previous = {clip['id']: clip for clip in animation_recipes(source_data)}
        changed = [clip['id'] for clip in request['clips'] if previous.get(clip['id']) != clip]
        settings['asset_type'] = 'rigged'
        status('running', 'Adding motions to model', 35)
        if changed:
            write(stage/'settings.json', dict(settings, animation_clips=request['clips'], rig_options=None,
                                             regenerate_ids=changed, previous_animation_set=previous_clips))
            shutil.copy2(source, stage/'rig-work'/'rigged.glb')
            run_blender('standard_animations.py', 240 + 20 * (len(changed) - 1))
            authored = json.loads((stage/'result.json').read_text(encoding='utf-8'))
        else:
            shutil.copy2(source, stage/'model.glb')
            run_blender('animation_blend.py', 240)
        model = validate_glb(stage/'model.glb')
        if not model.get('skins') or (changed and not authored.get('humanoid_mapping')):
            raise ValueError('Standard humanoid motions could not be applied to this skeleton.')
        verify_clips(source_data, model, authored.get('animation_set', []), changed)
    settings['animation_clips'] = animation_recipes(model)
    result = {**previous_result, **authored, 'revision': time.time_ns(),
              'animation_set': animation_metadata(model, authored.get('animation_set', previous_clips)),
              'animations': len(model.get('animations', []))}
    if not removing:
        result['rig_engine'] = previous_result.get('rig_engine', 'existing')
    motion_path = JOB / 'motion-report.json'
    motion_report = result.get('motion_report') or {}
    if motion_path.is_file():
        motion_report = json.loads(motion_path.read_text(encoding='utf-8'))
    removed_files = []
    if motion_report.get('clip') and motion_report['clip'] not in clip_names(model):
        result.pop('motion_report', None)
        if motion_path.is_file():
            removed_files.append(motion_path.name)
    write(stage/'result.json', result)
    write(stage/'settings.json', settings)
    edited_files = (*FILES, *prepare_lod_clips(JOB, stage))
    changed_files = (*edited_files, *removed_files)
    marker = JOB/'animation-transaction.json'
    if not linked:
        backup = stage/'backup'
        backup.mkdir()
        missing = []
        for name in changed_files:
            if (JOB/name).is_file():
                (backup/name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(JOB/name, backup/name)
            else:
                missing.append(name)
        write(marker, {'stage': stage.name, 'files': changed_files, 'missing': missing})
    for name in edited_files:
        (stage/name).replace(JOB/name)
    for name in removed_files:
        (JOB/name).unlink()
    if linked:
        try:
            source.unlink()
        except PermissionError:
            print('The source snapshot is still in use and was retained.', flush=True)
    else:
        marker.unlink()
    message = 'Animation removed' if removing else 'Animations saved to model'
    status('ready' if defer_publish else 'done', message, 100, result=result, removed_files=removed_files)
except Exception as error:
    traceback.print_exc()
    if linked:
        message = str(error)
        if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
            message = 'Blender could not finish preparing the animations. See the generation log for details.'
        status('error', 'Animation preparation failed', 0, error=message)
    else:
        recover(JOB)
        status('done', 'Animation update failed · model preserved', 100, operation_error=str(error))
finally:
    if stage.resolve().parent == JOB and stage.name.startswith('.animation-'):
        shutil.rmtree(stage, ignore_errors=True)
