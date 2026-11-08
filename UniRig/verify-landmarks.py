"""Reject a rig if final mesh transfer failed to preserve requested joints."""
from pathlib import Path
import json,sys,os
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rig_joint_placement import landmark_anchors

job=Path(sys.argv[1]);work=job/'rig-work'
report=json.loads((work/'landmarks-report.json').read_text())
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(work/'rigged.glb'))
arms=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
if len(arms)!=1:raise ValueError('Final rig has no single humanoid skeleton to verify.')
arm=arms[0];bones=list(arm.data.bones);names=[b.name for b in bones]
points=np.array([list(arm.matrix_world@b.head_local)+list(arm.matrix_world@b.tail_local) for b in bones]).reshape(-1,3)
anchors=landmark_anchors(names,report['mapping'])
errors={key:float(np.linalg.norm(points[index]-np.asarray(report['anchors'][key]['after']))) for key,index in anchors.items()}
tolerance=max(.002,report['height_m']*.003)
report['final_rig_verification']=dict(errors_m=errors,tolerance_m=tolerance,passed=max(errors.values())<=tolerance)
(work/'landmarks-report.json').write_text(json.dumps(report,indent=2))
if not report['final_rig_verification']['passed']:
    raise ValueError('Final rig transfer did not preserve the requested landmarks. The rig was not accepted.')
print('Final rig preserves all requested joint landmarks',flush=True)
# The embedded bpy wheel can fail during interpreter teardown after importing
# a skinned glTF. All validation and report writes have completed at this point.
os._exit(0)
