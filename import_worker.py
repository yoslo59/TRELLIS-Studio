"""Short-lived import process. Never uses the user's open Blender scene."""
from pathlib import Path
import json, os, subprocess, sys, time, traceback
from studio_import import find_blender, validate_glb, animation_recipes

ROOT = Path(__file__).resolve().parent
JOB = Path(sys.argv[1]).resolve()
started = time.time()

def status(state, stage, progress, **extra):
    path = JOB/'status.tmp'
    path.write_text(json.dumps(dict(state=state, stage=stage, progress=progress, elapsed=round(time.time()-started,1), **extra), ensure_ascii=False), encoding='utf-8')
    path.replace(JOB/'status.json')
    print(stage, flush=True)

try:
    status('running', 'Reading imported model', 8)
    blender = find_blender()
    if blender is None: raise RuntimeError('Blender introuvable. Définir TRELLIS_BLENDER ou installer Blender.')
    status('running', 'Preparing GLB and thumbnail', 25)
    command = [str(blender), '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1', '--python', str(ROOT/'import_blender.py'), '--', str(JOB)]
    process = subprocess.Popen(command, stdout=sys.stdout, stderr=subprocess.STDOUT, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try: code = process.wait(timeout=240)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait()
        raise RuntimeError('Import trop long. Réduire la taille du modèle puis réessayer.')
    if code: raise RuntimeError('Blender n’a pas pu importer ce fichier. Consulter le journal de l’objet.')
    data=validate_glb(JOB/'model.glb')
    result = json.loads((JOB/'result.json').read_text(encoding='utf-8'))
    settings = json.loads((JOB/'settings.json').read_text(encoding='utf-8'))
    settings.update(height_m=result['height_m'], texture_size=result['texture_size'], triangles=result['triangles'],animation_clips=animation_recipes(data))
    (JOB/'settings.json').write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding='utf-8')
    status('done', 'Model imported', 100, result=result)
except Exception as error:
    traceback.print_exc()
    status('error', 'Import failed', 0, error=str(error))
