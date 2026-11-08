"""Constrained joint placement in Blender world space (Z up), without bpy.

Landmarks constrain only X/Z. A harmonic displacement along the bone hierarchy
keeps the intervening joints continuous and translates unanchored finger/foot
branches with their wrist/ankle. The predicted Y depth is never changed.
"""
import numpy as np


def skin_normalization_frame(vertices,joints):
    """Exact inverse parameters for UniRig's inference_skin_transform [-1,1]."""
    vertices,joints=np.asarray(vertices,float),np.asarray(joints,float)
    if (vertices.ndim!=2 or joints.ndim!=2 or vertices.shape[1]!=3 or joints.shape[1]!=3
            or not len(vertices) or not len(joints) or not np.isfinite(vertices).all() or not np.isfinite(joints).all()):
        raise ValueError('Invalid skin normalization geometry.')
    low=np.minimum(vertices.min(0),joints.min(0));high=np.maximum(vertices.max(0),joints.max(0))
    scale=float(np.max(high-low)/2)
    if scale<=0:raise ValueError('Invalid skin normalization bounds.')
    return (low+high)/2,scale


def landmark_anchors(names, mapping):
    index = {name: i for i, name in enumerate(names)}
    roles = {'chin': ('head', 0), 'groin': ('root', 0)}
    for side in ('left', 'right'):
        roles.update({side+'_shoulder': (side+'_arm', 0), side+'_elbow': (side+'_forearm', 0),
                      side+'_wrist': (side+'_forearm', 1), side+'_knee': (side+'_shin', 0),
                      side+'_ankle': (side+'_shin', 1)})
        if side+'_hand' in mapping: roles[side+'_wrist']=(side+'_hand',0)
        if side+'_foot' in mapping: roles[side+'_ankle']=(side+'_foot',0)
    if any(role not in mapping or mapping[role] not in index for role, _ in roles.values()):
        raise ValueError('The predicted skeleton cannot be mapped confidently to all 12 humanoid landmarks.')
    return {key: 2*index[mapping[role]]+endpoint for key,(role,endpoint) in roles.items()}


def place_joints(bones, parents, names, mapping, options):
    points=np.asarray(bones,float).reshape(-1,3)
    if not np.isfinite(points).all():raise ValueError('Non-finite predicted joints.')
    height=options['height_m'];offset=options['offset_m']
    anchors=landmark_anchors(names,mapping)
    # Merge only joint endpoints already coincident in the predicted skeleton.
    representatives=np.arange(len(points))
    def root(i):
        while representatives[i]!=i:
            representatives[i]=representatives[representatives[i]];i=representatives[i]
        return i
    for i in range(len(points)):
        for j in range(i):
            if np.linalg.norm(points[i]-points[j]) < height*1e-5:representatives[root(i)]=root(j)
    groups=np.array([root(i) for i in range(len(points))]);unique,groups=np.unique(groups,return_inverse=True)
    lap=np.zeros((len(unique),len(unique)),float)
    def edge(a,b):
        ga,gb=groups[a],groups[b]
        if ga==gb:return
        weight=1/max(np.linalg.norm(points[a]-points[b]),height*1e-4)
        lap[ga,ga]+=weight;lap[gb,gb]+=weight;lap[ga,gb]-=weight;lap[gb,ga]-=weight
    for i,parent in enumerate(parents):
        edge(2*i,2*i+1)
        if parent is not None:
            endpoint=min((2*int(parent),2*int(parent)+1),key=lambda p:np.linalg.norm(points[2*i]-points[p]))
            edge(2*i,endpoint)
    fixed={};targets={}
    for key,point_index in anchors.items():
        x,y=options['landmarks'][key];target=points[point_index].copy()
        target[0]=x*height;target[2]=y*height+offset
        delta=target-points[point_index]
        if np.linalg.norm(delta)>height*.4:
            raise ValueError('A landmark is too far from its predicted joint; check model orientation and placement.')
        group=int(groups[point_index])
        if group in fixed and not np.allclose(fixed[group],delta,atol=height*1e-5):
            raise ValueError('Two different landmarks map to the same predicted joint.')
        fixed[group]=delta;targets[key]=target
    known=np.array(sorted(fixed),int);unknown=np.array([i for i in range(len(unique)) if i not in fixed],int)
    displacement=np.zeros((len(unique),3));displacement[known]=np.array([fixed[i] for i in known])
    if len(unknown):
        try:displacement[unknown]=np.linalg.solve(lap[np.ix_(unknown,unknown)],-lap[np.ix_(unknown,known)]@displacement[known])
        except np.linalg.LinAlgError as error:raise ValueError('The predicted skeleton contains an unconnected joint branch.') from error
    displacement[:,1]=0
    adjusted=points+displacement[groups]
    lengths=np.linalg.norm(adjusted[1::2]-adjusted[::2],axis=1)
    if (lengths<height*1e-5).any():raise ValueError('Landmark placement collapses a bone; adjust the landmarks.')
    report=dict(coordinate_space='Blender world Z-up; landmark X/Z constrained, Y depth preserved',
                max_joint_displacement_m=float(np.linalg.norm(adjusted-points,axis=1).max()),
                max_depth_change_m=float(np.abs(adjusted[:,1]-points[:,1]).max()),
                anchors={key:dict(bone=names[index//2],endpoint='head' if index%2==0 else 'tail',
                                 before=points[index].tolist(),after=adjusted[index].tolist(),
                                 error_m=float(np.linalg.norm(adjusted[index]-targets[key])))
                         for key,index in anchors.items()})
    return adjusted.reshape(-1,6),report
