"""Use UniRig's own merge alignment for skeleton previews and joint placement."""
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent/'source'))
from src.inference.merge import denormalize_vertices,get_correct_orientation_kdtree


def mesh_points(scene):
    return np.array([list(o.matrix_world@v.co) for o in scene.objects if o.type=='MESH' for v in o.data.vertices])


def align(vertices,target,bones):
    # Four affine probes recover the exact scale/translation/permutation selected
    # upstream, so corrected world joints can return to the normalized FBX.
    probes=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],float)
    stacked=np.r_[np.asarray(bones,float),np.c_[probes,probes]]
    vertices,stacked=denormalize_vertices(target,vertices,stacked)
    np.random.seed(12345)
    vertices,stacked=get_correct_orientation_kdtree(vertices,target,stacked)
    p=stacked[-4:,:3];matrix=np.eye(4);matrix[:3,3]=p[0];matrix[:3,:3]=(p[1:]-p[0]).T
    if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:3,:3]))<1e-10:
        raise ValueError('Cannot align the predicted skeleton to the reference mesh.')
    return stacked[:-4],matrix
