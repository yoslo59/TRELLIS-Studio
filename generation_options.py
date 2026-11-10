"""Validated generation controls shared by the API, worker and studio schema."""
import math

TRIANGLE_MIN = 1_000
TRIANGLE_MAX = 8_000_000

DEFAULT_PROFILE = 'game_d'
HUMANOID_EXPERIMENTAL = 'humanoid_experimental'
DENSE_MASTER = 'dense_master'
DENSE_MASTER_NAME = 'Dense Master'
DENSE_MASTER_TRIANGLES = 1_000_000
DENSE_MASTER_FINISHING = dict(mesh_mode='direct', pre_simplify=False, repair_holes=False)
GENERATION_PRESETS = {
    'game_d': (20, 28000, 2048), 'high_detail': (20, 250000, 2048),
    HUMANOID_EXPERIMENTAL: (20, 250000, 2048), DENSE_MASTER: (20, DENSE_MASTER_TRIANGLES, 2048),
    'fast': (8, 25000, 1024), 'balanced': (12, 50000, 2048), 'detail': (20, 100000, 2048),
}
DEFAULT_GENERATION = dict(preset=DEFAULT_PROFILE, resolution=1024, triangles=28000, texture_size=2048)

GROUPS = [
    dict(id='geometry',title='3D construction',hint='Zero steps uses the profile. Guidance strengthens the reference; excessive values can cause artifacts.'),
    dict(id='texture',title='Textures',hint='Material generation settings. A larger texture does not recreate missing geometry detail.'),
    dict(id='mesh',title='Mesh and simplification',hint='Simplification prioritises regions with lower shape error. Final triangle count is approximate.'),
    dict(id='uv',title='UV unwrapping',hint='Smaller angles create more UV islands. Review seams and stretching in Textures & UV.'),
]

def number(key,group,label,default,low,high,step=0.1,integer=False,hint=''):
    return dict(key=key,group=group,label=label,default=default,min=low,max=high,step=step,type='integer' if integer else 'number',hint=hint)

FIELDS=[]
for prefix,label,rescale,timing in [('structure','Structure',.7,5),('shape','Shape',.5,3)]:
    FIELDS += [
        number(prefix+'_steps','geometry',label+' · steps',0,0,50,1,True,'0 = profile step count.'),
        number(prefix+'_guidance','geometry',label+' · guidance',7.5,0,15,.1),
        number(prefix+'_rescale','geometry',label+' · rescale',rescale,0,1,.05,hint='Guidance rescale moderates excessive guidance.'),
        number(prefix+'_rescale_t','geometry',label+' · timestep distribution',timing,1,6,.1,hint='Changes how diffusion timesteps are distributed.'),
    ]
FIELDS += [
    number('texture_steps','texture','Texture · steps',0,0,50,1,True,'0 = profile step count.'),
    number('texture_guidance','texture','Texture · guidance',1,0,10,.1),
    number('texture_rescale','texture','Texture · rescale',0,0,1,.05),
    number('texture_rescale_t','texture','Texture · timestep distribution',3,1,6,.1),
    number('texture_seed','texture','Seed de texture',-1,-1,2147483647,1,True,'-1 continues the generation random sequence; a separate seed allows independent material comparisons.'),
    dict(key='mesh_mode',group='mesh',label='Mesh finishing method',type='choice',default='remesh',choices=[
        dict(value='remesh',label='Remesh and simplify'),dict(value='direct',label='Direct simplification')]),
    dict(key='pre_simplify',group='mesh',label='Pre-simplify dense meshes to 2M',type='boolean',default=True,
         hint='Disable to keep the raw surface for finishing, at a higher processing cost.'),
    dict(key='repair_holes',group='mesh',label='Fill small holes',type='boolean',default=False),
    number('remesh_project','mesh','Surface projection',0,0,1,.05,hint='0 = coque reconstruite ; 1 = retour maximal vers la surface source.'),
    number('remesh_band','mesh','Remesh band width',1,.5,3,.1),
    number('qem_edge_weight','mesh','QEM · edge length',.01,0,.1,.00001,hint='0 privilégie l’erreur de forme ; peut créer de grands triangles allongés sur les aplats.'),
    number('qem_skinny_weight','mesh','QEM · skinny triangles',.001,0,.1,.00001,hint='Pénalité supplémentaire contre les triangles très fins.'),
    number('uv_angle','uv','UV grouping angle',90,10,180,1,hint='En degrés ; pilote le regroupement des zones avant dépliage.'),
    number('uv_refine','uv','UV island refinement',0,0,10,1,True),
    number('uv_global','uv','Global passes',1,1,5,1,True),
    number('uv_smooth','uv','UV island smoothing',1,0,1,.1),
]
HELP={'structure_steps': 'Diffusion steps for the initial volume. Zero uses the profile. More steps increase computation without guaranteeing a better model.', 'structure_guidance': 'How strongly the initial volume follows the reference image. Excessive guidance can distort the result.', 'structure_rescale': 'Regulates the amplitude of volume guidance. Higher values can change detail while reducing excessive guidance.', 'structure_rescale_t': 'Changes the timing distribution of volume diffusion steps without increasing their count.', 'shape_steps': 'Steps per geometry generation pass. Zero uses the profile. More steps take longer.', 'shape_guidance': 'Reference strength for geometry and surface detail. Excessive guidance may produce bumps or artifacts.', 'shape_rescale': 'Regulates geometry guidance when its strength is high.', 'shape_rescale_t': 'Redistributes geometry diffusion steps over time; the step count remains unchanged.', 'texture_steps': 'Material generation steps. Zero uses the profile. More steps may add detail but take longer.', 'texture_guidance': 'Reference strength for colour and materials. Excessive guidance may amplify defects or contrast.', 'texture_rescale': 'Regulates material guidance when texture guidance is high.', 'texture_rescale_t': 'Redistributes texture diffusion steps over time without increasing their count.', 'texture_seed': 'Separate random seed for materials. Minus one continues the main random sequence; a fixed seed helps compare material settings.', 'mesh_mode': 'Remesh rebuilds a surface then simplifies it and may soften detail. Direct simplifies the generated surface while keeping more irregularities.', 'pre_simplify': 'First reduces dense surfaces towards two million triangles to limit processing cost. Disable to retain the raw surface for finishing.', 'repair_holes': 'Fills small openings before surface reconstruction. Enabled by profile D; it does not guarantee a closed mesh or preservation of every thin detail.', 'remesh_project': 'Projects the rebuilt surface towards the source. Zero keeps the reconstruction; one applies maximum projection. Used with remeshing only.', 'remesh_band': 'Width of the reconstruction band in voxels. This can affect thin parts and processing cost.', 'qem_edge_weight': 'Edge-length penalty during simplification. Higher values favour a more regular triangle distribution. Zero prioritises shape error and can create elongated triangles.', 'qem_skinny_weight': 'Penalises simplifications that produce very thin triangles while keeping the target triangle count.', 'uv_angle': 'Face grouping angle in degrees. Smaller values create more UV islands; larger values group more broadly and can affect stretching.', 'uv_refine': 'Iterations to refine UV groups before unwrapping. Zero disables refinement; higher values take longer and may change islands.', 'uv_global': 'Global grouping passes before UV unwrapping. More passes take longer and can reorganise islands.', 'uv_smooth': 'Smoothing strength for grouping faces into UV regions. Changes island boundaries without smoothing model geometry.'}
for field in FIELDS:field['hint']=HELP[field['key']]
ENGLISH_LABELS={'structure_steps': 'Structure · steps', 'structure_guidance': 'Structure · guidance', 'structure_rescale': 'Structure · rescale', 'structure_rescale_t': 'Structure · timestep distribution', 'shape_steps': 'Shape · steps', 'shape_guidance': 'Shape · guidance', 'shape_rescale': 'Shape · rescale', 'shape_rescale_t': 'Shape · timestep distribution', 'texture_steps': 'Texture · steps', 'texture_guidance': 'Texture · guidance', 'texture_rescale': 'Texture · rescale', 'texture_rescale_t': 'Texture · timestep distribution', 'texture_seed': 'Texture seed', 'mesh_mode': 'Finishing method', 'pre_simplify': 'Pre-simplify dense meshes to 2M', 'repair_holes': 'Fill small holes', 'remesh_project': 'Surface projection', 'remesh_band': 'Remesh band width', 'qem_edge_weight': 'QEM · edge length', 'qem_skinny_weight': 'QEM · skinny triangles', 'uv_angle': 'UV grouping angle', 'uv_refine': 'Island refinement', 'uv_global': 'Global passes', 'uv_smooth': 'Island smoothing'}
for field in FIELDS:field['label']=ENGLISH_LABELS[field['key']]
BY_KEY={f['key']:f for f in FIELDS}
# Keep historical fallback values in FIELDS so old job records are not reinterpreted.
# The new-game default is explicit both in submitted settings and the UI schema.
GAME_D_ADVANCED={f['key']:f['default'] for f in FIELDS}
GAME_D_ADVANCED.update(texture_guidance=2.0,repair_holes=True)
PROFILE_ADVANCED = {'game_d': GAME_D_ADVANCED, 'high_detail': {**GAME_D_ADVANCED, 'mesh_mode': 'direct'},
                    HUMANOID_EXPERIMENTAL: {**GAME_D_ADVANCED, 'mesh_mode': 'direct'},
                    DENSE_MASTER: {**GAME_D_ADVANCED, **DENSE_MASTER_FINISHING}}

def normalize_options(overrides=None,base_steps=12):
    if overrides is None:overrides={}
    if not isinstance(overrides,dict) or set(overrides)-set(BY_KEY):
        raise ValueError('Unknown or malformed advanced settings.')
    result={}
    for field in FIELDS:
        key=field['key'];value=overrides.get(key,field['default'])
        if field['type']=='boolean':
            if type(value) is not bool:raise ValueError(field['label']+' : expected true or false.')
        elif field['type']=='choice':
            if not isinstance(value,str) or value not in [c['value'] for c in field['choices']]:raise ValueError(field['label']+' : invalid selection.')
        else:
            if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(field['label']+' : expected a finite number.')
            if field['type']=='integer' and int(value)!=value:raise ValueError(field['label']+' : expected an integer.')
            if not field['min']<=value<=field['max']:raise ValueError(f"{field['label']} : enter a value between {field['min']} and {field['max']}.")
            value=int(value) if field['type']=='integer' else float(value)
            if key.endswith('_steps') and value==0:value=base_steps
        result[key]=value
    return result

def options_for_job(settings):
    """Read old laboratory settings without changing the original records."""
    base=int(settings.get('steps',GENERATION_PRESETS.get(settings.get('preset'),(12,))[0]))
    profile_base=PROFILE_ADVANCED.get(settings.get('preset'), {})
    if isinstance(settings.get('generation_options'),dict):
        return normalize_options({**profile_base,**settings['generation_options']},base)
    overrides={**profile_base,**{k:settings[k] for k in BY_KEY if k in settings}}
    if 'remesh' in settings:
        overrides['mesh_mode']='remesh' if settings['remesh'] else 'direct'
        overrides['pre_simplify']=bool(settings.get('predecimate',False))
    if isinstance(settings.get('qem_options'),dict):
        for source,target in [('lambda_edge_length','qem_edge_weight'),('lambda_skinny','qem_skinny_weight')]:
            if source in settings['qem_options']:overrides[target]=settings['qem_options'][source]
    return normalize_options(overrides,base)

def sampler_options(options):
    return {name:dict(steps=options[name+'_steps'],guidance_strength=options[name+'_guidance'],
        guidance_rescale=options[name+'_rescale'],rescale_t=options[name+'_rescale_t']) for name in ('structure','shape','texture')}

def export_options(options):
    return dict(remesh=options['mesh_mode']=='remesh',repair_holes=options['repair_holes'],remesh_band=options['remesh_band'],remesh_project=options['remesh_project'],
        mesh_cluster_threshold_cone_half_angle_rad=math.radians(options['uv_angle']),
        mesh_cluster_refine_iterations=options['uv_refine'],mesh_cluster_global_iterations=options['uv_global'],
        mesh_cluster_smooth_strength=options['uv_smooth'])

def configure_simplifier(cumesh,options):
    """One worker process per job; configure both pre-reduction and final QEM."""
    original=cumesh.CuMesh.simplify
    weights=dict(lambda_edge_length=options['qem_edge_weight'],lambda_skinny=options['qem_skinny_weight'])
    def simplify(self,target_num_faces,verbose=False,options=None):
        return original(self,target_num_faces,verbose=verbose,options={**(options or {}),**weights})
    cumesh.CuMesh.simplify=simplify

def schema():
    return dict(version=2,groups=GROUPS,fields=[dict(f,default=GAME_D_ADVANCED[f['key']]) for f in FIELDS],
        default_generation=DEFAULT_GENERATION,default_profile=DEFAULT_PROFILE,triangle_min=TRIANGLE_MIN,triangle_max=TRIANGLE_MAX,
        note='Experimental settings: compare geometry, materials and UVs. Settings alone do not guarantee quality.')
