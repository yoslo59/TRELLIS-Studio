"""Short-lived import process. Never uses the user's open Blender scene."""
from pathlib import Path
import json, os, subprocess, sys, time, traceback, shutil
from studio_import import find_blender, validate_glb, animation_recipes

ROOT = Path(__file__).resolve().parent
JOB = Path(sys.argv[1]).resolve()
started = time.time()

def status(state, stage, progress, **extra):
    path = JOB/'status.tmp'
    path.write_text(json.dumps(dict(state=state, stage=stage, progress=progress, elapsed=round(time.time()-started,1), **extra), ensure_ascii=False), encoding='utf-8')
    path.replace(JOB/'status.json')
    print(stage, flush=True)


def import_glb_direct():
    import numpy as np
    from glb_data import read, posed_bounds, image
    source=JOB/'original.glb'
    data,binary=read(source)
    accessors=data.get('accessors',[])
    triangles=vertices=0
    for mesh in data['meshes']:
        for primitive in mesh.get('primitives',[]):
            vertex_count=accessors[primitive['attributes']['POSITION']]['count']
            count=accessors[primitive['indices']]['count'] if 'indices' in primitive else vertex_count
            mode=primitive.get('mode',4)
            triangles+=count//3 if mode==4 else max(0,count-2) if mode in (5,6) else 0
            vertices+=vertex_count
    if not triangles or triangles>5_000_000 or vertices>15_000_000:
        raise ValueError('A GLB needs mesh faces and must stay within the import size limits.')
    try:low,high=posed_bounds(data,binary)
    except (ValueError,KeyError,IndexError,TypeError,RecursionError) as error:
        raise ValueError('This GLB could not be inspected directly. Install Blender to import it.') from error
    size=high-low
    if not np.isfinite(size).all() or not np.isfinite(low).all() or max(size)<=0:
        raise ValueError('The GLB has invalid dimensions.')
    texture_size=0
    for index in range(len(data.get('images',[]))):
        try:
            with image(data,binary,index) as embedded:texture_size=max(texture_size,*embedded.size)
        except (ValueError,OSError,KeyError,IndexError):pass # e.g. a viewer-supported compressed texture
    shutil.copyfile(source,JOB/'model.glb')
    result=dict(imported=True,vertices=vertices,triangles=triangles,height_m=round(float(size[1]),5),
        dimensions_m=size.tolist(),texture_size=texture_size,materials=len(data.get('materials',[])),
        bones=len({j for skin in data.get('skins',[]) for j in skin.get('joints',[])}),
        animations=len(data.get('animations',[])),source_format='GLB',units='Metres as stored in the GLB')
    (JOB/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')

try:
    status('running', 'Reading imported model', 8)
    blender = find_blender()
    if blender is None:
        if not (JOB/'original.glb').is_file():raise RuntimeError('Blender is required for this format.')
        status('running', 'Inspecting the embedded GLB', 25)
        import_glb_direct()
    else:
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
