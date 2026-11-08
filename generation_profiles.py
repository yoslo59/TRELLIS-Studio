"""Persistent user recipes. Built-in recipes never come from the writable store."""
from pathlib import Path
import json
import time
import uuid
from generation_options import GENERATION_PRESETS, GAME_D_ADVANCED, HUMANOID_EXPERIMENTAL, normalize_options


def profile_options(overrides, steps):
    options = normalize_options(overrides, steps)
    options.pop('texture_seed')  # Every seed belongs to the object, not its recipe.
    return options


def builtin_profiles():
    # Historical presets stay readable by old jobs, but are no longer offered.
    recipes = [('game_d', 'Game - Default', GAME_D_ADVANCED),
               ('high_detail', 'High Detail', {**GAME_D_ADVANCED, 'mesh_mode': 'direct'}),
               (HUMANOID_EXPERIMENTAL, 'Humanoid · Experimental', {**GAME_D_ADVANCED, 'mesh_mode': 'direct'})]
    profiles = [dict(id=key, name=name, builtin=True, settings=dict(
        preset=key, resolution=1024, triangles=GENERATION_PRESETS[key][1],
        texture_size=2048, remove_bg=True, advanced=profile_options(options,20)))
        for key,name,options in recipes]
    profiles[-1].update(experimental=True, asset_type='humanoid', keep_finishing_source=True,
                       description='Dense comparison master. Keeps the finishing source; review anatomy before reducing. No automatic local reconstruction or rig.')
    return profiles


def validate_recipe(data):
    if not isinstance(data, dict) or set(data) != {'name', 'settings'}:
        raise ValueError('Profile name and settings are required.')
    name = data['name']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 for c in name):
        raise ValueError('Choose a single-line name of 1–60 characters.')
    s = data['settings']
    keys = {'preset', 'resolution', 'triangles', 'texture_size', 'remove_bg', 'advanced'}
    if not isinstance(s, dict) or set(s) != keys:
        raise ValueError('Incomplete or unknown profile settings.')
    if not isinstance(s['preset'], str) or s['preset'] not in GENERATION_PRESETS:
        raise ValueError('Unknown base preset.')
    for key, choices in [('resolution', (512, 1024, 1536)), ('texture_size', (1024, 2048, 4096))]:
        if type(s[key]) is not int or s[key] not in choices:
            raise ValueError('Invalid resolution or texture size.')
    if type(s['triangles']) is not int or not 1000 <= s['triangles'] <= 500000:
        raise ValueError('Target must be between 1,000 and 500,000 triangles.')
    if type(s['remove_bg']) is not bool:
        raise ValueError('Background removal must be true or false.')
    s = dict(s, advanced=profile_options(s['advanced'], GENERATION_PRESETS[s['preset']][0]))
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep one last-good store, and replace atomically. Never touch generations.
        if self.path.exists():
            self.path.with_suffix('.previous.json').write_bytes(self.path.read_bytes())
        temp = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        temp.write_text(json.dumps(dict(version=1, profiles=profiles), indent=2, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)
        return record
