"""Versioned agent API contracts. No model engines or GPU libraries are imported."""
from typing import Literal,Annotated
from pydantic import BaseModel,ConfigDict,Field,field_validator

VERSION='3.1.0'
Identifier=Annotated[str,Field(pattern=r'^[a-f0-9]{32}$')]

class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Generation(Contract):
    upload_id:Identifier
    name:str=Field(default='New model',min_length=1,max_length=80)
    profile_id:str=Field(default='game_d',max_length=80,description='Built-in or custom profile ID from GET /profiles.')
    height_m:float=Field(default=1,ge=.01,le=100)
    seed:int=Field(default=42,ge=0,le=2147483647)
    priority:int=Field(default=0,ge=-2,le=2)
    overrides:dict=Field(default_factory=dict,examples=[{}],description='Optional recipe overrides: triangles, texture_size, resolution, remove_bg, advanced_settings, asset_type, reference_prompt, parent_id, keep_finishing_source.')

class MultiViewGeneration(Contract):
    upload_id: Identifier = Field(description='Front-view upload ID.')
    left_upload_id: Identifier | None = None
    back_upload_id: Identifier | None = None
    right_upload_id: Identifier | None = None
    side_angle: Literal[40, 90] = 40
    name: str = Field(default='Character', min_length=1, max_length=80)
    height_m: float = Field(default=1.7, ge=.01, le=100)
    seed: int = Field(default=42, ge=0, le=2147483647)
    triangles: int | None = Field(default=None, ge=1000, le=500000)
    texture_size: Literal[1024, 2048] | None = None
    parent_id: Identifier | None = None
    reference_prompt: str = Field(default='', max_length=6000)
    keep_finishing_source: bool = False
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

class Rig(Contract):
    rotation:tuple[float,float,float]=(0,0,0)
    height_m:float=Field(default=1.7,ge=.2,le=3)
    offset_m:float=Field(default=0,ge=-1,le=1)

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
    retry:bool=False

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

class ModelPage(Contract):
    total:int
    offset:int
    limit:int
    models:list[dict]
