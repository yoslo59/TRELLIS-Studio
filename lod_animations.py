"""Keep prepared skinned LODs on the same animation set as their full-detail model."""
from pathlib import Path
import copy,json,struct
from glb_data import read

def sync(source,destination):
    data,binary=read(source);lod,old=read(destination)
    if not data.get('animations') and not lod.get('animations'):return
    by_name={}
    for i,node in enumerate(lod['nodes']):by_name.setdefault(node.get('name',''),[]).append(i)
    clips=copy.deepcopy(data.get('animations',[]));accessors={};views={};payload=bytearray(old)
    def view(index):
        if index not in views:
            original=data['bufferViews'][index];payload.extend(b'\0'*(-len(payload)%4));start=len(payload)
            offset=original.get('byteOffset',0);payload.extend(binary[offset:offset+original['byteLength']])
            result=copy.deepcopy(original);result.update(buffer=0,byteOffset=start)
            views[index]=len(lod.setdefault('bufferViews',[]));lod['bufferViews'].append(result)
        return views[index]
    def accessor(index):
        if index not in accessors:
            result=copy.deepcopy(data['accessors'][index])
            if 'sparse' in result:raise ValueError('Sparse clip data cannot be copied to prepared LODs.')
            result['bufferView']=view(result['bufferView']);accessors[index]=len(lod.setdefault('accessors',[]));lod['accessors'].append(result)
        return accessors[index]
    for clip in clips:
        for channel in clip['channels']:
            name=data['nodes'][channel['target']['node']].get('name','');matches=by_name.get(name,[])
            if len(matches)!=1:raise ValueError('Cannot match animation node in LOD: '+name)
            channel['target']['node']=matches[0]
        for sampler in clip['samplers']:
            for key in ('input','output'):sampler[key]=accessor(sampler[key])
    if clips:lod['animations']=clips
    else:lod.pop('animations',None)
    # Remove previous clip accessors and their now-unused buffers, so repeated
    # edits do not keep appending an entire animation set to every detail level.
    used=set()
    for mesh in lod.get('meshes',[]):
        for primitive in mesh['primitives']:
            used.update(primitive['attributes'].values())
            if 'indices' in primitive:used.add(primitive['indices'])
            for target in primitive.get('targets',[]):used.update(target.values())
    used.update(s['inverseBindMatrices'] for s in lod.get('skins',[]) if 'inverseBindMatrices' in s)
    for clip in clips:
        for sampler in clip['samplers']:used.update((sampler['input'],sampler['output']))
    remap={a:i for i,a in enumerate(sorted(used))};lod['accessors']=[lod['accessors'][a] for a in sorted(used)]
    for mesh in lod.get('meshes',[]):
        for primitive in mesh['primitives']:
            primitive['attributes']={k:remap[v] for k,v in primitive['attributes'].items()}
            if 'indices' in primitive:primitive['indices']=remap[primitive['indices']]
            for target in primitive.get('targets',[]):
                for k in target:target[k]=remap[target[k]]
    for skin in lod.get('skins',[]):
        if 'inverseBindMatrices' in skin:skin['inverseBindMatrices']=remap[skin['inverseBindMatrices']]
    for clip in clips:
        for sampler in clip['samplers']:
            for key in ('input','output'):sampler[key]=remap[sampler[key]]
    view_ids={a['bufferView'] for a in lod['accessors']};view_ids.update(i['bufferView'] for i in lod.get('images',[]) if 'bufferView' in i)
    remap_views={v:i for i,v in enumerate(sorted(view_ids))};packed=bytearray();packed_views=[]
    for index in sorted(view_ids):
        entry=copy.deepcopy(lod['bufferViews'][index]);packed.extend(b'\0'*(-len(packed)%4));offset=entry.get('byteOffset',0);start=len(packed);packed.extend(payload[offset:offset+entry['byteLength']]);entry.update(buffer=0,byteOffset=start);packed_views.append(entry)
    lod['bufferViews']=packed_views
    for item in lod['accessors']+lod.get('images',[]):
        if 'bufferView' in item:item['bufferView']=remap_views[item['bufferView']]
    lod['buffers']=[dict(byteLength=len(packed))];encoded=json.dumps(lod,separators=(',',':')).encode();encoded+=b' '*(-len(encoded)%4);packed.extend(b'\0'*(-len(packed)%4))
    raw=struct.pack('<4sII',b'glTF',2,28+len(encoded)+len(packed))+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(packed),0x004e4942)+packed
    Path(destination).write_bytes(raw)

def prepare(folder,stage):
    folder,stage=Path(folder),Path(stage);files=[]
    for source in sorted((folder/'LODs').glob('LOD[0-4].glb')):
        relative='LODs/'+source.name;target=stage/relative;target.parent.mkdir(exist_ok=True)
        if source.name=='LOD0.glb':target.write_bytes((stage/'model.glb').read_bytes())
        else:target.write_bytes(source.read_bytes());sync(stage/'model.glb',target)
        files.append(relative)
    return files
