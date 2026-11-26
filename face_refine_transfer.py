"""Fit TOPOS surface relief to detected features on a textured frontal mesh."""
from pathlib import Path

import cv2
import numpy as np
from numba import njit
from scipy import ndimage, interpolate

from face_projection import _alignment, _find_face, _smooth
from multiview_texture_fusion import _raster


VIEW_SIZE = 512


def _topos_surface(path):
    # TOPOS predicts the coordinates on a fixed MetaHuman topology. These are
    # semantic template vertex IDs (zero-based), common to every prediction.
    positions, triangles = [], []
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if line.startswith('v '):
                positions.append([float(x) for x in line.split()[1:4]])
            elif line.startswith('f '):
                corners = [int(x.split('/')[0])-1 for x in line.split()[1:]]
                triangles.extend((corners[0], corners[j], corners[j+1]) for j in range(1, len(corners)-1))
    xyz = np.asarray(positions, dtype=np.float64)
    faces = np.asarray(triangles, dtype=np.int32)
    if (xyz.shape != (24049, 3) or not len(faces) or faces.min() < 0 or faces.max() >= len(xyz)
            or not np.isfinite(xyz).all()):
        raise ValueError('The reconstructed head does not match the installed TOPOS topology.')
    # Each eye centre is the midpoint of its outer and inner canthi.
    key = np.vstack((xyz[[13418, 13861]].mean(0), xyz[[9629, 7443]].mean(0),
                     xyz[2924], xyz[15042], xyz[22419]))
    return xyz, faces, key


def _screen(xyz, center, span, size):
    pixels_per_unit = (size-1)/span
    xy = (xyz[:, :2] - center) * [pixels_per_unit, -pixels_per_unit] + (size-1)*.5
    return np.column_stack((xy, xyz[:, 2])).astype('float32')


def _render_reference(xyz, faces, uv, atlas, center, span, size):
    screen = _screen(xyz, center, span, size)
    depth, coords = _raster(screen, faces, uv, (size, size), False)
    color = cv2.remap(atlas, (coords[..., 0] % 1)*atlas.shape[1]-.5, (coords[..., 1] % 1)*atlas.shape[0]-.5,
                      cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    color[~np.isfinite(depth)] = (48, 48, 48)
    return color, depth


def _face_view(xyz, faces, uv, atlas, detector_path):
    detector = cv2.FaceDetectorYN.create(str(detector_path), '', (320,320), .65, .3, 20)
    low, high = xyz[:, :2].min(axis=0), xyz[:, :2].max(axis=0)
    center = (low+high)*.5
    span = 1.05*max(high-low)
    if span < 1.e-7:
        raise ValueError('The character has no measurable frontal surface.')
    coarse_size = 800
    coarse, _ = _render_reference(xyz, faces, uv, atlas, center, span, coarse_size)
    box, _, _ = _find_face(detector, coarse, 'front of the textured model', coarse_size)
    center = center + (box[:2] + box[2:]*.5 - (coarse_size-1)*.5) * [1,-1] * span/(coarse_size-1)
    span = float(1.5*max(box[2:])*span/(coarse_size-1))
    # Reduce memory and keep adjacent faces for normal updates. The skin mask
    # remains inside the smaller detection frame, away from this outer margin.
    roi = (np.abs(xyz[:, :2]-center) < span*.72).all(axis=1)
    selected = np.flatnonzero(roi)
    local = np.full(len(xyz), -1, dtype=np.int32)
    local[selected] = np.arange(len(selected))
    nearby = local[faces[roi[faces].all(axis=1)]]
    if len(nearby) < 70:
        raise ValueError('The face has too few mesh triangles for a relief adjustment.')
    closeup, depth = _render_reference(xyz[selected], nearby, uv[selected], atlas, center, span, VIEW_SIZE)
    _, points, confidence = _find_face(detector, closeup, 'model close-up', VIEW_SIZE)
    return selected, nearby, center, span, depth, points, confidence


def _registered_depth(head_obj, points, span):
    xyz, faces, anchors = _topos_surface(head_obj)
    raw = anchors[:, :2] * [1., -1.]
    projected = xyz[:, :2] * [1., -1.]
    linear, offset, residual = _alignment(raw, points)
    coarse = raw @ linear + offset
    pixels = projected @ linear + offset
    # Correct modest feature offsets near the five observed landmarks while
    # holding the outer head fixed to its similarity fit.
    border = np.array([[0,0], [VIEW_SIZE,0], [0,VIEW_SIZE], [VIEW_SIZE,VIEW_SIZE]])
    anchors = np.concatenate((coarse, border))
    errors = np.concatenate((points-coarse, np.zeros((4,2))))
    warp = interpolate.RBFInterpolator(anchors, errors, kernel='thin_plate_spline', smoothing=4)
    pixels += warp(pixels)
    scale = np.linalg.norm(linear[0])*span/(VIEW_SIZE-1)
    screen = np.column_stack((pixels, xyz[:, 2]*scale)).astype('float32')
    depth, _ = _raster(screen, faces, np.empty((len(xyz),0), dtype='float32'),
                       (VIEW_SIZE,VIEW_SIZE), False)
    return depth, residual


@njit
def _lower_envelope(field, step):
    """Limit a steep local depth change to a bounded slope in screen space."""
    out = field.copy()
    diagonal = np.sqrt(2)*step
    h, w = field.shape
    for y in range(1,h-1):
        for x in range(1,w-1):
            out[y,x] = min(out[y,x],out[y,x-1]+step,out[y-1,x]+step,
                           out[y-1,x-1]+diagonal,out[y-1,x+1]+diagonal)
    for y in range(h-2,0,-1):
        for x in range(w-2,0,-1):
            out[y,x] = min(out[y,x],out[y,x+1]+step,out[y+1,x]+step,
                           out[y+1,x-1]+diagonal,out[y+1,x+1]+diagonal)
    return out


def _relief(donor, target, points, span):
    # Anatomical coordinates in interocular distances, oriented to the eye line.
    side = points[1]-points[0]
    eyes = np.linalg.norm(side)
    side /= eyes
    down = np.array([-side[1], side[0]])
    yy, xx = np.indices(target.shape)
    plane = np.dstack((xx, yy))-points[:2].mean(0)
    u, v = plane@side/eyes, plane@down/eyes
    valid = np.isfinite(donor) & np.isfinite(target)
    # Fit head pose and depth using skin around the features; deep openings,
    # the nose tip, ears and hair should not determine the alignment plane.
    footprint = ((u/.94)**2 + ((v-.48)/1.13)**2)
    fit = valid & (footprint < .84) & ((np.abs(u)>.29) | (v<-.1) | (v>1.12))
    if fit.sum() < 120:
        raise ValueError('Insufficient matching facial surface in the model and portrait.')
    design = np.dstack((np.ones_like(u),u,v))
    observed = np.zeros_like(target)
    np.subtract(target, donor, out=observed, where=valid)
    samples = design[fit]
    reference = observed[fit]
    subset = np.ones(len(reference), dtype=bool)
    for _ in range(3):
        beta = np.linalg.lstsq(samples[subset], reference[subset], rcond=None)[0]
        error = np.abs(reference-samples@beta)
        subset = error <= np.quantile(error,.80)
    delta = np.where(valid, design@beta - observed, 0.)
    # Bounded, smooth deformations keep the transition inside skin. Retain
    # eyelid interiors, the hairline and the entire boundary of the source.
    interocular = eyes*span/(VIEW_SIZE-1)
    limit = .19*interocular
    delta = limit*np.tanh(delta/limit)
    mask = _smooth(.0,.35,1.-footprint)*_smooth(-.56,.01,v)
    for eye in (-.5,.5):
        radius = np.sqrt(((u-eye)/.18)**2+(v/.075)**2)
        mask *= _smooth(.54,1.65,radius)
    mask *= _smooth(0., .075*eyes,ndimage.distance_transform_edt(valid))
    field = ndimage.gaussian_filter(delta*mask, max(1.3,eyes*.027))
    step = .68*span/(VIEW_SIZE-1)
    field = .5*(_lower_envelope(field,step)-_lower_envelope(-field,step))
    field *= (mask>0)
    return field, interocular, valid & (footprint<.45)


def _no_fold_shifts(original, faces, dz):
    # Each triangle's oriented area varies linearly when only Z changes.
    # Project the three displacements onto that area constraint. The lookup
    # groups coincident UV-seam vertices before making these corrections.
    unique, inverse = np.unique(original, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    moved = dz!=0
    welded = inverse[faces[moved[faces].any(axis=1)]]
    shifts = np.zeros(len(unique))
    shifts[inverse] = dz
    coords = unique[welded].astype('float64')
    u, v = coords[:,1]-coords[:,0], coords[:,2]-coords[:,0]
    normal = np.cross(u, v)
    area2 = np.einsum('ij,ij->i', normal, normal)
    cb = -normal[:,0]*v[:,1] + normal[:,1]*v[:,0]
    cc = normal[:,0]*u[:,1] - normal[:,1]*u[:,0]
    coeff = np.column_stack((-cb-cc,cb,cc))
    movable = coeff*(shifts[welded]!=0)
    for _ in range(96):
        change = np.einsum('ij,ij->i',coeff,shifts[welded])
        bad = np.flatnonzero((area2>0) & (area2+change < .11*area2))
        if not len(bad):
            break
        for j in bad:
            gradient = movable[j]
            denom = gradient@gradient
            if denom>0:
                current = coeff[j]@shifts[welded[j]]
                adjustment = max(0., -.82*area2[j]-current)/denom
                shifts[welded[j]] += adjustment*gradient
    # A remaining sliver reduces the strength globally instead of producing
    # a flipped triangle. Evaluate once more after float32 coordinate rounding.
    out = shifts[inverse]
    for _ in range(5):
        revised = (unique[:,2]+shifts).astype('float32').astype('float64') - unique[:,2]
        change = np.einsum('ij,ij->i',coeff,revised[welded])
        if np.all(area2+change >= .04*area2):
            return out
        risk = change < -area2*.9
        if not risk.any():
            break
        shifts *= min(.97, float(np.min(.87*area2[risk]/-change[risk])))
        out = shifts[inverse]
    raise ValueError('The face has very thin triangles. The relief could not be fitted safely.')


def _shading_delta(original, revised, normals, faces, moved):
    from normal_glb import unit
    _, group = np.unique(original, axis=0, return_inverse=True)
    group = group.reshape(-1)
    changed = faces[moved[faces].any(axis=1)]
    touched = np.unique(group[changed])
    change_normals = np.isin(group,touched)
    neighbors = faces[change_normals[faces].any(axis=1)]
    weld_tri = group[neighbors]
    def geometric(vertices):
        triangles = vertices[neighbors].astype('float64')
        directions = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
        summed = np.zeros((group.max()+1,3))
        for corner in range(3):
            np.add.at(summed,weld_tri[:,corner],directions)
        n = summed[group[change_normals]]
        return n/np.maximum(np.linalg.norm(n,axis=1,keepdims=True),1.e-30)
    before, after = geometric(original),geometric(revised)
    axis = np.cross(before,after)
    cosine = (before*after).sum(axis=1,keepdims=True)
    source = normals[change_normals]
    rotated = source + np.cross(axis,source) + np.cross(axis,np.cross(axis,source))/np.maximum(1.e-6,1+cosine)
    return change_normals, (unit(rotated)-source).astype('float32')


def fit_relief(xyz, normals, uv, faces, atlas, head_obj, detector_path):
    selected, nearby, center, span, original_depth, points, confidence = _face_view(
        xyz, faces, uv, atlas, detector_path)
    donor_depth, alignment_error = _registered_depth(head_obj, points, span)
    field, eye_span, skin = _relief(donor_depth, original_depth, points, span)
    original = xyz[selected]
    screen = _screen(original, center, span, VIEW_SIZE)
    shift = ndimage.map_coordinates(field,screen[:,1::-1].T,order=1,mode='constant')
    # Use the depth of the visible skin to keep the back of the head, hair,
    # collars and other geometry behind the face untouched.
    front = float(np.median(original_depth[skin]))
    shift *= _smooth(front-1.17*eye_span, front-.46*eye_span, original[:,2])
    shift[np.abs(shift)<eye_span*4.e-6] = 0
    if np.count_nonzero(shift)<30:
        raise ValueError('Too little facial geometry was available for this refinement.')
    shift = _no_fold_shifts(original,nearby,shift)
    revised = original.copy()
    revised[:,2] += shift
    moved = revised[:,2]!=original[:,2]
    count = int(moved.sum())
    if count<30 or np.abs(shift).max()>.25*eye_span:
        raise ValueError('A reliable match to the model face could not be established.')
    include, shading = _shading_delta(original,revised,normals[selected],nearby,moved)
    info = dict(face_center=[float(center[0]),float(center[1]),front], face_span=span,
                moved_vertices=count, landmark_confidence=round(confidence,4), alignment_error=alignment_error)
    return selected[include], (revised[include,2]-original[include,2]), shading, info
