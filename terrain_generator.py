"""Deterministic local heightfields, native Unity Terrain export and GLB preview."""
from pathlib import Path
import json, shutil, time, zipfile
import numpy as np
from scipy.ndimage import gaussian_filter, zoom
from PIL import Image
import trimesh

ROOT = Path(__file__).resolve().parent

def make_heightfield(settings):
    n = settings['resolution']
    rng = np.random.default_rng(settings['seed'])
    yy, xx = np.mgrid[-1:1:complex(n),-1:1:complex(n)]
    heights = np.zeros((n,n),dtype=np.float64)
    amplitude = 1.0
    for cells in (4,8,16,32,64):
        values = rng.normal(size=(cells+1,cells+1))
        layer = zoom(values,(n/(cells+1),n/(cells+1)),order=3)[:n,:n]
        heights += amplitude*layer
        amplitude *= settings['roughness']
    heights = gaussian_filter(heights,.75)
    heights -= heights.min()
    heights /= max(heights.max(),1e-9)
    style = settings['terrain_style']
    radius = np.sqrt(xx**2+yy**2)
    if style == 'mountains':
        heights = (1-np.abs(2*heights-1))**1.7
    elif style == 'basin':
        heights = .6*np.clip(radius,0,1)+.4*heights
    elif style == 'plateau':
        heights = np.tanh((heights-.42)*5)*.35+.5
    flat = settings['flat_zone']*2
    if flat > 0:
        weight = np.clip((radius-flat)/max(flat*.6,.08),0,1)
        weight = weight*weight*(3-2*weight)
        target = float(np.median(heights[radius<flat]))
        heights = target*(1-weight)+heights*weight
    heights -= heights.min()
    heights /= max(heights.max(),1e-9)
    return heights.astype(np.float32)

def terrain_weights(heights, size_m, height_m):
    gz,gx = np.gradient(heights*height_m,size_m/(len(heights)-1))
    slope = np.sqrt(gx*gx+gz*gz)
    rock = np.clip((slope-.18)/.65,0,.95)
    grass = np.clip((1-rock)*(.6-heights)*1.35,0,.65)
    soil = 1-rock-grass
    return np.stack([soil,rock,grass],axis=-1)

def ground_textures(settings, palette):
    textures=[]
    for index in range(3):
        rng=np.random.default_rng(settings['seed']+index)
        noise=rng.normal(size=(256,256))
        grain=gaussian_filter(noise,.65,mode='wrap')
        stains=gaussian_filter(noise,12,mode='wrap')
        stains=stains/max(float(stains.std()),1e-6)
        bump=grain*.45+stains*.08
        if index==1:
            yy,xx=np.mgrid[0:256,0:256]/256
            nearest=np.full((256,256),np.inf);second=nearest.copy()
            for point in rng.random((48,2)):
                dx=np.abs(xx-point[0]);dy=np.abs(yy-point[1])
                distance=np.minimum(dx,1-dx)**2+np.minimum(dy,1-dy)**2
                lower=distance<nearest
                second=np.where(lower,nearest,np.minimum(second,distance))
                nearest=np.minimum(nearest,distance)
            bump += np.clip((np.sqrt(second)-np.sqrt(nearest))*28,0,1)*.65
        elif index==2:
            bump += gaussian_filter(noise,(.5,2.5),mode='wrap')*.35
        albedo=(palette[index]+grain[:,:,None]*13+stains[:,:,None]*5+(bump[:,:,None]-.3)*12).clip(0,255).astype(np.uint8)
        gy,gx=np.gradient(bump)
        normals=np.stack([-gx*4,gy*4,np.ones_like(gx)],axis=-1)
        normals/=np.linalg.norm(normals,axis=-1,keepdims=True)
        normal_rgb=np.round((normals*.5+.5)*255).clip(0,255).astype(np.uint8)
        textures.append((albedo,normal_rgb))
    return textures

def generate_terrain(folder, settings):
    folder = Path(folder)
    started = time.time()
    heights = make_heightfield(settings)
    raw = np.round(heights*65535).astype('<u2')
    raw.tofile(folder/'heightmap.raw')
    Image.fromarray(raw).save(folder/'heightmap.png')
    weights = terrain_weights(heights,settings['size_m'],settings['height_m'])
    palettes={'blackwater':[[103,88,64],[108,113,106],[66,84,47]],'dry':[[151,122,79],[139,128,107],[109,105,62]],'ash':[[85,82,76],[108,109,108],[65,74,55]]}
    palette=np.array(palettes[settings.get('terrain_palette','blackwater')],dtype=np.float32)
    textures=ground_textures(settings,palette)
    weights_preview=np.asarray(Image.fromarray(np.round(weights*255).astype(np.uint8)).resize((512,512),Image.Resampling.BILINEAR))/255
    zz,xx=np.mgrid[0:512,0:512]
    tx=(xx*settings['size_m']/512/6*256).astype(int)%256
    tz=(zz*settings['size_m']/512/6*256).astype(int)%256
    colors=sum(weights_preview[:,:,i,None]*textures[i][0][tz,tx] for i in range(3)).clip(0,255).astype(np.uint8)
    color_image=Image.fromarray(colors)
    color_image.save(folder/'input.png')
    # Fixed preview budget; the native heightmap preserves the full chosen resolution.
    step = max(1,(len(heights)-1)//128)
    preview = heights[::step,::step]
    n = len(preview)
    z,x = np.mgrid[0:n,0:n]
    vertices = np.stack([x*settings['size_m']/(n-1),preview*settings['height_m'],z*settings['size_m']/(n-1)],axis=-1).reshape(-1,3)
    a = (np.arange(n-1)[:,None]*n+np.arange(n-1)[None,:]).reshape(-1)
    faces = np.concatenate([np.stack([a,a+n,a+1],axis=-1),np.stack([a+1,a+n,a+n+1],axis=-1)])
    uv=np.stack([x/(n-1),1-z/(n-1)],axis=-1).reshape(-1,2)
    visual=trimesh.visual.TextureVisuals(uv=uv,material=trimesh.visual.material.PBRMaterial(baseColorTexture=color_image,metallicFactor=0,roughnessFactor=.95))
    mesh = trimesh.Trimesh(vertices,faces,process=False,visual=visual)
    mesh.export(folder/'model.glb')
    bundle = folder/'Unity'/'TRELLIS'
    target = bundle/'Terrains'/(settings['name']+'-'+folder.name[:8])
    target.mkdir(parents=True,exist_ok=True)
    shutil.copy2(folder/'heightmap.raw',target/'heightmap.raw')
    shutil.copy2(folder/'heightmap.png',target/'heightmap.png')
    Image.fromarray(np.round(weights*255).clip(0,255).astype(np.uint8)).save(target/'Splatmap.png')
    for index, name in enumerate(('Soil','Rock','Grass')):
        Image.fromarray(textures[index][0]).save(target/(name+'.png'))
        Image.fromarray(textures[index][1]).save(target/(name+'Normal.png'))
    manifest = dict(name=settings['name'],resolution=settings['resolution'],size_m=settings['size_m'],height_m=settings['height_m'],seed=settings['seed'],raw='heightmap.raw')
    (target/'terrain-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    editor = bundle/'Editor'
    editor.mkdir(exist_ok=True)
    shutil.copy2(ROOT/'unity/TrellisTerrainImport.cs',editor/'TrellisTerrainImport.cs')
    (bundle/'LIRE-TERRAIN.txt').write_text('Copier TRELLIS dans Assets, attendre la compilation, puis Outils > TRELLIS > Import les terrains Unity.\n'
        'TerrainData, trois TerrainLayers, materiau Terrain Lit et prefab avec TerrainCollider sont crees.\n'
        'RAW : 16 bits non signes little-endian, ordre des lignes z puis x.\n'
        'Le GLB est un apercu de 32 768 triangles, pas le TerrainData natif.\n'
        'Le relief est procedural, sans reconstruction depuis une photo, route, erosion hydraulique ou grotte.\n'
        'Les textures procedurales servent de base et peuvent etre remplacees dans les TerrainLayers.\n',encoding='utf-8')
    with zipfile.ZipFile(folder/'Unity.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in bundle.rglob('*'):
            if path.is_file(): archive.write(path,path.relative_to(bundle.parent))
    result = dict(name=settings['name'],terrain=True,triangles=len(faces),vertices=len(vertices),height_m=settings['height_m'],
                  dimensions_m=[settings['size_m'],settings['height_m'],settings['size_m']],resolution=settings['resolution'],
                  seconds=round(time.time()-started,2),height_min=float(heights.min()),height_max=float(heights.max()))
    (folder/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    (folder/'status.json').write_text(json.dumps(dict(state='done',stage='Terrain ready',progress=100,elapsed=result['seconds'],result=result),ensure_ascii=False),encoding='utf-8')
    return result
