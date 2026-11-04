"""Retarget authored Quaternius rotations, timing and contacts onto a fitted rig."""
from pathlib import Path
import bpy,math,itertools
from mathutils import Vector,Quaternion
from retarget_catalogue import BY_ID

ROOT=Path(__file__).resolve().parent

def source_map(pack):
    if pack=='UAL1':
        result=dict(root='DEF-hips',spine_1='DEF-spine.001',spine_2='DEF-spine.002',spine='DEF-spine.003',neck='DEF-neck',head='DEF-head')
        for side,suffix in [('left','L'),('right','R')]:
            for semantic,bone in [('clavicle','shoulder'),('arm','upper_arm'),('forearm','forearm'),('hand','hand'),('leg','thigh'),('shin','shin'),('foot','foot'),('toe','toe')]:result[side+'_'+semantic]='DEF-'+bone+'.'+suffix
    else:
        result=dict(root='pelvis',spine_1='spine_01',spine_2='spine_02',spine='spine_03',neck='neck_01',head='Head')
        for side,suffix in [('left','l'),('right','r')]:
            for semantic,bone in [('clavicle','clavicle'),('arm','upperarm'),('forearm','lowerarm'),('hand','hand'),('leg','thigh'),('shin','calf'),('foot','foot'),('toe','ball')]:result[side+'_'+semantic]=bone+'_'+suffix
    return result

def author(scene,target,mapping,requests,height):
    packs={};original_actions=set(bpy.data.actions);original_objects=set(bpy.data.objects)
    for request in requests:
        recipe=BY_ID.get(request['id'])
        if not recipe or recipe['source_pack'] in packs:continue
        before=set(bpy.data.actions);objects=set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(ROOT/recipe['source_model']))
        source=next(o for o in set(bpy.data.objects)-objects if o.type=='ARMATURE')
        actions={a.name:a for a in set(bpy.data.actions)-before}
        # glTF import may suffix an action already present from the target.
        for action in list(actions.values()):
            base=action.name.rsplit('.',1)[0] if action.name[-3:].isdigit() else action.name
            actions.setdefault(base,action)
        smap=source_map(recipe['source_pack'])
        for side in ('left','right'):
            if side+'_hand' not in mapping:continue
            target_hand=target.data.bones[mapping[side+'_hand']];source_hand=source.data.bones[smap[side+'_hand']]
            target_roots=list(target_hand.children);source_roots=list(source_hand.children)
            if len(target_roots)!=5 or len(source_roots)!=5:continue
            tpoints=[target.matrix_world@b.head_local-target.matrix_world@target_hand.head_local for b in target_roots]
            spoints=[source.matrix_world@b.head_local-source.matrix_world@source_hand.head_local for b in source_roots]
            tscale=max(sum(p.length for p in tpoints)/5,.001);sscale=max(sum(p.length for p in spoints)/5,.001)
            permutation=min(itertools.permutations(range(5)),key=lambda indices:sum((tpoints[i]/tscale-spoints[indices[i]]/sscale).length_squared for i in range(5)))
            for finger,index in enumerate(permutation):
                tb=target_roots[finger];sb=source_roots[index]
                for segment in range(3):
                    key=side+'_finger_'+str(finger)+'_'+str(segment);mapping[key]=tb.name;smap[key]=sb.name
                    if not tb.children or not sb.children:break
                    tb=max(tb.children,key=lambda b:b.length);sb=max(sb.children,key=lambda b:b.length)
        packs[recipe['source_pack']]=(source,actions,smap)
    target.animation_data_clear();target.animation_data_create()
    rest={b.name:(target.matrix_world@b.matrix_local).to_quaternion() for b in target.data.bones}
    rest_heads={b.name:target.matrix_world@b.head_local for b in target.data.bones}
    ordered=list(target.data.bones);ordered.sort(key=lambda b:len(b.parent_recursive))
    authored=[]
    def reset_pose():
        for p in target.pose.bones:p.rotation_mode='QUATERNION';p.rotation_quaternion=Quaternion();p.location=Vector();p.scale=Vector((1,1,1))
    def capture(source,smap,frame):
        scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame));bpy.context.view_layer.update()
        return {key:dict(rotation=(source.matrix_world@source.pose.bones[name].matrix).to_quaternion(),head=source.matrix_world@source.pose.bones[name].head) for key,name in smap.items() if name in source.pose.bones}
    def activate(source,action):
        source.animation_data_create();source.animation_data.action=action
        if action.slots:source.animation_data.action_slot=action.slots[0]
    for request in requests:
        recipe=BY_ID.get(request['id'])
        if not recipe:
            authored.append(dict(id=request['id'],name=request['id'],duration_s=request['duration'],source='Legacy animation preserved'))
            continue
        source,actions,smap=packs[recipe['source_pack']]
        src_rest={key:(source.matrix_world@source.data.bones[name].matrix_local).to_quaternion() for key,name in smap.items() if name in source.data.bones}
        src_heads={key:source.matrix_world@source.data.bones[name].head_local for key,name in smap.items() if name in source.data.bones}
        src_action=actions[recipe['source_clip']];activate(source,src_action)
        start,end=src_action.frame_range;source_duration=(end-start)/24;duration=request['duration'];frames=round(duration*24)
        cycles=max(1,round(duration/max(source_duration,.001))) if recipe['loop'] else 1
        action_name='TS_'+request['id']+'_'+str(duration)+'s'
        for existing in list(bpy.data.actions):
            if existing in original_actions and existing.name.startswith('TS_'+request['id']+'_'):bpy.data.actions.remove(existing);original_actions.discard(existing)
        action=bpy.data.actions.new(action_name);action.use_fake_user=True;target.animation_data.action=action
        # Leg length controls root displacement and ankle targets, not head size.
        ratios=[]
        for side in ('left','right'):
            ratios.append((rest_heads[mapping[side+'_leg']]-rest_heads[mapping[side+'_foot']]).length/(src_heads[side+'_leg']-src_heads[side+'_foot']).length)
        scale=sum(ratios)/len(ratios)
        for frame in range(frames+1):
            u=frame/frames;phase=u*cycles
            source_frame=start+(end-start)*(phase%1 if u<1 and recipe['loop'] else 1 if recipe['loop'] else u)
            activate(source,src_action);pose=capture(source,smap,source_frame)
            if recipe.get('composite')=='jump':
                if u<.3:name,progress='Jump_Start',u/.3
                elif u<.65:name,progress='Jump_Loop',(u-.3)/.35
                else:name,progress='Jump_Land',(u-.65)/.35
                act=actions[name];activate(source,act);a,b=act.frame_range;pose=capture(source,smap,a+(b-a)*progress)
            elif recipe.get('composite')=='crouch':
                act=actions['Idle_Loop'];activate(source,act);idle=capture(source,smap,act.frame_range[0])
                act=actions['Crouch_Idle_Loop'];activate(source,act);down=capture(source,smap,act.frame_range[0])
                weight=min(1,u/.3,(1-u)/.3);weight=weight*weight*(3-2*weight)
                pose={key:dict(rotation=idle[key]['rotation'].slerp(down[key]['rotation'],weight),head=idle[key]['head'].lerp(down[key]['head'],weight)) for key in idle if key in down}
            reset_pose();desired={};target_to_semantic={name:key for key,name in mapping.items()}
            for b in ordered:
                key=target_to_semantic.get(b.name)
                parent_rest=rest[b.parent.name] if b.parent else target.matrix_world.to_quaternion()
                parent_pose=desired[b.parent.name] if b.parent else target.matrix_world.to_quaternion()
                if key in pose and key in src_rest:
                    world=pose[key]['rotation']@src_rest[key].inverted()@rest[b.name]
                    if any(key.endswith('_'+part) for part in ('arm','forearm','hand','leg','shin')):
                        axis=world@Vector((0,1,0));source_axis=pose[key]['rotation']@Vector((0,1,0))
                        world=axis.rotation_difference(source_axis)@world
                else:world=parent_pose@parent_rest.inverted()@rest[b.name]
                desired[b.name]=world
                target.pose.bones[b.name].rotation_quaternion=rest[b.name].inverted()@parent_rest@parent_pose.inverted()@world
            root=target.pose.bones[mapping['root']]
            displacement=(pose['root']['head']-src_heads['root'])*scale
            if recipe['loop'] or request['id'] in ('crouch','jump'):displacement.x=0;displacement.y=0
            root.location=(target.matrix_world.to_3x3()@root.bone.matrix_local.to_3x3()).inverted()@displacement
            bpy.context.view_layer.update()
            # Two-bone leg solve retains source knee planes and scaled ankle lift.
            for side in ('left','right'):
                thigh=target.pose.bones[mapping[side+'_leg']];shin=target.pose.bones[mapping[side+'_shin']];foot=target.pose.bones[mapping[side+'_foot']]
                hip=target.matrix_world@thigh.head
                source_vector=pose[side+'_foot']['head']-pose[side+'_leg']['head']
                goal=hip+source_vector*scale
                goal.z=rest_heads[mapping[side+'_foot']].z+(pose[side+'_foot']['head'].z-src_heads[side+'_foot'].z)*scale
                l1=(rest_heads[mapping[side+'_shin']]-rest_heads[mapping[side+'_leg']]).length;l2=(rest_heads[mapping[side+'_foot']]-rest_heads[mapping[side+'_shin']]).length
                direction=goal-hip;distance=min(max(direction.length,.001),l1+l2-.00001);direction.normalize()
                bend=pose[side+'_shin']['head']-pose[side+'_leg']['head'];bend-=direction*bend.dot(direction)
                if bend.length<.001:bend=Vector((0,-1,0));bend-=direction*bend.dot(direction)
                bend.normalize();cosine=max(-1,min(1,(l1*l1+distance*distance-l2*l2)/(2*l1*distance)))
                knee=hip+direction*l1*cosine+bend*l1*math.sqrt(max(0,1-cosine*cosine))
                for bone,vector in ((thigh,knee-hip),(shin,goal-knee)):
                    current=desired[bone.name];axis=current@Vector((0,1,0));world=axis.rotation_difference(vector.normalized())@current
                    parent_rest=rest[bone.parent.name] if bone.parent else target.matrix_world.to_quaternion();parent_pose=desired[bone.parent.name] if bone.parent else target.matrix_world.to_quaternion()
                    bone.rotation_quaternion=rest[bone.name].inverted()@parent_rest@parent_pose.inverted()@world;desired[bone.name]=world
                # Keep the foot orientation from the authored clip after the leg solve.
                world=desired[foot.name];foot.rotation_quaternion=rest[foot.name].inverted()@rest[foot.parent.name]@desired[foot.parent.name].inverted()@world
            for name in set(mapping.values()):
                p=target.pose.bones[name];p.keyframe_insert('rotation_quaternion',frame=frame,group=p.name)
            root.keyframe_insert('location',frame=frame,group=root.name)
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        for key in curve.keyframe_points:key.interpolation='LINEAR'
        authored.append(dict(id=request['id'],name=recipe['name'],clip=action_name,duration_s=frames/24,source='Quaternius · CC0 · retarget',source_clip=recipe['source_clip'],cycles=cycles,foot_ik=True))
    # Source rigs and their actions are never exported onto the target.
    source_actions=set()
    for source,actions,_ in packs.values():source.animation_data_clear();source_actions.update(actions.values())
    for obj in set(bpy.data.objects)-original_objects:bpy.data.objects.remove(obj,do_unlink=True)
    for action in source_actions:
        if action.name in bpy.data.actions:bpy.data.actions.remove(action)
    scene.frame_set(0)
    return authored
