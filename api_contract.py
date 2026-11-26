"""Versioned agent API contracts. No model engines or GPU libraries are imported."""
from typing import Literal,Annotated
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator

from studio_version import VERSION
from generation_options import (GENERATION_PRESETS, HI3D_FINISHING_METHODS, TRIANGLE_MIN, TRIANGLE_MAX,
                                validate_hi3d_overrides)
from retarget_catalogue import ANIMATIONS
Identifier=Annotated[str,Field(pattern=r'^[a-f0-9]{32}$')]

class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

Coordinate = Annotated[float, Field(ge=0, le=1)]
ViewRole = Literal['front', 'left', 'back', 'right']
SingleImageEngine = Literal['trellis2', 'pixal3d_single']
ReferenceEngine = Literal['trellis2', 'pixal3d_single', 'pixal3d']
MasterFinishing = Literal['studio_768', 'wtivo_1024', 'hi3d_512', 'hi3d_1024']
CreateMasterFinishing = MasterFinishing | Literal['none']
TextureMethod = Literal['trellis', 'multiview', 'ultratex']


class ReferenceStroke(Contract):
    mode: Literal['keep', 'erase']
    radius: float = Field(ge=.001, le=.5)
    points: list[tuple[Coordinate, Coordinate]] = Field(min_length=1, max_length=128)


class ReferenceEdit(Contract):
    crop: tuple[Coordinate, Coordinate, Coordinate, Coordinate] | None = None
    strokes: list[ReferenceStroke] = Field(default_factory=list, max_length=48)


class ReferenceUploads(Contract):
    upload_id: Identifier = Field(description='Front-view upload ID.')
    left_upload_id: Identifier | None = None
    back_upload_id: Identifier | None = None
    right_upload_id: Identifier | None = None
    reference_edits: dict[ViewRole, ReferenceEdit] | None = None


class ReferencePreview(ReferenceUploads):
    engine: ReferenceEngine = 'trellis2'
    remove_bg: bool = True


class ReferencePreviewView(Contract):
    role: ViewRole
    width: int
    height: int
    original: str
    cutout: str
    framed: str


class ReferencePreviewResult(Contract):
    engine: ReferenceEngine
    views: list[ReferencePreviewView]


class Generation(Contract):
    upload_id:Identifier
    name:str=Field(default='New model',min_length=1,max_length=80)
    profile_id:str=Field(default='game_d',max_length=80,description='Built-in or custom profile ID from GET /profiles.')
    height_m:float=Field(default=1,ge=.01,le=100)
    seed:int=Field(default=42,ge=0,le=2147483647)
    priority:int=Field(default=0,ge=-2,le=2)
    engine: SingleImageEngine = 'trellis2'
    master_finishing: CreateMasterFinishing | None = None
    reproject_face: bool = False
    retexture_after_refinement: bool | None = None
    texture_method: TextureMethod | None = None
    multiview_texture_refinement: bool | None = Field(default=None, strict=True,
                                                      description='Apply palette and multiview refinement to the baked texture.')
    overrides:dict=Field(default_factory=dict,examples=[{}],description='Optional recipe overrides: triangles, texture_size, resolution, remove_bg, advanced_settings, asset_type, reference_prompt, parent_id, keep_finishing_source, repair_surface or hd_reconstruction (Dense Master only).')
    reference_edits: dict[Literal['front'], ReferenceEdit] | None = None

class MultiViewGeneration(ReferenceUploads):
    profile_id: str = Field(default='character_pixal', max_length=80,
                            description='Built-in or custom profile ID. character_pixal is supported for existing callers.')
    side_angle: Literal[40, 90] = 40
    name: str = Field(default='Character', min_length=1, max_length=80)
    height_m: float = Field(default=1.7, ge=.01, le=100)
    seed: int = Field(default=42, ge=0, le=2147483647)
    triangles: int | None = Field(default=None, ge=TRIANGLE_MIN, le=TRIANGLE_MAX)
    texture_size: Literal[1024, 2048] | None = None
    asset_type: str = Field(default='humanoid', max_length=80)
    master_finishing: CreateMasterFinishing | None = None
    texture_method: TextureMethod | None = None
    parent_id: Identifier | None = None
    reference_prompt: str = Field(default='', max_length=6000)
    keep_finishing_source: bool | None = None
    repair_surface: bool = False
    hd_reconstruction: bool = Field(default=False, strict=True, description='Reconstruct the Dense Master at grid 768. Mutually exclusive with repair_surface.')
    reproject_face: bool = False
    priority: int = Field(default=0, ge=-2, le=2)

    @field_validator('profile_id')
    @classmethod
    def validate_profile_id(cls, value):
        if value != 'character_pixal' and value not in GENERATION_PRESETS and not value.startswith('user_'):
            raise ValueError('Choose a built-in or custom generation profile ID.')
        return value


class ImportModel(Contract):
    upload_id:Identifier
    name:str=Field(default='',max_length=80)

class Metadata(Contract):
    display_name:str|None=Field(default=None,min_length=1,max_length=80)
    category:str|None=Field(default=None,max_length=40)
    asset_type:str|None=None
    tags:list[str]|None=None
    notes:str|None=Field(default=None,max_length=2000)
    review_state:Literal['review','retouch','retained','rejected']|None=None
    favorite:bool|None=None

class LOD(Contract):
    ratios:list[float]=Field(default_factory=lambda:[.5,.25,.125],min_length=1,max_length=4)
    @field_validator('ratios')
    @classmethod
    def decreasing(cls,value):
        if any(not .02<=r<1 for r in value) or value!=sorted(set(value),reverse=True):raise ValueError('Choose distinct decreasing ratios from .02 to .99.')
        return value

class Game(Contract):
    pivot:Literal['source','ground','center']='ground'
    collider:Literal['none','box','convex']='box'
    rotation:tuple[float,float,float]=(0,0,0)
    offset:tuple[float,float,float]=(0,0,0)
    scale:float=Field(default=1,ge=.001,le=100)

class Motion(Contract):
    clip:str=Field(min_length=1,max_length=160)
    start:float=Field(default=0,ge=0,lt=1)
    end:float=Field(default=1,gt=0,le=1)
    duration:float=Field(default=2.5,ge=2,le=5)
    loop:bool=False
    blend:float=Field(default=.15,ge=0,le=.4)
    mode:Literal['in_place','root_motion']='in_place'
    distance:float=Field(default=0,ge=-10,le=10,description='Extra forward metres added to existing root travel. Use zero to retain authored travel.')

class Animation(Contract):
    clip:str=Field(min_length=1,max_length=80,description='Library motion ID, not an embedded clip name.')
    duration:float=Field(default=2.5,ge=2,le=5,multiple_of=.5)

class GeneratedAnimationCreate(Contract):
    name: str = Field(default='New animation', min_length=1, max_length=80)
    prompt: str = Field(min_length=3, max_length=1000)
    duration: float = Field(default=5, ge=2, le=10, multiple_of=.5)
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)

    @field_validator('name', 'prompt')
    @classmethod
    def visible_text(cls, value, info):
        value = value.strip()
        allowed = '\n\r\t' if info.field_name=='prompt' else ''
        if not value or any(ord(char) < 32 and char not in allowed for char in value):
            raise ValueError('Use visible text without control characters.')
        if info.field_name=='prompt':
            value=' '.join(value.split())
            if len(value)<3:raise ValueError('Describe the motion with at least three characters.')
        return value

class GeneratedAnimationApply(Contract):
    animation_id: Identifier
    in_place: bool = Field(default=False, description='Suppress horizontal root travel. The saved motion is added to the selected rigged model, retaining its ID and existing clips; this flag only controls root travel.')

class GeneratedAnimationAccepted(Contract):
    id: Identifier
    state: Literal['queued', 'running', 'done', 'error', 'cancelled']
    status_url: str

class GeneratedAnimationItem(Contract):
    id: Identifier
    name: str
    prompt: str
    duration: float
    fps: int
    frames: int
    seed: int
    created: float
    clip_name: str
    provenance: dict
    state: Literal['queued', 'running', 'done', 'error', 'cancelled']
    progress: float
    stage: str
    preview_url: str | None = None
    preview_root: str | None = None
    files: dict[str,str] = Field(default_factory=dict,description='Completed-only download URLs for preview.glb, motion.npz and skeleton.json.')
    error: str | None = None

class GeneratedAnimationEngine(Contract):
    ready: bool
    label: str
    backend: str
    experimental: bool
    message: str

class GeneratedAnimationLibrary(Contract):
    animations: list[GeneratedAnimationItem]
    engine: GeneratedAnimationEngine
    min_duration: float
    max_duration: float
    step: float

class AnimationBatch(Contract):
    clips: list[Animation] = Field(min_length=1, max_length=len(ANIMATIONS))

    @field_validator('clips')
    @classmethod
    def distinct_clips(cls, clips):
        if len({item.clip for item in clips}) != len(clips):
            raise ValueError('Choose distinct library motions.')
        return clips

class AnimationRemoval(Contract):
    clip: str | None = Field(default=None, max_length=500, description='Exact embedded clip name, or null for an unnamed indexed clip.')
    index: int | None = Field(default=None, ge=0)
    revision: str | None = Field(default=None, pattern=r'^[0-9]+$')

    @model_validator(mode='after')
    def validate_selection(self):
        if self.index is None and not self.clip:
            raise ValueError('Choose a named clip or supply its index and expected name.')
        if self.index is not None and 'clip' not in self.model_fields_set:
            raise ValueError('Supply the expected clip name with its index.')
        return self

class Rig(Contract):
    mode: Literal['humanoid', 'creature'] | None = None
    profile: Literal['auto', 'advanced'] | None = None
    articulated_fingers: bool | None = Field(default=None, description='Enable independent finger joints with the Advanced rig. Requires separated fingers.')
    rotation:tuple[float,float,float]=(0,0,0)
    height_m:float=Field(default=1.7,ge=.2,le=3)
    offset_m:float=Field(default=0,ge=-1,le=1)
    landmarks:dict[str,list[float]]|None=Field(default=None,description='Optional complete set of 12 frontal joint markers [x/height,y/height], centre/ground origin before offset. +X is anatomical left. Requires an unrigged humanoid A/T pose; depth is predicted by UniRig.')
    @model_validator(mode='before')
    @classmethod
    def validate_setup(cls,value):
        from rig_landmarks import validate_rig_options
        if isinstance(value,dict):
            raw = {k: v for k, v in value.items()
                   if k not in ('landmarks', 'mode', 'profile', 'articulated_fingers') or v is not None}
            validate_rig_options(raw)
        return value

class CreatureBone(Contract):
    name: str
    parent: str | None
    head: tuple[float, float, float]
    tail: tuple[float, float, float]


class CreatureRigCompletion(Contract):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    bones: list[CreatureBone] = Field(min_length=1, max_length=512)
    motion: Literal['none', 'test', 'quadruped', 'serpentine', 'winged'] = 'test'

    @model_validator(mode='before')
    @classmethod
    def validate_edit(cls, value):
        from creature_rig import validate_creature_edit
        return validate_creature_edit(value)


class CreatureMotion(Contract):
    revision: str = Field(pattern=r'^[0-9]{1,24}$')
    motion: Literal['test', 'quadruped', 'serpentine', 'winged', 'swim']
    name: str | None = None
    duration_s: float | None = Field(default=None, ge=2, le=12)
    amplitude: float | None = Field(default=None, ge=.1, le=2)
    cycles: int | None = Field(default=None, ge=1, le=4)
    phase_lag: float | None = Field(default=None, ge=0, le=1.2)
    travel_m: float | None = Field(default=None, ge=0, le=.35)

    @model_validator(mode='before')
    @classmethod
    def validate_recipe(cls, value):
        from creature_motion_edit import validate_creature_motion_recipe

        if isinstance(value, dict):
            validate_creature_motion_recipe({key: item for key, item in value.items()
                                             if key != 'revision' and item is not None})
        return value


class PrintCopy(Contract):
    height_mm:float=Field(default=150,ge=20,le=500)
    voxel_mm:float=Field(default=1,ge=.15,le=2)

class MaterialAdjustment(Contract):
    roughness: float = Field(default=0, ge=-1, le=1, strict=True)
    metalness: float = Field(default=0, ge=-1, le=1, strict=True)


class Export(Contract):
    ids:list[Identifier]=Field(min_length=1,max_length=500)
    target:Literal['blender','unity','unreal','glb','print','library']='glb'
    include_revisions:bool=False
    material_adjustments: dict[Identifier, MaterialAdjustment] = Field(
        default_factory=dict, max_length=50,
        description='Per-model roughness and metallic adjustments from -1 to 1. Zero preserves the authored channel.',
    )

    @model_validator(mode='after')
    def check_material_selection(self):
        if self.material_adjustments:
            if self.target in ('print', 'library'):
                raise ValueError('Material adjustments require an export with materials.')
            if not self.material_adjustments.keys() <= set(self.ids):
                raise ValueError('Material adjustments must belong to the selected models.')
        return self


class QueueControl(Contract):
    paused:bool

class QueueEdit(Contract):
    priority:int|None=Field(default=None,ge=-2,le=2)
    retry:bool=Field(default=False,description='In-place retry is available in the desktop edition. Use POST /queue/{identifier}/retry for a new attempt.')


class JobRetry(Contract):
    workflow_options: dict | None = Field(default=None, description='Advanced options keyed by stage for a standalone profile.')
    resolution: Literal[512, 1024, 1536] | None = None
    texture_size: Literal[1024, 2048, 4096] | None = None
    triangles: int | None = Field(default=None, ge=TRIANGLE_MIN, le=TRIANGLE_MAX)
    height_m: float | None = Field(default=None, ge=.01, le=100)
    priority: int | None = Field(default=None, ge=-2, le=2)
    mesh_mode: Literal['direct', 'remesh'] | None = None
    pre_simplify: bool | None = None
    repair_holes: bool | None = None
    bake_normals: bool | None = None
    repair_surface: bool | None = None
    hd_reconstruction: bool | None = Field(default=None, strict=True)
    wtivo_reconstruction: bool | None = Field(default=None, strict=True)
    multiview_texture_refinement: bool | None = Field(default=None, strict=True)
    texture_method: TextureMethod | None = None


class Refinish(Contract):
    master_finishing: MasterFinishing | None = Field(default=None,
        description='Create an HD Master Finishing variant. Choose the reconstruction method and optionally the texture size.')
    hi3d_options: dict | None = Field(default=None, description='Optional Hi3D controls from the generation options schema. Omitted controls inherit the selected model’s values.')
    retexture_after_refinement: bool | None = Field(default=None, strict=True,
        description='Generate new TRELLIS.2 materials at 12 steps after cached Hi3D refinement, using the chosen texture size.')
    triangles: int | None = Field(default=None, ge=TRIANGLE_MIN, le=TRIANGLE_MAX)
    budget_preset: Literal['low', 'medium', 'high', 'ultra'] | None = None
    texture_size: Literal[1024, 2048, 4096] | None = None
    mesh_mode: Literal['direct', 'remesh'] | None = None
    pre_simplify: bool | None = None
    repair_holes: bool | None = None
    adaptive_finishing: bool | None = None
    bake_normals: bool | None = None
    repair_surface: bool | None = None
    hd_reconstruction: bool | None = Field(default=None, strict=True, description='Reconstruct a cached HD source at grid 768.')
    wtivo_reconstruction: bool | None = Field(default=None, strict=True,
        description='Reconstruct a cached 1024-or-higher source using the optional external WTiVo 1024 runtime.')
    remesh_resolution: Literal[512, 768, 1024] | None = None
    texture_sampling: Literal['trilinear', 'nearest'] | None = None
    normal_source: Literal['decoded', 'parent'] | None = None

    @field_validator('hi3d_options')
    @classmethod
    def validate_hi3d_options(cls, value):
        return None if value is None else validate_hi3d_overrides(value)

    @model_validator(mode='after')
    def validate_budget(self):
        if self.hi3d_options is not None and self.master_finishing not in HI3D_FINISHING_METHODS:
            raise ValueError('Advanced Hi3D controls require Detail Refinement (512 or 1024).')
        if self.retexture_after_refinement is not None and self.master_finishing not in HI3D_FINISHING_METHODS:
            raise ValueError('Post-refinement texturing requires Detail Refinement (512 or 1024).')
        allowed = {'master_finishing', 'texture_size', 'hi3d_options', 'retexture_after_refinement'}
        if self.master_finishing is not None and self.model_dump(exclude_none=True).keys() - allowed:
            raise ValueError('HD Master Finishing accepts a reconstruction method, texture size and eligible Hi3D options.')
        if self.triangles is not None and self.budget_preset is not None:
            raise ValueError('Choose either a triangle count or a preset.')
        return self


class FinishingBatch(Contract):
    variants: list[Refinish] = Field(min_length=1, max_length=3)


class FinishingSelection(Contract):
    selected_id: Identifier | None


class ParentNormalSource(Contract):
    available: bool
    unavailable_reason: str | None


class MasterFinishingMethod(Contract):
    available: bool
    reason: str


class MasterFinishingOptions(Contract):
    source_resolution: int
    default_method: MasterFinishing | None
    methods: dict[MasterFinishing, MasterFinishingMethod]
    triangle_ceiling: int
    default_texture_size: int


class CachedRetextureOptions(Contract):
    available: bool
    reason: str
    enabled: bool


class FinishingOptions(Contract):
    source_id: Identifier
    source_triangles: int | None
    cache_bytes: int
    texture_sizes: list[int]
    triangle_limits: dict[str, int]
    triangle_presets: dict[str, int]
    normal_bake: bool
    repair_surface: bool
    hd_reconstruction: bool = False
    wtivo_reconstruction: bool = False
    wtivo_reason: str = ''
    master_finishing: MasterFinishingOptions | None = None
    hi3d_options: dict | None = None
    retexture: CachedRetextureOptions | None = None
    remesh_resolutions: list[int]
    texture_sampling_modes: list[str]
    normal_sources: list[str]
    parent_normal_source: ParentNormalSource
    selected_id: Identifier | None


class FinishingBatchAccepted(Contract):
    ids: list[Identifier]
    source_id: Identifier
    count: int
    paused: bool

class ProjectionCamera(Contract):
    view: Literal['front', 'back', 'left', 'right', 'top', 'bottom', 'custom'] = 'front'
    frame: tuple[float, float, float, float] = Field(
        description='Left, bottom, right and top in world-plane coordinates for orthographic projection, '
                    'or on the plane one unit in front of a perspective camera.')
    projection: Literal['orthographic', 'perspective'] = 'orthographic'
    basis: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = Field(
        default=None, description='Required for a custom view. Rows are right, up and toward the camera, '
                                  'forming a right-handed orthonormal basis.')
    origin: tuple[float, float, float] | None = Field(
        default=None, description='Camera position in model world coordinates, required for perspective projection.')

    @model_validator(mode='after')
    def validate_pose(self):
        if (self.view == 'custom') != (self.basis is not None):
            raise ValueError('Supply a camera basis only for a custom view.')
        if (self.projection == 'perspective') != (self.origin is not None):
            raise ValueError('Supply a camera origin only for perspective projection.')
        return self


class ProjectionAlignment(Contract):
    camera: ProjectionCamera
    source_rect: tuple[Coordinate, Coordinate, Coordinate, Coordinate]
    target_rect: tuple[Coordinate, Coordinate, Coordinate, Coordinate]
    source_points: list[tuple[Coordinate, Coordinate]] | None = Field(default=None, min_length=4, max_length=4)
    target_points: list[tuple[Coordinate, Coordinate]] | None = Field(default=None, min_length=4, max_length=4)
    source_polygon: list[tuple[Coordinate, Coordinate]] | None = Field(
        default=None, min_length=3, max_length=128,
        description='Simple source-image polygon; normalized coordinates with top-left origin. Requires the polygon mask shape.')
    mask_shape: Literal['ellipse', 'rectangle', 'polygon'] = 'ellipse'
    linked_selection: bool = Field(
        default=False, strict=True,
        description='Evaluate the selection in source-image coordinates using the alignment transform. Polygon masks are always linked.')
    opacity: float = Field(default=1, gt=0, le=1)
    feather: float = Field(default=.06, ge=0, le=.5)

    @model_validator(mode='after')
    def validate_alignment(self):
        if (self.source_points is None) != (self.target_points is None):
            raise ValueError('Supply all corresponding points on both images.')
        if (self.source_polygon is not None) != (self.mask_shape == 'polygon'):
            raise ValueError('Supply a reference polygon only with the polygon mask shape.')
        return self


class TextureProjection(ProjectionAlignment):
    reference: Literal['prepared', 'conditioning', 'input'] | None = None
    upload_id: Identifier | None = None

    @model_validator(mode='after')
    def validate_reference(self):
        if (self.reference is None) == (self.upload_id is None):
            raise ValueError('Choose one saved reference or upload one image.')
        return self


class MultiViewTextureProjection(Contract):
    views: list[TextureProjection] = Field(
        min_length=2, max_length=2,
        description='Two independently aligned reference images to blend into one texture copy.')


class GenerationViewTextureProjection(Contract):
    method: Literal['generation_views'] = Field(
        description='Project the prepared views and calibrated cameras saved by the selected Pixal3D generation.')


class GenerationReferenceTextureProjection(ProjectionAlignment):
    method: Literal['generation_reference'] = Field(
        description='Project the saved TRELLIS.2 cutout with a supplied alignment. TRELLIS.2 has no saved generation camera.')


TextureProjectionRequest = (TextureProjection | MultiViewTextureProjection | GenerationViewTextureProjection
                            | GenerationReferenceTextureProjection)


PROTECTED_REGION_LIMIT = 24
PROTECTED_REGION_COORD_LIMIT = 3.4028234663852886e38  # GLB POSITION uses float32.
PROTECTED_REGION_RADIUS_LIMIT = 4 * PROTECTED_REGION_COORD_LIMIT
ModelCoordinate = Annotated[float, Field(strict=True, ge=-PROTECTED_REGION_COORD_LIMIT,
                                           le=PROTECTED_REGION_COORD_LIMIT)]


class ProtectedRegion(Contract):
    center: tuple[ModelCoordinate, ModelCoordinate, ModelCoordinate] = Field(
        description='Surface center in the source GLB coordinate system (Y up).')
    radius: float = Field(gt=0, le=PROTECTED_REGION_RADIUS_LIMIT, strict=True,
                          description='Radius in source GLB units.')
    strength: float = Field(ge=0, le=1, strict=True, description='Relative reduction priority from 0 to 1.')


class BakedGameCopy(Contract):
    profile: Literal['object', 'humanoid'] = 'object'
    triangles: int | None = Field(default=None, ge=5000, le=150000)
    texture_size: Literal[1024, 2048, 4096] = 2048
    bake_normals: bool = True
    protected_regions: list[ProtectedRegion] = Field(default_factory=list, max_length=PROTECTED_REGION_LIMIT,
        description='Optional soft spherical priorities. They add to the selected profile’s priority weights.')

    @model_validator(mode='after')
    def default_budget(self):
        if self.triangles is None:
            self.triangles = 14000 if self.profile == 'humanoid' else 28000
        return self


class Folder(Contract):
    name:str=Field(min_length=1,max_length=80)
    parent_id:str=Field(default='',pattern=r'^([a-f0-9]{32})?$')

class Assign(Contract):
    ids:list[Identifier]=Field(min_length=1,max_length=100)
    folder_id:str=Field(default='',pattern=r'^([a-f0-9]{32})?$')

class SavedView(Contract):
    name:str=Field(min_length=1,max_length=80)
    query:str=Field(default='',max_length=160)
    filter:str=Field(default='all',max_length=160)
    folder_id:str=Field(default='*',max_length=32)

class Checkpoint(Contract):
    label:str=Field(default='Checkpoint',min_length=1,max_length=100)

class RestoreVersion(Contract):
    revision:Identifier

class RestoreCollection(Contract):
    name:str=Field(default='Restored collection',min_length=1,max_length=80)

class JobAccepted(Contract):
    id:str
    state:str
    status_url:str

class AnimationAccepted(JobAccepted):
    operation_id: Identifier | None = None

class JobRetryAccepted(JobAccepted):
    retry_of: Identifier
    paused: bool

class ModelPage(Contract):
    total:int
    offset:int
    limit:int
    models:list[dict]
