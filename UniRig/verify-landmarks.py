"""Reject a rig if final mesh transfer failed to preserve requested joints."""
from pathlib import Path
import json,sys,os,traceback
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rig_joint_placement import landmark_anchors

def verify(work):
    path = work / 'landmarks-report.json'
    report = json.loads(path.read_text())
    verification = dict(passed=False, tolerance_m=max(.002, report['height_m'] * .003))
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(work / 'rigged.glb'))
        arms = [obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE']
        if len(arms) != 1:
            raise ValueError('Final rig has no single humanoid skeleton to verify.')
        arm = arms[0]
        bones = list(arm.data.bones)
        points = np.array([list(arm.matrix_world @ bone.head_local) +
                           list(arm.matrix_world @ bone.tail_local) for bone in bones]).reshape(-1, 3)
        anchors = landmark_anchors([bone.name for bone in bones], report['mapping'])
        errors = {key: float(np.linalg.norm(points[index] - np.asarray(report['anchors'][key]['after'])))
                  for key, index in anchors.items()}
        if not np.isfinite(list(errors.values())).all():
            raise ValueError('Final rig contains non-finite joint positions.')
        verification['errors_m'] = errors
        if max(errors.values()) > verification['tolerance_m']:
            raise ValueError('Final rig transfer did not preserve the requested landmarks. The rig was not accepted.')
        verification['passed'] = True
    except ValueError as error:
        verification['error'] = str(error)
    report['final_rig_verification'] = verification
    path.write_text(json.dumps(report, indent=2))
    if not verification['passed']:
        print(verification['error'], file=sys.stderr, flush=True)
        return 1
    print('Final rig preserves all requested joint landmarks', flush=True)
    return 0


if __name__ == '__main__':
    try:
        exit_code = verify(Path(sys.argv[1]) / 'rig-work')
    except Exception:
        traceback.print_exc()
        sys.stderr.flush()
        exit_code = 1
    # Embedded bpy can fault during interpreter teardown after importing a
    # skinned glTF. Return the actual validation status on either outcome.
    os._exit(exit_code)
