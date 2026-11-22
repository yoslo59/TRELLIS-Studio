"""Persistent user recipes. Built-in recipes never come from the writable store."""
from pathlib import Path
import json
import time
import uuid
from generation_options import (DENSE_MASTER, DENSE_MASTER_NAME, FIGURE_MASTER, GENERATION_PRESETS, HUMANOID_EXPERIMENTAL,
                                PROFILE_ADVANCED, TRIANGLE_MIN, TRIANGLE_MAX, normalize_options,
                                resolve_texture_method, validate_create_master_finishing,
                                validate_post_refinement_texturing, validate_texture_method)
from native_workflow_options import schema as workflow_schema

DENSE_MASTER_DESCRIPTION = ('Keeps the complete decoded geometry and materials before finishing. '
                            'Adapts the working copy to the surface, with a default ceiling of 1.2 million triangles.')
DETAIL_STUDY = 'detail_study'
PROPS_PROFILE = 'high_detail'
STARTUP_PROFILE = 'high_detail_native'
COMFY_INPUT_FILES = tuple('workflow-input' + extension for extension in ('.png', '.jpg', '.webp'))
COMFY_PROFILES = {
    'game_asset_comfy': dict(name='Game Asset · ComfyUI', label='PixelArtistry Character 05',
        description='Pixal3D 1536, a 12,000 triangle target, and UltraTex 2K with baked normals.',
        resolution=1536, target_faces=12_000, texture_size=2048, seed=56, texture_method='ultratex'),
    'high_detail_comfy': dict(name='High Detail · ComfyUI', label='High Quality GGUF',
        description='TRELLIS.2 1024 cascade with two geometry passes, QuadDC and MeshLib, and TRELLIS.2 textures at 4K.',
        resolution=1024, target_faces=2_000_000, texture_size=4096, seed=12345, texture_method='trellis'),
}
STANDALONE_PROFILES = {
    'game_asset_native': dict(name='Game Asset', label='Character workflow',
        description='A 12,000 triangle target with baked normals and multiview UltraTex materials.',
        resolution=1536, target_faces=12_000, texture_size=2048, seed=56, texture_method='ultratex'),
    'high_detail_native': dict(name='High Detail', label='High Quality GGUF',
        description='Two geometry passes with QuadDC and MeshLib. Uses UltraTex materials when available.',
        resolution=1024, target_faces=2_000_000, texture_size=4096, seed=12345, texture_method='trellis'),
}
WORKFLOW_PROFILES = {**COMFY_PROFILES, **STANDALONE_PROFILES}


def profile_options(overrides, steps):
    options = normalize_options(overrides, steps)
    options.pop('texture_seed')  # Every seed belongs to the object, not its recipe.
    return options


def builtin_profiles():
    # Retired recipes remain available to saved jobs and API callers.
    recipes = [
        ('game_d', 'Game - Default', dict(retired=True)),
        (PROPS_PROFILE, 'High Detail (Props)', dict(keep_finishing_source=True)),
        (FIGURE_MASTER, 'Figure Master', dict(keep_finishing_source=True,
            description='1024 cascade with 30 structure and 35 shape steps, one 1024 surface reconstruction and an 800,000 triangle ceiling.')),
        (HUMANOID_EXPERIMENTAL, 'Humanoid · Experimental', dict(retired=True,
            experimental=True, asset_type='humanoid', keep_finishing_source=True,
            description='Dense comparison master. Keeps the finishing source; review anatomy before reducing. No automatic local reconstruction or rig.')),
        (DENSE_MASTER, DENSE_MASTER_NAME, dict(retired=True, keep_finishing_source=True, description=DENSE_MASTER_DESCRIPTION)),
    ]
    profiles = []
    for key, name, flags in recipes:
        steps, triangles, texture_size = GENERATION_PRESETS[key]
        settings = dict(preset=key, resolution=1024, triangles=triangles, texture_size=texture_size,
                        remove_bg=True, multiview_texture_refinement=key == PROPS_PROFILE,
                        advanced=profile_options(PROFILE_ADVANCED[key], steps))
        if key == PROPS_PROFILE:
            settings['triangles'] = 300_000
            settings['advanced'].update(structure_steps=12, shape_steps=25, texture_steps=12,
                                        mesh_mode='remesh', remesh_resolution=1024)
        if key == FIGURE_MASTER:
            settings['texture_method'] = 'multiview'
            settings['multiview_texture_refinement'] = True
        profiles.append(dict(id=key, name=name, builtin=True, settings=settings, **flags))
    profiles.append(dict(id=DETAIL_STUDY, name='Detail Study', builtin=True, experimental=True, retired=True,
                         keep_finishing_source=True,
                         description='TRELLIS.2 12 / 24 / 12, Hi3D 1024 and a 4K texture pass after refinement.',
                         settings=dict(preset=DENSE_MASTER, resolution=1024, triangles=1_000_000, texture_size=4096,
                                       remove_bg=True, master_finishing='hi3d_1024', retexture_after_refinement=True,
                                       multiview_texture_refinement=False,
                                       advanced=profile_options({**PROFILE_ADVANCED[DENSE_MASTER],
                                           'structure_steps': 12, 'shape_steps': 24, 'texture_steps': 12,
                                           'texture_guidance': 1}, 12))))
    for key, recipe in STANDALONE_PROFILES.items():
        settings = dict(preset=key, resolution=recipe['resolution'], triangles=recipe['target_faces'],
                        texture_size=recipe['texture_size'], remove_bg=True, advanced={},
                        texture_method=recipe['texture_method'], multiview_texture_refinement=False)
        workflow = {field: recipe[field] for field in ('label', 'resolution', 'texture_size', 'target_faces')}
        workflow['advanced'] = workflow_schema(key)['stages']
        profiles.append(dict(id=key, name=recipe['name'], builtin=True, settings=settings,
                             description=recipe['description'], workflow=workflow))
    return profiles


def validate_recipe(data):
    if not isinstance(data, dict) or set(data) != {'name', 'settings'}:
        raise ValueError('Profile name and settings are required.')
    name = data['name']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 for c in name):
        raise ValueError('Choose a single-line name of 1–60 characters.')
    s = data['settings']
    keys = {'preset', 'resolution', 'triangles', 'texture_size', 'remove_bg', 'advanced'}
    optional = {'master_finishing', 'retexture_after_refinement', 'multiview_texture_refinement', 'texture_method'}
    if not isinstance(s, dict) or not keys <= set(s) <= keys | optional:
        raise ValueError('Incomplete or unknown profile settings.')
    if not isinstance(s['preset'], str) or s['preset'] not in GENERATION_PRESETS:
        raise ValueError('Unknown base preset.')
    for key, choices in [('resolution', (512, 1024, 1536)), ('texture_size', (1024, 2048, 4096))]:
        if type(s[key]) is not int or s[key] not in choices:
            raise ValueError('Invalid resolution or texture size.')
    if type(s['triangles']) is not int or not TRIANGLE_MIN <= s['triangles'] <= TRIANGLE_MAX:
        raise ValueError(f'Target must be between {TRIANGLE_MIN:,} and {TRIANGLE_MAX:,} triangles.')
    if type(s['remove_bg']) is not bool:
        raise ValueError('Background removal must be true or false.')
    multiview_texture_refinement = s.get('multiview_texture_refinement', False)
    if type(multiview_texture_refinement) is not bool:
        raise ValueError('Multiview texture refinement must be true or false.')
    texture_method = resolve_texture_method(s.get('texture_method'),
        s.get('multiview_texture_refinement'))
    validate_texture_method(texture_method, texture_size=s['texture_size'])
    finishing = s.get('master_finishing')
    if finishing is not None and not isinstance(finishing, str):
        raise ValueError('Choose a supported master finishing method.')
    validate_create_master_finishing(finishing, preset=s['preset'], resolution=s['resolution'], triangles=s['triangles'])
    validate_post_refinement_texturing(s.get('retexture_after_refinement', False), preset=s['preset'],
                                       resolution=s['resolution'], master_finishing=finishing)
    s = dict(s, multiview_texture_refinement=multiview_texture_refinement,
             advanced=profile_options(s['advanced'], GENERATION_PRESETS[s['preset']][0]))
    if 'texture_method' in s:
        s.update(texture_method=texture_method, multiview_texture_refinement=texture_method == 'multiview')
    return dict(name=name.strip(), settings=s)


class ProfileStore:
    def __init__(self, path):
        self.path = Path(path)

    def _read(self):
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('profiles'), list):
                raise ValueError()
            seen = set()
            for p in data['profiles']:
                if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not p['id'].startswith('user_') or p['id'] in seen:
                    raise ValueError()
                seen.add(p['id'])
                validate_recipe({k: p[k] for k in ('name', 'settings')})
            return data['profiles']
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('The profile file is unreadable and has been preserved. Restore its backup before trying again.')

    def list(self):
        return builtin_profiles() + self._read()

    def save(self, data, identifier=None):
        recipe = validate_recipe(data)
        profiles = self._read()
        if identifier and not any(p['id'] == identifier for p in profiles):
            raise KeyError(identifier)
        if any(p['name'].casefold() == recipe['name'].casefold() and p['id'] != identifier
               for p in builtin_profiles() + profiles):
            raise ValueError('This profile name already exists.')
        if not identifier and len(profiles) >= 100:
            raise ValueError('The limit of 100 custom profiles has been reached.')
        record = dict(recipe, id=identifier or 'user_' + uuid.uuid4().hex,
                      builtin=False, updated=time.time())
        profiles = [record if p['id'] == identifier else p for p in profiles] if identifier else profiles + [record]
        self._write(profiles)
        return record

    def delete(self, identifier):
        profiles = self._read()
        remaining = [profile for profile in profiles if profile['id'] != identifier]
        if len(remaining) == len(profiles):
            raise KeyError(identifier)
        self._write(remaining)

    def _write(self, profiles):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep one last-good store, and replace atomically. Never touch generations.
        if self.path.exists():
            self.path.with_suffix('.previous.json').write_bytes(self.path.read_bytes())
        temp = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        temp.write_text(json.dumps(dict(version=1, profiles=profiles), indent=2, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)
