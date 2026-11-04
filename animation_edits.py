"""Recovery for edits of an existing model's animation set."""
from pathlib import Path
import json, shutil

FILES=('model.glb','model.blend','result.json','settings.json')
def recover(folder):
    marker=folder/'animation-transaction.json'
    if not marker.is_file():return
    data=json.loads(marker.read_text(encoding='utf-8'))
    stage=(folder/data['stage']).resolve()
    if stage.parent!=folder.resolve() or not stage.name.startswith('.animation-'):raise ValueError('Zone de récupération invalide.')
    names=data.get('files',FILES)
    allowed=(*FILES,'motion-report.json',*(f'LODs/LOD{i}.glb' for i in range(5)))
    if any(name not in allowed for name in names):raise ValueError('Invalid recovery file.')
    for name in names:
        backup=stage/'backup'/name
        if backup.is_file():(folder/name).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(backup,folder/name)
        elif name in data.get('missing',[]):(folder/name).unlink(missing_ok=True)
    marker.unlink()
