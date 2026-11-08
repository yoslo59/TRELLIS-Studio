"""Validated frontal humanoid rig landmarks, independent of Blender and UniRig.

Coordinates are [horizontal / height, height above ground / height] before
offset_m. Positive horizontal is the character's anatomical left. They locate
joint centres in the front view; UniRig supplies each joint's depth.
"""
import math

LANDMARK_KEYS = ('chin', 'groin', 'left_shoulder', 'right_shoulder',
                 'left_elbow', 'right_elbow', 'left_wrist', 'right_wrist',
                 'left_knee', 'right_knee', 'left_ankle', 'right_ankle')


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):return False
    try:return math.isfinite(value)
    except OverflowError:return False


def validate_landmarks(value):
    if not isinstance(value, dict) or set(value) != set(LANDMARK_KEYS):
        raise ValueError('Place all 12 rig landmarks: chin, groin, shoulders, elbows, wrists, knees and ankles.')
    result = {}
    for key in LANDMARK_KEYS:
        point = value[key]
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or not all(_number(v) for v in point)
                or not -1 <= point[0] <= 1 or not 0 <= point[1] <= 1.05):
            raise ValueError('Rig landmarks must be finite [x, y] positions within the character setup area.')
        result[key] = [float(v) for v in point]
    chin, groin = result['chin'], result['groin']
    if abs(chin[0]) > .15 or abs(groin[0]) > .15 or chin[1] <= groin[1] + .12:
        raise ValueError('Place the chin and groin near the centre, with the chin above the torso.')
    for side, sign in (('left', 1), ('right', -1)):
        shoulder, elbow, wrist, knee, ankle = [result[side + '_' + part] for part in
                                               ('shoulder', 'elbow', 'wrist', 'knee', 'ankle')]
        if any(sign * p[0] <= 0 for p in (shoulder, elbow, wrist, knee, ankle)):
            raise ValueError('Keep left landmarks on the character\'s left (+X), and right landmarks on its right (-X).')
        if not chin[1] > shoulder[1] > groin[1] > knee[1] > ankle[1]:
            raise ValueError('Order the chin, shoulders, groin, knees and ankles from top to bottom.')
        if not sign * wrist[0] > sign * elbow[0] > sign * shoulder[0]:
            raise ValueError('Use an A-pose or T-pose: elbows outside shoulders and wrists outside elbows.')
        if max(elbow[1], wrist[1]) >= chin[1] or min(elbow[1], wrist[1]) <= ankle[1]:
            raise ValueError('Place elbows and wrists between the ankles and chin.')
        for a, b in ((shoulder, elbow), (elbow, wrist), (knee, ankle)):
            if math.dist(a, b) < .015:
                raise ValueError('Adjacent limb landmarks are too close together.')
    return result


def validate_rig_options(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid rig options.')
    rotation = value.get('rotation', [0, 0, 0])
    height = value.get('height_m', 1.7)
    offset = value.get('offset_m', 0)
    if (not isinstance(rotation, (list, tuple)) or len(rotation) != 3
            or any(not _number(v) or not -180 <= v <= 180 for v in rotation)
            or not _number(height) or not .2 <= height <= 3
            or not _number(offset) or not -1 <= offset <= 1):
        raise ValueError('Invalid rig rotation, height or offset.')
    result = dict(rotation=list(rotation), height_m=float(height), offset_m=float(offset))
    if 'landmarks' in value:
        result['landmarks'] = validate_landmarks(value['landmarks'])
    return result
