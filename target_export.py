"""Target recipes and PBR maps. Export staging never writes into an asset directory."""
from pathlib import Path
import copy,json,shutil,struct,subprocess,sys
import numpy as np
from PIL import Image
from glb_data import read,image,animation_info
from model_diagnostics import matching_report, source_sha256
from studio_import import find_blender

ROOT=Path(__file__).resolve().parent
TARGETS={
 'library':dict(label='Collection archive',units='original',up='original',forward='original',format='TRELLIS collection ZIP'),
 'blender':dict(label='Blender',units='metres',up='Z',forward='-Y',format='BLEND + GLB'),
 'unity':dict(label='Unity',units='metres',up='Y',forward='-Z',format='FBX + PBR maps',fbx_scale_mode='FBX_SCALE_UNITS'),
 'unreal':dict(label='Unreal Engine',units='centimetres',up='Z',forward='-Y',format='FBX + ORM maps',fbx_scale_mode='FBX_SCALE_NONE'),
 'print':dict(label='3D printing',units='millimetres',up='Z',forward='Y',format='STL + report'),
 'glb':dict(label='Portable GLB',units='metres',up='Y',forward='Z',format='GLB'),
}

def material_maps(data,binary,destination):
    target=destination/'Textures';target.mkdir(exist_ok=True);materials=[]
    def texture(info):return image(data,binary,data['textures'][info['index']]['source'])
    for i,entry in enumerate(data.get('materials',[])):
        prefix=f'TS_Mat_{i:03d}';pbr=entry.get('pbrMetallicRoughness',{});maps={}
        for key,label in [('baseColorTexture','BaseColor'),('metallicRoughnessTexture','MetallicRoughness'),('normalTexture','NormalOpenGL'),('occlusionTexture','Occlusion'),('emissiveTexture','Emissive')]:
            info=pbr.get(key) if key in ('baseColorTexture','metallicRoughnessTexture') else entry.get(key)
            if info is not None:
                img=texture(info);name=f'{prefix}_{label}.png';img.save(target/name);maps[label]='Textures/'+name
                if label=='NormalOpenGL':
                    pixels=np.asarray(img.convert('RGB')).copy();pixels[...,1]=255-pixels[...,1];name=f'{prefix}_NormalDirectX.png';Image.fromarray(pixels).save(target/name);maps['NormalDirectX']='Textures/'+name
        size=(4,4)
        if 'MetallicRoughness' in maps:size=Image.open(destination/maps['MetallicRoughness']).size
        mr=np.asarray(Image.open(destination/maps['MetallicRoughness']).convert('RGB')) if 'MetallicRoughness' in maps else np.full((size[1],size[0],3),255,dtype=np.uint8)
        rough=np.clip(mr[...,1].astype(float)*pbr.get('roughnessFactor',1),0,255).astype(np.uint8)
        metal=np.clip(mr[...,2].astype(float)*pbr.get('metallicFactor',1),0,255).astype(np.uint8)
        occ=np.asarray(Image.open(destination/maps['Occlusion']).convert('RGB').resize(size))[...,0] if 'Occlusion' in maps else np.full_like(rough,255)
        orm=np.stack([occ,rough,metal],axis=-1);name=f'{prefix}_ORM.png';Image.fromarray(orm).save(target/name);maps['ORM']='Textures/'+name
        packed=np.zeros((*rough.shape,4),dtype=np.uint8);packed[...,0]=metal;packed[...,3]=255-rough
        name=f'{prefix}_MetallicSmoothness.png';Image.fromarray(packed).save(target/name);maps['MetallicSmoothness']='Textures/'+name
        materials.append(dict(name=prefix,source_name=entry.get('name',f'Material {i+1}'),base_color=pbr.get('baseColorFactor',[1,1,1,1]),
            metallic_factor=pbr.get('metallicFactor',1),roughness_factor=pbr.get('roughnessFactor',1),emissive_factor=entry.get('emissiveFactor',[0,0,0]),
            normal_scale=entry.get('normalTexture',{}).get('scale',1),alpha_mode=entry.get('alphaMode','OPAQUE'),double_sided=entry.get('doubleSided',False),maps=maps))
    return materials

def conversion_glb(data,binary,path):
    clone=copy.deepcopy(data)
    for i,material in enumerate(clone.get('materials',[])):material['name']=f'TS_Mat_{i:03d}'
    text=json.dumps(clone,separators=(',',':')).encode();text+=b' '*((-len(text))%4);binary+=b'\0'*((-len(binary))%4)
    path.write_bytes(struct.pack('<4sII',b'glTF',2,28+len(text)+len(binary))+struct.pack('<II',len(text),0x4e4f534a)+text+struct.pack('<II',len(binary),0x004e4942)+binary)

def export_asset(source,destination,target,notify=lambda fraction,stage:None,*,preflight_budget=None,expected_hash=None):
    source=Path(source);destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    digest=source_sha256(source/'model.glb')
    if expected_hash is not None and digest != expected_hash:
        raise ValueError('A model changed after its export check. Run the check again for the current model.')
    preflight=None
    if preflight_budget is not None:
        from export_preflight import asset_report
        notify(.03,'Checking the export snapshot')
        preflight=asset_report(source,target,preflight_budget,digest=digest,staged_cache=source/'diagnostics.json')
    data,binary=read(source/'model.glb');settings=json.loads((source/'settings.json').read_text(encoding='utf-8'))
    metadata=json.loads((source/'metadata.json').read_text(encoding='utf-8'))
    recipe=TARGETS[target];clips=animation_info(data,binary)
    shutil.copy2(source/'model.glb',destination/'model.glb')
    notify(.1,'Extracting PBR maps')
    materials=material_maps(data,binary,destination) if target in ('blender','unity','unreal') else []
    manifest=dict(version=1,target=target,name=metadata.get('display_name',settings['name']),id=source.name,coordinates=recipe,
        source_glb_sha256=digest,height_m=settings.get('height_m'),animations=clips,
        bones=len({j for skin in data.get('skins',[]) for j in skin.get('joints',[])}),materials=materials)
    if preflight is not None:
        manifest['preflight']=preflight
    if target=='unity':
        manifest.update(unity_asset_id=source.name,unity_parent_id=settings.get('parent_id',''))
    if (source/'physics.json').is_file():
        manifest['physics']=json.loads((source/'physics.json').read_text(encoding='utf-8'))
        if (source/'Collisions').is_dir():shutil.copytree(source/'Collisions',destination/'Collisions',dirs_exist_ok=True)
    if (source/'motion-report.json').is_file():
        m=json.loads((source/'motion-report.json').read_text(encoding='utf-8'));manifest['motion_root']=m.get('root_bone','') if m.get('root_motion') else ''
    if target in ('blender','unity','unreal'):
        notify(.35,f'Converting geometry and {len(clips)} animation clips')
        conversion_glb(data,binary,destination/'.conversion.glb')
        (destination/'export-config.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        command=[str(find_blender()),'--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(ROOT/'export_blender.py'),'--',str(destination),target]
        subprocess.run(command,stdout=sys.stdout,stderr=subprocess.STDOUT,check=True,timeout=1200)
        (destination/'.conversion.glb').unlink();(destination/'export-config.json').unlink()
        manifest['conversion']=json.loads((destination/'conversion-report.json').read_text(encoding='utf-8'))
    if (source/'physics.json').is_file():
        manifest['physics']=json.loads((source/'physics.json').read_text(encoding='utf-8'));shutil.copy2(source/'physics.json',destination/'physics.json')
        if (source/'Collisions').is_dir():shutil.copytree(source/'Collisions',destination/'Collisions',dirs_exist_ok=True)
    if (source/'lod-manifest.json').is_file():
        manifest['lods']=json.loads((source/'lod-manifest.json').read_text(encoding='utf-8'))
        for level in manifest['lods']['levels'][1:]:
            temporary=destination/('.lod-'+str(level['level']));temporary.mkdir()
            shutil.copy2(source/'LODs'/level['file'],temporary/'model.glb')
            for file in ('settings.json','metadata.json'):shutil.copy2(source/file,temporary/file)
            level['export_path']='LODs/LOD'+str(level['level'])
            export_asset(temporary,destination/level['export_path'],target,lambda *_:None);shutil.rmtree(temporary)
    if (source/'motion-report.json').is_file():
        motion=json.loads((source/'motion-report.json').read_text(encoding='utf-8'));manifest['motion_root']=motion.get('root_bone','') if motion.get('root_motion') else '';shutil.copy2(source/'motion-report.json',destination/'motion-report.json')
    if target=='print':
        shutil.copy2(source/'model.stl',destination/'model.stl');shutil.copy2(source/'print-report.json',destination/'print-report.json')
        manifest['print_report']=json.loads((source/'print-report.json').read_text(encoding='utf-8'))
    notify(.85,'Writing scale, material and import information')
    for filename in ('settings.json','metadata.json'):
        if (source/filename).is_file():shutil.copy2(source/filename,destination/filename)
    diagnostic=(matching_report(source/'Diagnostics/report.json',digest,current=False)
                or matching_report(source/'diagnostics.json',digest,current=False))
    if diagnostic:
        (destination/'diagnostics.json').write_text(json.dumps(diagnostic,indent=2),encoding='utf-8')
    (destination/'studio-export.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    lines=[f'TRELLIS Studio — {recipe["label"]} export','',f'Units: {recipe["units"]}; up: {recipe["up"]}; forward: {recipe["forward"]}.',
      'The original GLB is included byte-for-byte. Its own coordinates are always Y up / metres.',
      f'Embedded source clips: {len(clips)}. Skeleton joints: {manifest["bones"]}.','']
    if target=='unity':
        lines+=['FIRST IMPORT: Copy this model folder into Assets/TrellisStudioExports. Copy the supplied Editor folder into Assets.',
          'Wait for compilation, then Tools > TRELLIS Studio > Configure exported models.',
          'UPDATE: Extract the new export outside the Unity project. Select the installed model folder in Unity, then open Tools > TRELLIS Studio > Preview model update.',
          'Choose the extracted studio-export.json, review the changes and select FBX, texture files and/or existing LOD and collision FBX files.',
          'Updates keep the installed paths, Unity .meta files, material assignments and prefab edits. The FBX contains both geometry and its embedded rig/clips.',
          'An update accepts the same Studio card or an explicitly selected related variant. Incompatible material slots or lost Unity references stop the update.',
          'The helper uses Generic animation, Convert Units and URP Lit (or Standard). It does not create a Humanoid Avatar or controller.',
          'MetallicSmoothness: R=metallic, A=1-roughness; linear. BaseColor/emissive are sRGB. NormalOpenGL is a normal map.']
    elif target=='unreal':
        lines+=['Import model.fbx with Convert Scene and Convert Scene Unit enabled. Use +X forward / Z up in the engine.',
          'For a rig: import the skeletal mesh and animations; reuse that skeleton when importing clips.',
          'Create materials from the supplied maps: ORM R=occlusion, G=roughness, B=metallic (linear). Use NormalDirectX with normal-map compression.',
          'BaseColor and Emissive are sRGB. Factor values and alpha modes are recorded in studio-export.json.',
          'Material graph creation and gameplay animation blueprints remain editor setup steps.']
    elif target=='blender':lines+=['Open model.blend. Geometry, skin, actions and textures are packed. Use the Action Editor to choose a clip.']
    elif target=='print':lines+=['The STL coordinates are millimetres. Review the topology report before slicing; wall thickness and supports still need checking.']
    lines+=['','Prepared LODs and collision proxies are included when present. Controllers, physical printing and artistic validation remain separate steps.']
    (destination/'README.txt').write_text('\n'.join(lines),encoding='utf-8')
    return manifest
