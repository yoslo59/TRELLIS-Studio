"""Recovery for edits of an existing model's animation set."""
from pathlib import Path
import json, os, shutil, time
from studio_workspace import ID, read, write

FILES=('model.glb','model.blend','result.json','settings.json')


def replace_file(source, target):
    for attempt in range(30):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(.02)


def recover(folder):
    marker=folder/'animation-transaction.json'
    if not marker.is_file():return
    data=json.loads(marker.read_text(encoding='utf-8'))
    stage=(folder/data['stage']).resolve()
    if stage.parent!=folder.resolve() or not stage.name.startswith('.animation-'):raise ValueError('Zone de récupération invalide.')
    names=data.get('files',FILES)
    allowed=(*FILES,'status.json','motion-report.json',*(f'LODs/LOD{i}.glb' for i in range(5)))
    if any(name not in allowed for name in names):raise ValueError('Invalid recovery file.')
    for name in names:
        backup = stage/'backup'/name
        target = folder/name
        if backup.is_file():
            if target.is_file() and os.path.samefile(backup, target):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            restored = stage/'restore'/name
            restored.parent.mkdir(parents=True, exist_ok=True)
            restored.unlink(missing_ok=True)
            try:
                os.link(backup, restored)
            except OSError:
                shutil.copy2(backup, restored)
            replace_file(restored, target)
        elif name in data.get('missing', []):
            target.unlink(missing_ok=True)
    marker.unlink()


def publish_operation(folder, operation, state, revision):
    """Publish an already verified server animation update with a reversible commit."""
    if not ID.fullmatch(operation.name) or not folder.is_dir() or folder.resolve() != folder:
        raise ValueError('The source model is no longer available.')
    marker = folder/'animation-transaction.json'
    stage = folder/('.animation-' + operation.name)
    if marker.is_file():
        recover(folder)
    if stage.exists():
        if stage.resolve() != stage or not stage.is_dir():
            raise ValueError('Invalid animation backup directory.')
        shutil.rmtree(stage)

    current = read(folder/'status.json', {})
    if current.get('last_animation_operation') == operation.name:
        return
    if current.get('state') != 'done' or str((folder/'model.glb').stat().st_mtime_ns) != revision:
        raise ValueError('The source model changed while the animation update was running.')
    removed = state.get('removed_files', [])
    if not isinstance(removed, list) or any(name != 'motion-report.json' for name in removed):
        raise ValueError('Invalid animation update files.')
    files = [*FILES, *(f'LODs/LOD{i}.glb' for i in range(5)
                       if (operation/f'LODs/LOD{i}.glb').is_file())]
    if 'motion-report.json' not in removed and (operation/'motion-report.json').is_file():
        files.append('motion-report.json')
    result = state.get('result')
    if not isinstance(result, dict) or any(not (operation/name).is_file() for name in files):
        raise ValueError('The completed animation update is missing output files.')
    current.update(state='done', stage='Animations saved to model', progress=100,
                   result=result, last_animation_operation=operation.name)
    current.pop('operation_error', None)
    current.pop('operation', None)
    write(operation/'commit-status.json', current)
    replacements = {name: operation/name for name in files}
    replacements['status.json'] = operation/'commit-status.json'
    names = list(dict.fromkeys([*replacements, *removed]))
    missing = []
    try:
        for name in names:
            target = folder/name
            if not target.is_file():
                missing.append(name)
                continue
            backup = stage/'backup'/name
            backup.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(target, backup)
            except OSError:
                shutil.copy2(target, backup)
        write(marker, dict(stage=stage.name, files=names, missing=missing))
        for name in removed:
            (folder/name).unlink(missing_ok=True)
        for name, source in replacements.items():
            target = folder/name
            target.parent.mkdir(parents=True, exist_ok=True)
            replace_file(source, target)
        marker.unlink()
    except Exception:
        recover(folder)
        raise
    finally:
        if not marker.is_file() and stage.is_dir():
            shutil.rmtree(stage, ignore_errors=True)
