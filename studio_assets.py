"""Portable Blender bundle and inspection assets, using the installed mesh stack."""
from pathlib import Path
import hashlib, json, zipfile
import numpy as np
import trimesh
from PIL import Image, ImageDraw

EXPORT_VERSION = 6

def prepare_blender(folder):
    folder = Path(folder)
    source = folder / 'model.glb'
    if not source.is_file():
        raise ValueError('This model has no GLB file.')
    target = folder / 'Blender'
    manifest_path = target / 'manifest.json'
    if manifest_path.exists() and (folder/'Blender.zip').exists():
        cached = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (cached.get('export_version') == EXPORT_VERSION and cached.get('source_size') == source.stat().st_size
            and cached.get('source_mtime_ns') == source.stat().st_mtime_ns):
            return cached
    scene = trimesh.load(source, force='scene', process=False)
    target.mkdir(exist_ok=True)
    meshes = []
    textures = []
    for index, node in enumerate(scene.graph.nodes_geometry):
        transform, geometry = scene.graph[node]
        mesh = scene.geometry[geometry]
        uv = np.asarray(getattr(mesh.visual, 'uv', []), dtype=float)
        valid_uv = uv.shape == (len(mesh.vertices), 2) and bool(np.isfinite(uv).all())
        material = getattr(mesh.visual, 'material', None)
        prefix = f'mesh-{index+1:02d}'
        maps = {}
        for attr, label, color_space in [('baseColorTexture','BaseColor','sRGB'),
                ('metallicRoughnessTexture','MetallicRoughness','Non-Color'),
                ('normalTexture','Normal','Non-Color'),('occlusionTexture','Occlusion','Non-Color'),
                ('emissiveTexture','Emissive','sRGB')]:
            image = getattr(material, attr, None)
            if image is None: continue
            image = image.convert('RGBA' if label=='BaseColor' else 'RGB')
            filename = f'{prefix}-{label}.png'
            image.save(target/filename)
            maps[label] = filename
            textures.append(dict(file=filename, label=label, color_space=color_space,
                                 width=image.width, height=image.height))
            if label == 'MetallicRoughness':
                # glTF G = roughness, B = metal; deliver readable independent maps too.
                for channel, channel_name in [(1,'Roughness'),(2,'Metallic')]:
                    name=f'{prefix}-{channel_name}.png'
                    image.getchannel(channel).save(target/name)
                    maps[channel_name]=name
                    textures.append(dict(file=name,label=channel_name,color_space='Non-Color',width=image.width,height=image.height))
        uv_file = None
        if valid_uv:
            # Trimesh uses conventional bottom-left UVs; SVG/PNG images run downwards.
            faces = np.asarray(mesh.faces)
            if not ((uv >= -.0001).all() and (uv <= 1.0001).all()):
                uv_warning = 'Some UVs are outside 0–1; the layout preview is limited to the main tile.'
            else: uv_warning = None
            uv_file=f'{prefix}-UV.svg'
            lines=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><rect width="1024" height="1024" fill="#f6f5f0"/><g fill="none" stroke="#193e43" stroke-width="0.4">']
            preview=Image.new('RGB',(1024,1024),'#f6f5f0');draw=ImageDraw.Draw(preview)
            for face in faces:
                pts=[(float(uv[v,0])*1024,(1-float(uv[v,1]))*1024) for v in face]
                lines.append('<path d="M '+' L '.join(f'{x:.3f} {y:.3f}' for x,y in pts)+' Z"/>')
                draw.line(pts+[pts[0]],fill='#31585d',width=1)
            lines.append('</g></svg>')
            (target/uv_file).write_text('\n'.join(lines),encoding='utf-8')
            preview.save(target/f'{prefix}-UV.png')
        else: uv_warning='This mesh has no usable UV coordinates.'
        meshes.append(dict(name=geometry, node=node, vertices=len(mesh.vertices),triangles=len(mesh.faces),
                           has_uv=valid_uv,uv_layout=uv_file,uv_preview=f'{prefix}-UV.png' if valid_uv else None,
                           uv_warning=uv_warning,maps=maps,transform=np.asarray(transform).tolist()))
    manifest=dict(export_version=EXPORT_VERSION,source='model.glb',source_size=source.stat().st_size,source_mtime_ns=source.stat().st_mtime_ns,
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  coordinates='GLB Y up; metres; Blender glTF importer converts axes',
                  meshes=meshes,textures=textures,
                  has_uv=bool(meshes) and all(m['has_uv'] for m in meshes),
                  has_base_color=any('BaseColor' in m['maps'] for m in meshes),
                  has_pbr=any('MetallicRoughness' in m['maps'] for m in meshes),
                  normals_map_present=any('Normal' in m['maps'] for m in meshes))
    manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    (target/'LIRE-BLENDER.txt').write_text(
        'TRELLIS STUDIO - BLENDER\n\n'
        'Extract this archive. Open model.blend when provided, or import model.glb through File > Import > glTF 2.0.\n'
        'The GLB contains geometry, UVs, materials, skin weights and embedded animation clips when present.\n'
        'Separate PNG maps and UV guides are included for editing. Blender converts the glTF axes automatically.\n'
        'Base colour and emissive maps use sRGB; roughness, metallic, normal and occlusion maps use non-colour data.\n'
        'MetallicRoughness: G = roughness, B = metallic. Independent channel images are also supplied.\n'
        'A normal map is included only when it exists in the model; check manifest.json.\n'
        'Review scale, pivot, thin geometry, seams, foot contacts and clothing deformation before production use.\n'
        'This export does not create collision, LODs, gameplay behaviours or an animation controller.\n',encoding='utf-8')
    # Atomic output; an interrupted export never replaces a valid archive with a partial one.
    temp=folder/'Blender.zip.tmp'
    with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.write(source,'model.glb')
        if (folder/'model.blend').is_file():archive.write(folder/'model.blend','model.blend')
        for name in ('model.stl','print-report.json'):
            if (folder/name).is_file():archive.write(folder/name,name)
        generated={'manifest.json','LIRE-BLENDER.txt'}
        generated.update(t['file'] for t in textures)
        for mesh in meshes:
            generated.update(f for f in (mesh['uv_layout'],mesh['uv_preview']) if f)
        for name in sorted(generated):
            archive.write(target/name,'TrellisTextures/'+name)
        for name in ('settings.json','metadata.json','result.json'):
            if (folder/name).exists(): archive.write(folder/name,name)
    temp.replace(folder/'Blender.zip')
    return manifest
