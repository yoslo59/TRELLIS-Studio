"""LOD, pivot/collider and sampled humanoid motion preparation in factory Blender."""
from pathlib import Path
import bpy,json,math,sys,re
from mathutils import Vector,Quaternion,Matrix

JOB=Path(sys.argv[sys.argv.index('--')+1]);request=json.loads((JOB/'work-request.json').read_text(encoding='utf-8'));options=request['options'];operation=request['operation']
bpy.ops.wm.read_factory_settings(use_empty=True);bpy.context.preferences.filepaths.use_scripts_auto_execute=False
bpy.ops.import_scene.gltf(filepath=str(JOB/'source.glb'));scene=bpy.context.scene;arms=[o for o in scene.objects if o.type=='ARMATURE']
shapes={p.custom_shape for a in arms for p in a.pose.bones if p.custom_shape}
for shape in shapes:bpy.data.objects.remove(shape,do_unlink=True)
meshes=[o for o in scene.objects if o.type=='MESH'];roots=[o for o in scene.objects if o.parent is None]
for obj in scene.objects:
    if obj.animation_data:
        obj.animation_data.action=None
        for track in obj.animation_data.nla_tracks:track.mute=True
for arm in arms:
    for bone in arm.pose.bones:bone.rotation_mode='QUATERNION';bone.matrix_basis=Matrix.Identity(4)
for action in bpy.data.actions:action.use_fake_user=True
bpy.context.view_layer.update();scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
def bounds():
    points=[o.matrix_world@Vector(c) for o in meshes for c in o.bound_box];return Vector([min(v[i] for v in points) for i in range(3)]),Vector([max(v[i] for v in points) for i in range(3)])
def triangles():return sum(len(p.vertices)-2 for o in meshes for p in o.data.polygons)
def export(path,animations=True):
    bpy.ops.object.select_all(action='DESELECT')
    for o in scene.objects:
        if o.type in {'MESH','ARMATURE','EMPTY'}:o.select_set(True)
    args=dict(filepath=str(path),export_format='GLB',use_selection=True,export_animations=animations,export_skins=True,export_animation_mode='ACTIONS',export_force_sampling=True,export_frame_range=False,export_anim_single_armature=True,export_yup=True,export_extras=True)
    supported=bpy.ops.export_scene.gltf.get_rna_type().properties.keys();bpy.ops.export_scene.gltf(**{k:v for k,v in args.items() if k in supported})
def activate(arm,action):
    arm.animation_data_create();arm.animation_data.action=action
    if action.slots:arm.animation_data.action_slot=next((s for s in action.slots if s.target_id_type=='OBJECT'),action.slots[0])
def curves_linear(action):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    for point in curve.keyframe_points:point.interpolation='LINEAR'

lo,hi=bounds();report=dict(height_m=hi.z-lo.z,triangles=triangles(),bones=sum(len(a.data.bones) for a in arms))
if operation=='lod':
    folder=JOB/'LODs';folder.mkdir(exist_ok=True);levels=[];original=triangles()
    export(folder/'LOD0.glb');levels.append(dict(level=0,file='LOD0.glb',ratio=1,triangles=original))
    originals={o:o.data.copy() for o in meshes}
    for index,ratio in enumerate(options['ratios'],1):
        for o in meshes:
            o.data=originals[o].copy();bpy.context.view_layer.objects.active=o
            modifier=o.modifiers.new('Studio LOD','DECIMATE');modifier.ratio=max(ratio,min(1,4/max(1,sum(len(p.vertices)-2 for p in o.data.polygons))));modifier.use_collapse_triangulate=True
            # Collapse before the armature modifier so the skin remains deformable.
            while list(o.modifiers).index(modifier)>0:bpy.ops.object.modifier_move_up(modifier=modifier.name)
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        export(folder/f'LOD{index}.glb');levels.append(dict(level=index,file=f'LOD{index}.glb',ratio=ratio,triangles=triangles()))
    for o in meshes:o.data=originals[o]
    (JOB/'lod-manifest.json').write_text(json.dumps(dict(version=1,levels=levels,screen_heights=[.6,.3,.12,.04][:len(levels)],source_triangles=original),indent=2))
    export(JOB/'model.glb');report['lods']=levels
elif operation=='game':
    root=bpy.data.objects.new('StudioAsset',None);scene.collection.objects.link(root)
    for o in roots:world=o.matrix_world.copy();o.parent=root;o.matrix_world=world
    from mathutils import Euler
    convert=Matrix.Rotation(math.pi/2,4,'X');root.matrix_world=convert@Euler([math.radians(v) for v in options['rotation']],'XYZ').to_matrix().to_4x4()@convert.inverted();root.scale=(options['scale'],)*3;bpy.context.view_layer.update();lo,hi=bounds();center=(lo+hi)*.5
    pivot=options['pivot'];offset=Vector(options['offset']);offset=Vector((offset.x,-offset.z,offset.y))
    shift=Vector((0,0,0)) if pivot=='source' else -Vector((center.x,center.y,lo.z if pivot=='ground' else center.z))
    root.location+=shift+offset;bpy.context.view_layer.update();lo,hi=bounds()
    physics=dict(version=1,kind=options['collider'],pivot=pivot,rotation_degrees=options['rotation'],scale=options['scale'],offset_m=options['offset'],units='metres',up='Y')
    if options['collider']!='none':
        bpy.ops.object.select_all(action='DESELECT')
        folder=JOB/'Collisions';folder.mkdir(exist_ok=True);collection=bpy.data.collections.new('Collision');scene.collection.children.link(collection)
        if options['collider']=='box':
            bpy.ops.mesh.primitive_cube_add(size=1,location=(lo+hi)*.5);collider=bpy.context.object;collider.scale=(hi-lo)
        else:
            deps=bpy.context.evaluated_depsgraph_get();vertices=[]
            for o in meshes:
                evaluated=o.evaluated_get(deps);m=evaluated.to_mesh();vertices.extend(o.matrix_world@v.co for v in m.vertices);evaluated.to_mesh_clear()
            data=bpy.data.meshes.new('Collision hull');data.from_pydata(vertices,[],[]);data.update();collider=bpy.data.objects.new('Collision',data);scene.collection.objects.link(collider);bpy.context.view_layer.objects.active=collider;collider.select_set(True)
            bpy.ops.object.mode_set(mode='EDIT');bpy.ops.mesh.select_all(action='SELECT');bpy.ops.mesh.convex_hull();bpy.ops.object.mode_set(mode='OBJECT')
            if len(collider.data.polygons)>200:
                modifier=collider.modifiers.new('Hull budget','DECIMATE');modifier.ratio=180/max(1,len(collider.data.polygons));bpy.ops.object.modifier_apply(modifier=modifier.name)
        collider.name='UCX_StudioAsset_00';collider['studio_collider']=True
        bpy.ops.object.select_all(action='DESELECT');collider.select_set(True);bpy.context.view_layer.objects.active=collider;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
        bpy.ops.export_scene.gltf(filepath=str(folder/'collision.glb'),export_format='GLB',use_selection=True,export_animations=False,export_yup=True,export_extras=True)
        physics.update(file='Collisions/collision.glb',center_m=[(lo.x+hi.x)/2,(lo.z+hi.z)/2,-(lo.y+hi.y)/2],size_m=[hi.x-lo.x,hi.z-lo.z,hi.y-lo.y]);bpy.data.objects.remove(collider,do_unlink=True)
    (JOB/'physics.json').write_text(json.dumps(physics,indent=2));export(JOB/'model.glb');report.update(height_m=hi.z-lo.z,physics=physics)
elif operation=='motion':
    if not arms:raise ValueError('This model has no skeleton.')
    arm=arms[0];old=next((a for a in bpy.data.actions if a.name==options['clip']),None)
    if old is None:raise ValueError('Animation clip not found.')
    sys.path.insert(0,str(Path(__file__).parent))
    from humanoid_mapping import infer
    mapping=request.get('result',{}).get('humanoid_mapping') or infer(arm,lo,hi);report['humanoid_mapping']=mapping;root_name=mapping.get('root') or next(b.name for b in arm.data.bones if b.parent is None)
    fps=scene.render.fps/scene.render.fps_base;start,end=old.frame_range;first=start+(end-start)*options['start'];last=start+(end-start)*options['end'];frames=round(options['duration']*fps);samples=[];feet={side:[] for side in ('left','right') if mapping.get(side+'_foot','') in arm.pose.bones}
    for frame in range(frames+1):
        activate(arm,old);t=first+(last-first)*frame/frames;scene.frame_set(math.floor(t),subframe=t%1);bpy.context.view_layer.update()
        samples.append({p.name:(p.location.copy(),p.rotation_quaternion.copy(),p.scale.copy()) for p in arm.pose.bones})
        for side in feet:feet[side].append(arm.matrix_world@arm.pose.bones[mapping[side+'_foot']].head)
    # Estimated support phases are visible in the report; they are not proof of contact.
    contacts={};height=max(report['height_m'],.01)
    for side,points in feet.items():
        floor=min(p.z for p in points);mask=[p.z<=floor+height*.025 for p in points];distance=sum((points[i]-points[i-1]).to_2d().length for i in range(1,len(points)) if mask[i] and mask[i-1]);contacts[side]=dict(contact_ratio=sum(mask)/len(mask),travel_during_contact_m=distance,positions=[[p.x,p.z,-p.y] for p in points],contact=mask)
    renamed=re.sub(r'_[0-9.]+s$', '_'+str(float(options['duration']))+'s',old.name) if old.name.startswith('TS_') else old.name;old.name='__Studio_previous_clip';action=bpy.data.actions.new(renamed);action.use_fake_user=True;activate(arm,action)
    first_root=samples[0][root_name][0];basis=arm.matrix_world.to_3x3()@arm.data.bones[root_name].matrix_local.to_3x3();initial_world=basis@first_root
    for frame,sample in enumerate(samples):
        u=frame/frames
        for p in arm.pose.bones:
            location,rotation,scale=sample[p.name];location=location.copy();rotation=rotation.copy();scale=scale.copy()
            if options['loop'] and options['blend']>0 and u>1-options['blend']:
                w=(u-1+options['blend'])/options['blend'];w=w*w*(3-2*w);a,b,c=samples[0][p.name];location=location.lerp(a,w);rotation=rotation.slerp(b,w);scale=scale.lerp(c,w)
            if p.name==root_name:
                world=basis@location
                if options['mode']=='in_place':world.x=initial_world.x;world.y=initial_world.y
                else:world.y-=options['distance']*u
                location=basis.inverted()@world
            p.location=location;p.rotation_mode='QUATERNION';p.rotation_quaternion=rotation;p.scale=scale
            for channel in ('location','rotation_quaternion','scale'):p.keyframe_insert(channel,frame=frame,group=p.name)
    curves_linear(action);bpy.data.actions.remove(old)
    contacts={}
    for side in feet:
        points=[]
        for frame in range(frames+1):
            scene.frame_set(frame);bpy.context.view_layer.update();points.append(arm.matrix_world@arm.pose.bones[mapping[side+'_foot']].head)
        floor=min(p.z for p in points);mask=[p.z<=floor+height*.025 for p in points];distance=sum((points[i]-points[i-1]).to_2d().length for i in range(1,len(points)) if mask[i] and mask[i-1]);contacts[side]=dict(contact_ratio=sum(mask)/len(mask),travel_during_contact_m=distance,positions=[[p.x,p.z,-p.y] for p in points],contact=mask)
    motion=dict(version=1,clip=renamed,options=options,root_bone=root_name,contacts=contacts,assessment='Estimated foot support from ankle height. Travel is expected during in-place locomotion; inspect against the ground and intended speed.',root_motion=options['mode']=='root_motion')
    (JOB/'motion-report.json').write_text(json.dumps(motion,indent=2));report['motion_report']=motion
    for obj in scene.objects:
        if obj.animation_data:obj.animation_data.action=None
    for a in arms:
        for p in a.pose.bones:p.matrix_basis=Matrix.Identity(4)
    export(JOB/'model.glb')
else:raise ValueError('Unsupported preparation.')
bpy.ops.file.pack_all();bpy.context.preferences.filepaths.save_version=0;bpy.ops.wm.save_as_mainfile(filepath=str(JOB/'model.blend'))
if operation=='game':
    affine=root.matrix_world.copy()
    if (JOB/'motion-report.json').is_file():
        motion=json.loads((JOB/'motion-report.json').read_text())
        for foot in motion.get('contacts',{}).values():
            result=[]
            for point in foot['positions']:
                p=affine@Vector((point[0],-point[2],point[1]));result.append([p.x,p.z,-p.y])
            foot['positions']=result
        (JOB/'motion-report.json').write_text(json.dumps(motion))
    if (JOB/'lod-manifest.json').is_file():
        import shutil
        lods=json.loads((JOB/'lod-manifest.json').read_text());shutil.copy2(JOB/'model.glb',JOB/'LODs/LOD0.glb')
        for level in lods['levels'][1:]:
            bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=str(JOB/'LODs'/level['file']));scene=bpy.context.scene
            imported=[o for o in scene.objects if o.parent is None];anchor=bpy.data.objects.new('StudioAsset',None);scene.collection.objects.link(anchor)
            for obj in imported:obj.parent=anchor
            anchor.matrix_world=affine;bpy.context.view_layer.update();export(JOB/'LODs'/level['file'])
    report['lods']=json.loads((JOB/'lod-manifest.json').read_text())['levels'] if (JOB/'lod-manifest.json').is_file() else None
(JOB/'work-result.json').write_text(json.dumps(report,indent=2))
