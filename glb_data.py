"""Small read-only GLB helpers, shared by diagnostics, cards and exports."""
from functools import lru_cache
from math import isfinite
from pathlib import Path
import shutil
import base64,io,json,struct
import numpy as np
from PIL import Image
from studio_import import validate_glb

def read(path):
    data=validate_glb(Path(path));raw=Path(path).read_bytes();offset=12;binary=b''
    while offset+8<=len(raw):
        length,kind=struct.unpack_from('<II',raw,offset);offset+=8
        if offset+length>len(raw):raise ValueError('Truncated GLB chunk.')
        if kind==0x004e4942:binary=raw[offset:offset+length]
        offset+=length
    return data,binary


def remove_animation(source, destination, name, index=None):
    """Rewrite only the GLB descriptor and leave its binary payload intact."""
    data = validate_glb(source)
    animations = data.get('animations', [])
    if index is None:
        matches = [i for i, clip in enumerate(animations) if clip.get('name') == name]
        if len(matches) != 1:
            raise ValueError('Select one exact embedded animation.')
        index = matches[0]
    if type(index) is not int or not 0 <= index < len(animations) or animations[index].get('name') != name:
        raise ValueError('The selected animation has changed. Reload the model and try again.')
    remaining = [clip for i, clip in enumerate(animations) if i != index]
    if remaining:
        data['animations'] = remaining
    else:
        data.pop('animations', None)
    descriptor = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    descriptor += b' ' * (-len(descriptor) % 4)
    with source.open('rb') as incoming, destination.open('wb') as outgoing:
        original_length, json_length = struct.unpack('<II', incoming.read(16)[8:16])
        incoming.seek(20 + json_length)
        outgoing.write(struct.pack('<4sIIII', b'glTF', 2, original_length - json_length + len(descriptor),
                                   len(descriptor), 0x004e4f534a))
        outgoing.write(descriptor)
        shutil.copyfileobj(incoming, outgoing)
    return data

def image(data,binary,index):
    entry=data['images'][index]
    if 'bufferView' in entry:
        view=data['bufferViews'][entry['bufferView']];start=view.get('byteOffset',0);raw=binary[start:start+view['byteLength']]
    else:raw=base64.b64decode(entry['uri'].split(',',1)[1])
    result=Image.open(io.BytesIO(raw));result.load();return result

def accessor(data,binary,index):
    a=data['accessors'][index]
    if 'sparse' in a:raise ValueError('Sparse animation accessors are not supported by this diagnostic.')
    view=data['bufferViews'][a['bufferView']];dtype=np.dtype({5120:'i1',5121:'u1',5122:'<i2',5123:'<u2',5125:'<u4',5126:'<f4'}[a['componentType']])
    width={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[a['type']];stride=view.get('byteStride',width*dtype.itemsize)
    start=view.get('byteOffset',0)+a.get('byteOffset',0)
    result=np.ndarray((a['count'],width),dtype=dtype,buffer=binary,offset=start,strides=(stride,dtype.itemsize)).copy()
    if a.get('normalized') and dtype.kind in ('i','u'):
        result=np.maximum(result.astype(float)/np.iinfo(dtype).max,-1)
    return result

def static_placement(document, *, allow_identity=False):
    """Return the uniform scale and translation of one static scene placement group."""
    identities = {'matrix': np.eye(4).ravel().tolist(), 'translation': [0, 0, 0],
                  'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}
    nodes = document.get('nodes', [])

    def active_transforms(node):
        return {key for key in identities if key in node and not (allow_identity and node[key] == identities[key])}

    placed = [index for index, node in enumerate(nodes) if 'skin' in node or active_transforms(node)]
    if not placed:
        return None
    unsupported = 'Apply object transforms or use a single scene root with positive uniform scale and translation.'
    if len(placed) != 1:
        raise ValueError(unsupported)
    root = placed[0]
    group = nodes[root]
    scenes = document.get('scenes', [])
    scene = document.get('scene', 0)
    if (len(scenes) != 1 or type(scene) is not int or scene != 0 or scenes[0].get('nodes') != [root]
            or 'mesh' in group or 'skin' in group or active_transforms(group) - {'scale', 'translation'}):
        raise ValueError(unsupported)

    seen = set()
    pending = [root]
    while pending:
        index = pending.pop()
        if type(index) is not int or not 0 <= index < len(nodes) or index in seen:
            raise ValueError(unsupported)
        seen.add(index)
        children = nodes[index].get('children', [])
        if not isinstance(children, list):
            raise ValueError(unsupported)
        pending.extend(children)
    if not any('mesh' in nodes[index] for index in seen):
        raise ValueError(unsupported)

    scale = group.get('scale', identities['scale'])
    translation = group.get('translation', identities['translation'])
    for vector in (scale, translation):
        if (not isinstance(vector, list) or len(vector) != 3
                or any(type(value) not in (int, float) or not isfinite(value) for value in vector)):
            raise ValueError(unsupported)
    if scale[0] <= 0 or any(value != scale[0] for value in scale[1:]):
        raise ValueError(unsupported)
    return float(scale[0]), tuple(map(float, translation))


def placed_vertices(vertices, placement):
    if placement is None:
        return vertices
    scale, translation = placement
    world = vertices.astype(np.float64) * scale + translation
    if not np.isfinite(world).all() or np.abs(world).max() > 1e12:
        raise ValueError('The placement exceeds the supported coordinate range.')
    return world


def animation_info(data,binary):
    result=[]
    for index,clip in enumerate(data.get('animations',[])):
        inputs={s['input'] for s in clip.get('samplers',[])};times=[accessor(data,binary,i).ravel() for i in inputs]
        result.append(dict(name=clip.get('name',f'Animation {index+1}'),duration=round(max((float(t.max()) for t in times if len(t)),default=0),5),channels=len(clip.get('channels',[]))))
    return result

def posed_bounds(data,binary):
    """Evaluate stored glTF node transforms and linear skinning in world metres."""
    nodes=data.get('nodes',[]);parents={child:i for i,n in enumerate(nodes) for child in n.get('children',[])};world={}
    def matrix(i):
        if i in world:return world[i]
        n=nodes[i]
        if 'matrix' in n:m=np.asarray(n['matrix'],dtype=float).reshape(4,4).T
        else:
            x,y,z,w=n.get('rotation',[0,0,0,1]);m=np.eye(4)
            m[:3,:3]=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])@np.diag(n.get('scale',[1,1,1]));m[:3,3]=n.get('translation',[0,0,0])
        world[i]=matrix(parents[i])@m if i in parents else m;return world[i]
    low=np.full(3,np.inf);high=np.full(3,-np.inf)
    for i,node in enumerate(nodes):
        if 'mesh' not in node:continue
        transform=matrix(i);skin=data.get('skins',[])[node['skin']] if 'skin' in node else None
        if skin:
            inverse=accessor(data,binary,skin['inverseBindMatrices']).reshape(-1,4,4).transpose(0,2,1) if 'inverseBindMatrices' in skin else np.tile(np.eye(4),(len(skin['joints']),1,1))
            joints=np.stack([matrix(j) for j in skin['joints']])@inverse
        for primitive in data['meshes'][node['mesh']]['primitives']:
            attrs=primitive['attributes'];position=accessor(data,binary,attrs['POSITION']);homogeneous=np.column_stack([position,np.ones(len(position))])
            if skin and 'JOINTS_0' in attrs and 'WEIGHTS_0' in attrs:
                sets=sorted(int(k.split('_')[1]) for k in attrs if k.startswith('JOINTS_') and 'WEIGHTS_'+k.split('_')[1] in attrs)
                ids=np.concatenate([accessor(data,binary,attrs[f'JOINTS_{s}']).astype(int) for s in sets],axis=1)
                weights=np.concatenate([accessor(data,binary,attrs[f'WEIGHTS_{s}']).astype(float) for s in sets],axis=1)
                weights/=np.maximum(weights.sum(axis=1,keepdims=True),1e-12)
                transformed=np.einsum('nk,nkij,nj->ni',weights,joints[ids],homogeneous)[:,:3]
            else:transformed=(homogeneous@transform.T)[:,:3]
            if len(transformed):low=np.minimum(low,transformed.min(axis=0));high=np.maximum(high,transformed.max(axis=0))
    return low,high

@lru_cache(maxsize=512)
def _media(filename,mtime,size):
    try:
        data=validate_glb(Path(filename));names=[clip.get('name') for clip in data.get('animations',[])]
        return dict(available=True,animated=bool(names),clips=len(names),
                    bones=len({j for s in data.get('skins',[]) for j in s.get('joints',[])}),
                    animation_names=names, animation_revision=str(mtime),
                    animation_items=[dict(index=index,name=name) for index,name in enumerate(names)])
    except (OSError,ValueError,KeyError,TypeError):return dict(available=False,animated=False,clips=0,bones=0)

def media(path):
    try:stat=path.stat();return _media(str(path),stat.st_mtime_ns,stat.st_size)
    except OSError:return dict(available=False,animated=False,clips=0,bones=0)
