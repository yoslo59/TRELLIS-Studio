"""Portable OBJ + URP textures and a Unity editor importer; GLB stays PBR."""
from pathlib import Path
import json, shutil, zipfile
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent

def export_unity(mesh, folder, settings):
    folder = Path(folder)
    bundle = folder / 'Unity' / 'TRELLIS'
    package = bundle / 'Models' / (settings['name'] + '-' + folder.name[:8])
    package.mkdir(parents=True, exist_ok=True)
    material = mesh.visual.material
    color = material.baseColorTexture
    if color is None:
        color = Image.new('RGBA', (4, 4), (255, 255, 255, 255))
    color.convert('RGBA').save(package / 'BaseColor.png')
    mr = material.metallicRoughnessTexture
    if mr is None:
        mr = Image.new('RGB', color.size, (0, 128, 0))
    mr = np.asarray(mr.convert('RGB'), dtype=np.uint8)
    Image.fromarray(mr).save(package / 'MetallicRoughness.png')
    packed = np.zeros((*mr.shape[:2], 4), dtype=np.uint8)
    packed[..., 0] = mr[..., 2]
    packed[..., 3] = 255 - mr[..., 1]
    Image.fromarray(packed).save(package / 'MetallicSmoothness.png')
    obj = package / 'Model.obj'
    uv = np.asarray(mesh.visual.uv)
    normals = np.asarray(mesh.vertex_normals)
    with obj.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write('# TRELLIS local / Y up / metres / pivot at floor\nmtllib Model.mtl\no Model\n')
        for vertex in mesh.vertices:
            stream.write('v %.7f %.7f %.7f\n' % tuple(vertex))
        for coord in uv:
            stream.write('vt %.7f %.7f\n' % tuple(coord))
        for normal in normals:
            stream.write('vn %.7f %.7f %.7f\n' % tuple(normal))
        stream.write('usemtl TrellisSurface\n')
        for face in mesh.faces + 1:
            stream.write('f ' + ' '.join(f'{i}/{i}/{i}' for i in face) + '\n')
    (package / 'Model.mtl').write_text('newmtl TrellisSurface\nKd 1 1 1\nKa 0 0 0\nKs 0 0 0\nd 1\nillum 2\nmap_Kd BaseColor.png\n', encoding='utf-8')
    manifest = {
        'name': settings['name'], 'obj': 'Model.obj', 'baseColor': 'BaseColor.png',
        'metallicSmoothness': 'MetallicSmoothness.png', 'metallicRoughness': 'MetallicRoughness.png',
        'height_m': settings['height_m'], 'triangles': len(mesh.faces), 'vertices': len(mesh.vertices),
        'seed': settings['seed'], 'engine': 'TRELLIS.2', 'resolution': settings.get('resolution',512),
    }
    (package / 'trellis-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    editor = bundle / 'Editor'
    editor.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'unity' / 'TrellisUnityImporter.cs', editor / 'TrellisUnityImporter.cs')
    (bundle / 'LIRE-UNITY.txt').write_text(
        'IMPORT UNITY 6 / URP\n\n1. Decompresser ce ZIP.\n2. Copier le dossier TRELLIS dans Assets.\n'
        '3. Attendre la compilation du script Editor.\n4. Menu Outils > TRELLIS > Creer les prefabs URP.\n'
        'Le prefab et le materiau sont crees a cote du modele.\n\n'
        'Echelle en metres, axe Y vertical, pivot au sol. La hauteur est celle choisie dans l interface.\n'
        'Le GLB garde les materiaux PBR pour Blender ou un importeur glTF Unity.\n'
        'MetallicSmoothness.png : R=metal, A=1-rugosite ; texture lineaire.\n'
        'Pas de collider, LOD, interieur visitable ou destructibilite generes automatiquement.\n', encoding='utf-8')
    archive = folder / 'Unity.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
        for path in bundle.rglob('*'):
            if path.is_file():
                stream.write(path, path.relative_to(bundle.parent))
    return manifest
