"""Conservative geometric humanoid mapping shared by authored motion tools."""
from mathutils import Vector

def infer(arm,low,high):
    bones=list(arm.data.bones);height=high.z-low.z
    world={b.name:(arm.matrix_world@b.head_local,arm.matrix_world@b.tail_local) for b in bones}
    roots=[b for b in bones if not b.parent]
    mapping={};warning=''
    try:
        if len(roots)!=1: raise ValueError('Le squelette possède plusieurs racines.')
        root=roots[0];cx=world[root.name][0].x
        def limb(sign,kind):
            candidates=[]
            for b in bones:
                h,t=world[b.name];v=t-h
                if sign*(h.x-cx)<height*.025 or v.length<height*.065: continue
                children=[c for c in b.children if (world[c.name][1]-world[c.name][0]).length>height*.065]
                if not children: continue
                c=max(children,key=lambda c:(world[c.name][1]-world[c.name][0]).length)
                ch,ct=world[c.name]
                if kind=='leg':
                    if not (low.z+height*.32<h.z<low.z+height*.68 and v.z<-height*.14 and ct.z<ch.z-height*.13):continue
                    score=abs((h.z-low.z)/height-.54)+abs(h.x-cx)/height*.2
                else:
                    if not (low.z+height*.57<h.z<low.z+height*.9 and abs(h.x-cx)>height*.07 and sign*v.x>height*.065 and abs(v.z)<height*.23 and sign*(ct.x-ch.x)>height*.06):continue
                    score=abs(h.x-cx)/height+abs(v.z)/height
                candidates.append((score,b,c))
            if not candidates: raise ValueError('Membres humanoïdes non reconnus avec assez de confiance.')
            _,upper,lower=min(candidates,key=lambda x:x[0]);return upper,lower
        for label,sign in [('left',1),('right',-1)]:
            for kind in ('arm','leg'):
                upper,lower=limb(sign,kind)
                mapping[label+'_'+kind]=upper.name;mapping[label+'_'+('forearm' if kind=='arm' else 'shin')]=lower.name
            shin=arm.data.bones[mapping[label+'_shin']]
            if shin.children:mapping[label+'_foot']=max(shin.children,key=lambda b:b.length).name
        if len(set(mapping.values()))!=len(mapping): raise ValueError('Correspondance ambiguë entre les membres.')
        left_parent=arm.data.bones[mapping['left_leg']].parent
        right_parents=[arm.data.bones[mapping['right_leg']],*arm.data.bones[mapping['right_leg']].parent_recursive]
        while left_parent and left_parent not in right_parents:left_parent=left_parent.parent
        if left_parent:root=left_parent
        mapping['root']=root.name
        for side in ('left','right'):
            upper=arm.data.bones[mapping[side+'_arm']]
            if upper.parent and upper.parent.name!=root.name:mapping[side+'_clavicle']=upper.parent.name
            forearm=arm.data.bones[mapping[side+'_forearm']]
            if forearm.children:mapping[side+'_hand']=max(forearm.children,key=lambda b:b.length).name
            foot=arm.data.bones.get(mapping.get(side+'_foot',''))
            if foot and foot.children:mapping[side+'_toe']=max(foot.children,key=lambda b:b.length).name

        central=[b for b in bones if abs(world[b.name][0].x-cx)<height*.12 and world[b.name][0].z>world[root.name][0].z+height*.04]
        if central:
            mapping['spine']=min(central,key=lambda b:abs(world[b.name][0].z-(world[root.name][0].z+height*.14))).name
            mapping['head']=max(central,key=lambda b:world[b.name][0].z).name
            head=arm.data.bones[mapping['head']]
            chain=[b for b in reversed(head.parent_recursive) if b!=root and abs(world[b.name][0].x-cx)<height*.12]
            if len(chain)>=4:
                mapping['spine_1']=chain[0].name;mapping['spine_2']=chain[1].name;mapping['spine']=chain[2].name;mapping['neck']=chain[-1].name


    except ValueError as error:
        warning=str(error);mapping={}

    return mapping
