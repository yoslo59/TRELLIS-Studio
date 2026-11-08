"""Coordinate, depth and descendant regression tests; no model inference."""
from pathlib import Path
import sys,copy,json,subprocess
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from rig_landmarks import validate_rig_options,validate_landmarks
from rig_joint_placement import place_joints,skin_normalization_frame


def fixture():
    names=[];bones=[];parents=[];mapping={}
    def add(name,head,tail,parent=None,role=None):
        names.append(name);bones.append([*head,*tail]);parents.append(names.index(parent) if parent else None)
        if role:mapping[role]=name
    add('Pelvis',(0,0,.95),(0,0,1.1),role='root')
    add('Spine',(0,0,1.1),(0,0,1.3),'Pelvis','spine')
    add('Chest',(0,0,1.3),(0,0,1.48),'Spine')
    add('Neck',(0,0,1.48),(0,0,1.6),'Chest','neck')
    add('Head',(0,0,1.6),(0,0,1.82),'Neck','head')
    for side,sign in [('left',1),('right',-1)]:
        shoulder=(sign*.18,.01,1.45);elbow=(sign*.45,.025,1.42);wrist=(sign*.7,.04,1.39)
        add(side+'Clavicle',(0,0,1.45),shoulder,'Chest',side+'_clavicle')
        add(side+'Arm',shoulder,elbow,side+'Clavicle',side+'_arm')
        add(side+'Forearm',elbow,wrist,side+'Arm',side+'_forearm')
        add(side+'Hand',wrist,(sign*.79,.04,1.39),side+'Forearm',side+'_hand')
        add(side+'Finger',(sign*.79,.04,1.39),(sign*.85,.04,1.39),side+'Hand')
        hip=(sign*.1,0,.95);knee=(sign*.105,-.03,.55);ankle=(sign*.11,.01,.2)
        add(side+'Thigh',hip,knee,'Pelvis',side+'_leg')
        add(side+'Shin',knee,ankle,side+'Thigh',side+'_shin')
        add(side+'Foot',ankle,(sign*.11,-.13,.14),side+'Shin',side+'_foot')
    marks={'chin':[0,.87],'groin':[0,.51]}
    for side,sign in [('left',1),('right',-1)]:
        for name,x,y in [('shoulder',.1,.8),('elbow',.24,.77),('wrist',.37,.75),('knee',.055,.28),('ankle',.055,.055)]:
            marks[side+'_'+name]=[sign*x,y]
    options=validate_rig_options(dict(rotation=[0,0,0],height_m=1.8,offset_m=.1,landmarks=marks))
    return np.asarray(bones,float),parents,names,mapping,options


def main():
    from api_contract import Rig
    ui=json.loads(subprocess.check_output(['node','--input-type=module','-e',
        "import {initialRigLandmarks} from './web/rig-setup-viewer.js'; console.log(JSON.stringify(initialRigLandmarks()));"],cwd=ROOT,text=True))
    validate_landmarks(ui)
    bones,parents,names,mapping,options=fixture()
    after,report=place_joints(bones,parents,names,mapping,options)
    assert report['max_depth_change_m']==0
    assert all(v['error_m']<1e-10 for v in report['anchors'].values())
    assert np.array_equal(after[:,[1,4]],bones[:,[1,4]])
    for side in ('left','right'):
        finger=names.index(side+'Finger');hand=names.index(side+'Hand')
        assert np.allclose(after[finger]-bones[finger],np.tile(after[hand,:3]-bones[hand,:3],2))
        assert np.allclose(after[finger,3:]-after[finger,:3],bones[finger,3:]-bones[finger,:3])
    # Height and offset are applied once in world units, not normalized FBX units.
    for key,anchor in report['anchors'].items():
        x,y=options['landmarks'][key]
        assert np.allclose(np.array(anchor['after'])[[0,2]],[x*1.8,y*1.8+.1])
    for mutate in [lambda m:m.pop('chin'),lambda m:m['chin'].__setitem__(0,float('nan')),
                   lambda m:m['left_wrist'].__setitem__(0,-.4),lambda m:m['left_knee'].__setitem__(1,.8),
                   lambda m:m['right_elbow'].__setitem__(0,True)]:
        bad=copy.deepcopy(options['landmarks']);mutate(bad)
        try:validate_landmarks(bad)
        except ValueError:pass
        else:raise AssertionError('Invalid landmarks accepted')
        try:Rig(landmarks=bad)
        except ValueError:pass
        else:raise AssertionError('REST/MCP contract accepted invalid landmarks')
    assert 'landmarks' not in validate_rig_options({})
    assert 'landmarks' not in Rig().model_dump(exclude_none=True)
    assert Rig(**options).model_dump()['landmarks']==options['landmarks']
    # Captured real-rig regression: finger joints beyond mesh bounds shift the
    # skin frame by 0.00781265 normalized units (6.21 mm on the 1.59 m model).
    v=np.array([[-.9555633,-.2969794,-1],[.9555633,.29697946,1]])
    j=np.array([[-.9610848,-.17578052,-.99140143],[.9767101,.02734391,.7672955]])
    centre,scale=skin_normalization_frame(v,j)
    assert abs(centre[0]-.00781265)<1e-8 and scale==1
    assert np.allclose(((j-centre)/scale)*scale+centre,j)
    assert not np.allclose((j-centre)/scale,j)
    # Also recover scale if a predicted joint extends the longest mesh axis.
    centre,scale=skin_normalization_frame(v,np.r_[j,[[0,0,1.4]]])
    assert np.isclose(scale,1.2) and np.allclose(((v-centre)/scale)*scale+centre,v)
    print('PASS: 12 anchors, metre scale/offset, unchanged depth, rigid descendant fingers, validation, legacy contract')


if __name__=='__main__':main()
