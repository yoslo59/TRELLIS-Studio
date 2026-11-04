"""Measured diagnostics. No mesh repair, acceptance certificate or source mutation."""
from pathlib import Path
import hashlib,json,time,uuid
import numpy as np
import trimesh
from glb_data import read,image,animation_info,posed_bounds,accessor

VERSION=3
def diagnose(folder,force=False):
    folder=Path(folder);source=folder/'model.glb';stat=source.stat();target=folder/'Diagnostics';cache=target/'report.json'
    if cache.is_file() and not force:
        old=json.loads(cache.read_text(encoding='utf-8'))
        if old.get('version')==VERSION and old.get('source_size')==stat.st_size and old.get('source_mtime_ns')==stat.st_mtime_ns:return old
    settings=json.loads((folder/'settings.json').read_text(encoding='utf-8')) if (folder/'settings.json').is_file() else {}
    data,binary=read(source);scene=trimesh.load(source,force='scene',process=False);issues=[];meshes=[]
    def issue(code,message,count=None,severity='warning'):
        entry=dict(code=code,message=message,severity=severity)
        if count is not None:entry['count']=int(count)
        issues.append(entry)
    for name,geometry in scene.geometry.items():
        mesh=geometry.copy();vertices=np.asarray(mesh.vertices);faces=np.asarray(mesh.faces)
        finite=bool(np.isfinite(vertices).all());extent=max(float(np.ptp(vertices,axis=0).max()),1e-6) if len(vertices) and finite else 1
        degenerate=int(np.count_nonzero(mesh.area_faces<=extent*extent*1e-14))
        loose=len(vertices)-len(np.unique(faces));uv=np.asarray(getattr(geometry.visual,'uv',[]),dtype=float)
        has_uv=uv.shape==(len(vertices),2) and bool(np.isfinite(uv).all());outside=0;uv_zero=0
        if has_uv:
            outside=int(np.count_nonzero(np.any((uv<-.0001)|(uv>1.0001),axis=1)))
            p=uv[faces];u=p[:,1]-p[:,0];v=p[:,2]-p[:,0];uv_zero=int(np.count_nonzero(np.abs(u[:,0]*v[:,1]-u[:,1]*v[:,0])<1e-12))
        mesh.merge_vertices(merge_tex=True,merge_norm=True,digits_vertex=8);mesh.remove_unreferenced_vertices()
        counts=np.bincount(mesh.edges_unique_inverse,minlength=len(mesh.edges_unique));boundary=int(np.count_nonzero(counts==1));nonmanifold=int(np.count_nonzero(counts>2))
        labels=trimesh.graph.connected_component_labels(mesh.edges_unique,node_count=len(mesh.vertices));shells=len(np.unique(labels))
        winding=bool(mesh.is_winding_consistent);closed=bool(len(mesh.faces) and not boundary and not nonmanifold and finite)
        volume=float(mesh.volume) if closed and finite else None
        normals=np.asarray(geometry.vertex_normals);bad_normals=int(np.count_nonzero(~np.isfinite(normals).all(axis=1)|(np.linalg.norm(normals,axis=1)<.5)))
        entry=dict(name=name,vertices=len(vertices),triangles=len(faces),welded_vertices=len(mesh.vertices),boundary_edges=boundary,
            nonmanifold_edges=nonmanifold,connected_shells=shells,degenerate_faces=degenerate,loose_vertices=loose,closed_surface=closed,
            winding_consistent=winding,volume_m3=volume,has_uv=has_uv,uv_outside_tile=outside,degenerate_uv_faces=uv_zero,bad_normals=bad_normals)
        meshes.append(entry)
        for code,label,count in [('boundary_edges','boundary edges',boundary),('nonmanifold_edges','nonmanifold edges',nonmanifold),('degenerate_faces','degenerate faces',degenerate),('loose_vertices','unused vertices',loose),('bad_normals','invalid normals',bad_normals)]:
            if count:issue(code,f'{name}: {count:,} {label}.',count)
        if not finite:issue('nonfinite_vertices',f'{name}: invalid vertex coordinates.',severity='error')
        if not has_uv:issue('missing_uv',f'{name}: no usable UV map.'+(' This is expected for a print copy.' if settings.get('asset_type')=='print' else ''),severity='info' if settings.get('asset_type')=='print' else 'warning')
        if outside:issue('uv_outside_tile',f'{name}: UVs extend beyond 0–1; check whether tiling is intentional.',outside,'info')
        if uv_zero:issue('degenerate_uv',f'{name}: UV faces with zero or near-zero area.',uv_zero)
        if not winding:issue('winding',f'{name}: face winding is inconsistent.')
        if volume is not None and volume<=0:issue('volume',f'{name}: non-positive signed volume; inspect surface orientation.')
        if shells>1:issue('shells',f'{name}: {shells:,} connected shells; inspect detached parts or cavities.',shells,'info')
    textures=[]
    for index,entry in enumerate(data.get('images',[])):
        try:
            img=image(data,binary,index);textures.append(dict(index=index,name=entry.get('name',f'Image {index+1}'),width=img.width,height=img.height,mode=img.mode))
        except Exception:issue('unreadable_texture',f'Image {index+1} cannot be decoded.',severity='error')
    materials=[]
    for index,entry in enumerate(data.get('materials',[])):
        pbr=entry.get('pbrMetallicRoughness',{});materials.append(dict(name=entry.get('name',f'Material {index+1}'),base_color='baseColorTexture' in pbr,
            metallic_roughness='metallicRoughnessTexture' in pbr,normal='normalTexture' in entry,occlusion='occlusionTexture' in entry,emissive='emissiveTexture' in entry,
            alpha_mode=entry.get('alphaMode','OPAQUE'),double_sided=entry.get('doubleSided',False)))
    clips=animation_info(data,binary)
    # Scene graph transforms include mesh node placement; glTF's vertical axis is Y.
    low,high=posed_bounds(data,binary);size=high-low
    stored_bad=0
    for mesh in data.get('meshes',[]):
        for primitive in mesh['primitives']:
            index=primitive['attributes'].get('NORMAL')
            if index is not None:
                n=accessor(data,binary,index);stored_bad+=int(np.count_nonzero(~np.isfinite(n).all(axis=1)|(np.linalg.norm(n,axis=1)<.5)))
    if stored_bad:issue('stored_normals','Invalid or near-zero normals in the GLB source.',stored_bad)
    summary=dict(vertices=sum(m['vertices'] for m in meshes),triangles=sum(m['triangles'] for m in meshes),meshes=len(meshes),instances=len(scene.graph.nodes_geometry),
        materials=len(materials),textures=len(textures),clips=len(clips),bones=len({j for s in data.get('skins',[]) for j in s.get('joints',[])}),
        dimensions_m=size.tolist(),height_m=float(size[1]),has_uv=bool(meshes) and all(m['has_uv'] for m in meshes),
        closed_surface=bool(meshes) and all(m['closed_surface'] for m in meshes),boundary_edges=sum(m['boundary_edges'] for m in meshes),nonmanifold_edges=sum(m['nonmanifold_edges'] for m in meshes),
        degenerate_faces=sum(m['degenerate_faces'] for m in meshes),connected_shells=sum(m['connected_shells'] for m in meshes),
        degenerate_uv_faces=sum(m['degenerate_uv_faces'] for m in meshes),invalid_stored_normals=stored_bad,dimension_pose='Stored GLB pose, with skinning evaluated')
    report=dict(version=VERSION,created=time.time(),source_size=stat.st_size,source_mtime_ns=stat.st_mtime_ns,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        summary=summary,meshes=meshes,textures=textures,materials=materials,animations=clips,issues=issues,
        status='attention' if any(i['severity'] in ('warning','error') for i in issues) else 'checked',
        scope='Topology uses positions welded to 8 decimal places in metres. Materials are measured as stored; missing optional maps are not automatically defects.',
        not_checked=['Self-intersections','UV overlaps','Minimum wall thickness','Morph target deformations','Rig fit and deformation quality','Visual quality','Gameplay readiness','Physical printability'])
    target.mkdir(exist_ok=True);temp=target/('report-'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8');temp.replace(cache)
    return report
