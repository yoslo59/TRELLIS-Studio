"""Advanced settings for standalone generation recipes, independent of any graph runner."""
from functools import lru_cache
import json
import math
from pathlib import Path
import re

from native_ultratex.options import EDITABLE_TEXTURE_FIELDS, validate_texture_options


RECIPES = Path(__file__).resolve().parent / 'native_workflows'
PROFILE_IDS = ('game_asset_native', 'high_detail_native')
GAME_ASSET_EDITABLE = {
    '316': {'value'},
    '312': {'width', 'height', 'pad_factor', 'grow_mask', 'background'},
    '56': {'fov_x_degrees', 'resolution_level'},
    '119': {'resolution'}, '94': {'target_resolution'},
    '108': {'shift'},
    '125': {'multiplier'}, '126': {'multiplier'},
    '199': {'cfg', 'start_percent', 'end_percent'},
    '279': {'cfg', 'start_percent', 'end_percent'},
    **{stage: {'seed', 'steps', 'cfg', 'scheduler', 'sampler_name', 'denoise'} for stage in ('3', '18', '23')},
    **EDITABLE_TEXTURE_FIELDS,
}
GAME_ASSET_FINISHING = {'325', '327', '328', '335', '337', '338', '341', '342'}
GAME_ASSET_SAMPLERS = {'euler', 'ddim', 'heun', 'dpm_2', 'dpmpp_2m', 'euler_ancestral', 'ipndm'}
GAME_ASSET_ENGINE_STAGES = {'55': 'pixal3d', '56': 'pixal3d', '242': 'pixal3d',
                            '319': 'pixal3d', '40': 'trellis2'}


@lru_cache(maxsize=len(PROFILE_IDS))
def schema(profile_id):
    if profile_id not in PROFILE_IDS:
        raise ValueError('Unknown standalone generation profile.')
    path = RECIPES / f'{profile_id}.json'
    document = json.loads(path.read_text(encoding='utf-8'))
    if profile_id == 'game_asset_native':
        for stage in document['stages']:
            identifier = stage['id']
            if identifier in GAME_ASSET_ENGINE_STAGES:
                stage['geometry_backend'] = GAME_ASSET_ENGINE_STAGES[identifier]
            for field in stage['fields']:
                name = field['name']
                finishing = identifier in GAME_ASSET_FINISHING and name != 'load_output_images'
                if not finishing and name not in GAME_ASSET_EDITABLE.get(identifier, ()) and not field.get('locked'):
                    field['locked'] = True
                    field['hint'] = (field.get('hint', '') + ' The native stage uses this reference value.').strip()
                if name == 'sampler_name':
                    for choice in field['choices']:
                        choice['available'] = choice['value'] in GAME_ASSET_SAMPLERS
                        if not choice['available']:
                            choice['reason'] = 'This solver is not available in the native shape worker.'
    return document


def _value(field, value):
    label = field['label']
    kind = field['kind']
    if field.get('locked') and value != field['default']:
        raise ValueError(f'{label} is fixed by this profile.')
    if kind == 'choice':
        if not any(type(value) is type(choice['value']) and value == choice['value']
                   and choice.get('available', True) for choice in field['choices']):
            raise ValueError(f'Choose one of the listed values for {label}.')
        return value
    if kind == 'boolean':
        if type(value) is not bool:
            raise ValueError(f'{label} must be on or off.')
        return value
    if kind == 'text':
        if not isinstance(value, str) or len(value) > 6000 or '\0' in value:
            raise ValueError(f'{label} must contain at most 6,000 characters.')
        return value
    if kind == 'large_integer':
        if type(value) is int:
            value = str(value)
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,20}', value):
            raise ValueError(f'{label} must be a whole number.')
        number = int(value)
        if not int(field['min']) <= number <= int(field['max']):
            raise ValueError(f'{label} is outside the supported range.')
        return str(number)
    if kind == 'integer' and type(value) is not int:
        raise ValueError(f'{label} must be a whole number.')
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number.')
    if not field.get('min', -math.inf) <= value <= field.get('max', math.inf):
        raise ValueError(f'{label} is outside the supported range.')
    return value


def settings(profile_id, overrides=None):
    """Validate only named stage options and fill omitted values from the reference recipe."""
    stages = schema(profile_id)['stages']
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict) or overrides.keys() - {stage['id'] for stage in stages}:
        raise ValueError('Unknown workflow stage.')
    result = {}
    for stage in stages:
        values = overrides.get(stage['id'], {})
        fields = stage['fields']
        if not isinstance(values, dict) or values.keys() - {field['name'] for field in fields}:
            raise ValueError(f"Unknown option in {stage['title']}.")
        effective = {field['name']: _value(field, values.get(field['name'], field['default']))
                     for field in fields}
        for key, start in effective.items():
            if key.endswith('_interval_start') and start > effective[key[:-5] + 'end']:
                raise ValueError(f"The guidance interval is reversed in {stage['title']}.")
        if 'start_percent' in effective and effective['start_percent'] > effective['end_percent']:
            raise ValueError(f"The guidance interval is reversed in {stage['title']}.")
        result[stage['id']] = effective
    if profile_id == 'game_asset_native':
        _validate_game_options(result)
    else:
        _validate_high_options(result)
    return result


def _validate_game_options(options):
    if not re.fullmatch(r'#?[0-9a-fA-F]{6}', options['312']['background']):
        raise ValueError('Reference crop background must be a six-digit RGB hex colour.')
    if options['94']['target_resolution'] % 128:
        raise ValueError('Shape upsampling resolution must be a multiple of 128.')
    proxy = options['327']
    if proxy['proxy_eps_scale'] / proxy['input_res'] <= 0.0001:
        raise ValueError('Increase WTiVo proxy epsilon for this input resolution.')
    for field in ('neutral_normal_color', 'material_base_color'):
        try:
            components = [float(value.strip()) for value in re.split('[,;]', options['341'][field])]
        except ValueError:
            components = []
        if len(components) not in (3, 4) or not all(0 <= value <= 1 for value in components):
            raise ValueError('Bake colours need three or four comma-separated values between 0 and 1.')
    validate_texture_options(options)


def _validate_high_options(options):
    if options['25']['texture_method'] == 'ultratex' and options['25']['bake_on_vertices']:
        raise ValueError('UltraTex needs a UV atlas. Turn off Bake on vertices.')
    for identifier, stages in (('17', ('sparse_structure', 'shape')), ('20', ('shape',)), ('25', ('texture',))):
        stage = options[identifier]
        if stage['dino_lock'] and any(stage[f'{name}_rescale_t'] <= 0 for name in stages):
            raise ValueError('Rescale T must be positive when using DINO lock.')


def summary(profile_id, options):
    if profile_id == 'game_asset_native':
        return dict(resolution=int(options['94']['target_resolution']),
                    triangles=options['335']['target_tris'], texture_size=options['377']['texture_size'],
                    seed=int(options['3']['seed']), remove_bg=True, texture_method='ultratex',
                    geometry_backend=geometry_backend(profile_id, options))
    backend = geometry_backend(profile_id, options)
    material = options['25']
    size = material['ultratex_size'] if material['texture_method'] == 'ultratex' else (
        0 if material['bake_on_vertices'] else material['texture_size'])
    return dict(resolution=int(options['17']['pipeline_type'].split('_')[0]),
                triangles=options['22']['target_face_num'],
                texture_method=material['texture_method'], texture_size=size,
                seed=56 if backend == 'pixal3d' else options['17']['seed'],
                geometry_resolution=1536 if backend == 'pixal3d' else int(options['17']['pipeline_type'].split('_')[0]),
                geometry_backend=backend, remove_bg=options['12']['remove_background'])


def geometry_backend(profile_id, options):
    if profile_id == 'game_asset_native':
        return 'trellis2' if options['316']['value'] else 'pixal3d'
    return options['44']['geometry_backend']
