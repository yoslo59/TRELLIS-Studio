"""Versioned agent API contracts. No model engines or GPU libraries are imported."""
from typing import Literal,Annotated
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator

from studio_version import VERSION
from generation_options import DENSE_MASTER, HUMANOID_EXPERIMENTAL, TRIANGLE_MIN, TRIANGLE_MAX
from retarget_catalogue import ANIMATIONS
Identifier=Annotated[str,Field(pattern=r'^[a-f0-9]{32}$')]

class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

Coordinate = Annotated[float, Field(ge=0, le=1)]
ViewRole = Literal['front', 'left', 'back', 'right']


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
    engine: Literal['trellis2', 'pixal3d'] = 'trellis2'
    remove_bg: bool = True


class ReferencePreviewView(Contract):
    role: ViewRole
    width: int
    height: int
    original: str
    cutout: str
    framed: str


class ReferencePreviewResult(Contract):
    engine: Literal['trellis2', 'pixal3d']
    views: list[ReferencePreviewView]


class Generation(Contract):
    upload_id:Identifier
    name:str=Field(default='New model',min_length=1,max_length=80)
    profile_id:str=Field(default='game_d',max_length=80,description='Built-in or custom profile ID from GET /profiles.')
    height_m:float=Field(default=1,ge=.01,le=100)
    seed:int=Field(default=42,ge=0,le=2147483647)
    priority:int=Field(default=0,ge=-2,le=2)
    overrides:dict=Field(default_factory=dict,examples=[{}],description='Optional recipe overrides: triangles, texture_size, resolution, remove_bg, advanced_settings, asset_type, reference_prompt, parent_id, keep_finishing_source, repair_surface (Dense Master only).')
    reference_edits: dict[Literal['front'], ReferenceEdit] | None = None

class MultiViewGeneration(ReferenceUploads):
    profile_id: Literal['character_pixal', HUMANOID_EXPERIMENTAL, DENSE_MASTER] = Field(
        default='character_pixal', description='Standard Pixal3D, Humanoid Experimental or Dense Master.')
    side_angle: Literal[40, 90] = 40
    name: str = Field(default='Character', min_length=1, max_length=80)
    height_m: float = Field(default=1.7, ge=.01, le=100)
    seed: int = Field(default=42, ge=0, le=2147483647)
    triangles: int | None = Field(default=None, ge=TRIANGLE_MIN, le=TRIANGLE_MAX)
    texture_size: Literal[1024, 2048] | None = None
    parent_id: Identifier | None = None
    reference_prompt: str = Field(default='', max_length=6000)
    keep_finishing_source: bool = False
    repair_surface: bool = False
    reproject_face: bool = False
    priority: int = Field(default=0, ge=-2, le=2)


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
    rotation:tuple[float,float,float]=(0,0,0)
    height_m:float=Field(default=1.7,ge=.2,le=3)
    offset_m:float=Field(default=0,ge=-1,le=1)
    landmarks:dict[str,list[float]]|None=Field(default=None,description='Optional complete set of 12 frontal joint markers [x/height,y/height], centre/ground origin before offset. +X is anatomical left. Requires an unrigged humanoid A/T pose; depth is predicted by UniRig.')
    @model_validator(mode='before')
    @classmethod
    def validate_setup(cls,value):
        from rig_landmarks import validate_rig_options
        if isinstance(value,dict):
            raw={k:v for k,v in value.items() if k!='landmarks' or v is not None}
            validate_rig_options(raw)
        return value

class PrintCopy(Contract):
    height_mm:float=Field(default=150,ge=20,le=500)
    voxel_mm:float=Field(default=1,ge=.15,le=2)

class Export(Contract):
    ids:list[Identifier]=Field(min_length=1,max_length=500)
    target:Literal['blender','unity','unreal','glb','print','library']='glb'
    include_revisions:bool=False

class QueueControl(Contract):
    paused:bool

class QueueEdit(Contract):
    priority:int|None=Field(default=None,ge=-2,le=2)
    retry:bool=Field(default=False,description='In-place retry is available in the desktop edition. Use POST /queue/{identifier}/retry for a new attempt.')


class JobRetry(Contract):
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


class Refinish(Contract):
    triangles: int | None = Field(default=None, ge=TRIANGLE_MIN, le=TRIANGLE_MAX)
    budget_preset: Literal['low', 'medium', 'high', 'ultra'] | None = None
    texture_size: Literal[1024, 2048, 4096] | None = None
    mesh_mode: Literal['direct', 'remesh'] | None = None
    pre_simplify: bool | None = None
    repair_holes: bool | None = None
    adaptive_finishing: bool | None = None
    bake_normals: bool | None = None
    repair_surface: bool | None = None

    @model_validator(mode='after')
    def validate_budget(self):
        if self.triangles is not None and self.budget_preset is not None:
            raise ValueError('Choose either a triangle count or a preset.')
        return self


class FinishingBatch(Contract):
    variants: list[Refinish] = Field(min_length=1, max_length=3)


class FinishingSelection(Contract):
    selected_id: Identifier | None


class FinishingOptions(Contract):
    source_id: Identifier
    source_triangles: int | None
    cache_bytes: int
    texture_sizes: list[int]
    triangle_limits: dict[str, int]
    triangle_presets: dict[str, int]
    normal_bake: bool
    repair_surface: bool
    selected_id: Identifier | None


class FinishingBatchAccepted(Contract):
    ids: list[Identifier]
    source_id: Identifier
    count: int
    paused: bool

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
