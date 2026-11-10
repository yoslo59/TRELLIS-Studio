"""Read-only measurements against a user-selected export budget."""

import math
import re

from model_diagnostics import diagnose, source_sha256
from studio_import import validate_glb
from studio_workspace import read


USAGES = {'static', 'background', 'interactive', 'character', 'reference', 'print'}
FIELDS = {'usage', 'max_triangles', 'max_lod_triangles', 'max_texture_edge', 'max_materials',
          'expected_height_m', 'pivot', 'require_collision'}


def positive(value, label, ceiling, *, integer=False):
    if value is None:
        return None
    if type(value) not in (int, float) or not 0 < value <= ceiling or (integer and type(value) is not int):
        raise ValueError(f'Enter a valid {label}.')
    return value


def options(payload, target):
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError('Unknown export check setting.')
    usage = payload.get('usage', 'static')
    if not isinstance(usage, str) or usage not in USAGES or (target == 'print') != (usage == 'print'):
        raise ValueError('Choose an export use that matches the destination.')
    lods = payload.get('max_lod_triangles', [])
    if not isinstance(lods, list) or len(lods) > 4:
        raise ValueError('Set up to four detail-level budgets.')
    pivot = payload.get('pivot', 'any')
    if pivot not in ('any', 'ground', 'center'):
        raise ValueError('Choose a valid pivot expectation.')
    collision = payload.get('require_collision', False)
    if type(collision) is not bool:
        raise ValueError('Choose whether a collision proxy is required.')
    return dict(
        usage=usage,
        max_triangles=positive(payload.get('max_triangles'), 'triangle budget', 50_000_000, integer=True),
        max_lod_triangles=[positive(value, 'detail-level budget', 50_000_000, integer=True) for value in lods],
        max_texture_edge=positive(payload.get('max_texture_edge'), 'texture size limit', 32_768, integer=True),
        max_materials=positive(payload.get('max_materials'), 'material limit', 10_000, integer=True),
        expected_height_m=positive(payload.get('expected_height_m'), 'expected height', 1_000_000),
        pivot=pivot,
        require_collision=collision,
    )


def triangles(path):
    """Count rendered triangle instances in the default GLB scene."""
    data = validate_glb(path)
    accessors = data.get('accessors', [])
    mesh_counts = []
    for mesh in data['meshes']:
        count = 0
        for primitive in mesh['primitives']:
            mode = primitive.get('mode', 4)
            if mode not in (4, 5, 6):
                continue
            accessor = primitive.get('indices', primitive['attributes']['POSITION'])
            length = accessors[accessor]['count']
            count += length // 3 if mode == 4 else max(0, length - 2)
        mesh_counts.append(count)
    nodes = data.get('nodes', [])
    scenes = data.get('scenes', [])
    pending = list(scenes[data.get('scene', 0)].get('nodes', [])) if scenes else list(range(len(nodes)))
    visited = set()
    count = 0
    while pending:
        index = pending.pop()
        if index in visited:
            continue
        visited.add(index)
        node = nodes[index]
        if 'mesh' in node:
            instances = node.get('extensions', {}).get('EXT_mesh_gpu_instancing', {}).get('attributes', {})
            multiplier = accessors[next(iter(instances.values()))]['count'] if instances else 1
            count += mesh_counts[node['mesh']] * multiplier
        pending.extend(node.get('children', []))
    return count


def asset_report(folder, target, budget, *, digest=None, staged_cache=None, metadata=None):
    digest = digest or source_sha256(folder / 'model.glb')
    report = diagnose(folder, verified_digest=digest, staged_cache=staged_cache)
    measured = report['summary']
    metadata = metadata if metadata is not None else read(folder / 'metadata.json', {})
    settings = read(folder / 'settings.json', {})
    physics = read(folder / 'physics.json', {})
    checks = []

    def row(label, detail, status='checked', action=None):
        checks.append(dict(label=label, detail=detail, status=status, action=action))

    def maximum(label, actual, limit, suffix='', action='diagnostics'):
        detail = f'{actual:,}{suffix}'
        if limit is not None:
            detail += f' / {limit:,}{suffix}'
        row(label, detail, 'attention' if limit is not None and actual > limit else 'checked', action)

    maximum('Triangles · LOD 0', triangles(folder / 'model.glb'), budget['max_triangles'], action='lod')
    declared = read(folder / 'lod-manifest.json', {}).get('levels', [])
    levels = {
        entry['level']: folder / 'LODs' / entry['file']
        for entry in declared
        if type(entry.get('level')) is int and 1 <= entry['level'] <= 4
        and entry.get('file') == f"LOD{entry['level']}.glb"
    }
    lod_budgets = budget['max_lod_triangles']
    for level in sorted(set(levels) | {i for i, limit in enumerate(lod_budgets, 1) if limit is not None}):
        limit = lod_budgets[level - 1] if level <= len(lod_budgets) else None
        path = levels.get(level)
        if path is not None and path.is_file():
            maximum(f'Triangles · LOD {level}', triangles(path), limit, action='lod')
        else:
            row(f'Triangles · LOD {level}', 'No prepared model for this level', 'attention', 'lod')

    if target != 'print':
        textures = report['textures']
        max_edge = max((max(item['width'], item['height']) for item in textures), default=0)
        maximum('Largest texture edge', max_edge, budget['max_texture_edge'], ' px')
        footprint = sum(item['width'] * item['height'] * 4 for item in textures) / 1024**2
        row('Embedded textures', f'{len(textures)} · {footprint:.2f} MiB decoded RGBA8 (estimate)')
        maximum('Materials', measured['materials'], budget['max_materials'])
        if not measured['has_uv'] and textures:
            row('UV coordinates', 'Textured model has missing or incomplete UVs', 'attention', 'diagnostics')
        if measured['degenerate_uv_faces']:
            row('Collapsed UV faces', f"{measured['degenerate_uv_faces']:,}", 'attention', 'diagnostics')

    dimensions = measured['dimensions_m']
    height = measured['height_m']
    expected = budget['expected_height_m']
    unit, multiplier = ('mm', 1000) if target == 'print' else ('cm', 100) if target == 'unreal' else ('m', 1)
    dimension_label = ' × '.join(f'{value * multiplier:.4g}' for value in dimensions) + f' {unit}'
    if expected is not None:
        dimension_label += f' · expected height {expected * multiplier:.4g} {unit}'
    scaled = expected is not None and abs(height - expected) > max(expected * .05, .005)
    row('Width × height × depth', dimension_label, 'attention' if scaled else 'checked', 'print' if target == 'print' else 'game')

    if budget['pivot'] != 'any':
        low, high = measured['bounds_min_m'], measured['bounds_max_m']
        point = [(a + b) * .5 for a, b in zip(low, high)]
        if budget['pivot'] == 'ground':
            point[1] = low[1]
        distance = math.dist(point, (0, 0, 0))
        label = 'bottom centre' if budget['pivot'] == 'ground' else 'geometric centre'
        row('Pivot', f'Origin is {distance:.3g} m from the {label}',
            'attention' if distance > max(.005, max(dimensions) * .01) else 'checked', 'game')

    if target in ('unity', 'unreal'):
        kind = physics.get('kind', 'none')
        collision = kind in ('box', 'convex') and (folder / 'Collisions' / 'collision.glb').is_file()
        row('Collision proxy', 'Box' if collision and kind == 'box' else 'Convex hull' if collision else 'No prepared proxy',
            'attention' if budget['require_collision'] and not collision else 'checked', 'game')
    if budget['usage'] == 'character':
        row('Skeleton and motion', f"{measured['bones']} joints · {measured['clips']} clips",
            'attention' if not measured['bones'] else 'checked', 'diagnostics')

    for code, label in [('boundary_edges', 'Boundary edges'), ('nonmanifold_edges', 'Nonmanifold edges'),
                        ('degenerate_faces', 'Degenerate faces'), ('invalid_stored_normals', 'Invalid normals')]:
        if measured[code]:
            row(label, f'{measured[code]:,}', 'attention', 'diagnostics')
    inconsistent = sum(not mesh['winding_consistent'] for mesh in report['meshes'])
    if inconsistent:
        row('Face orientation', f'{inconsistent} mesh(es) have inconsistent winding', 'attention', 'diagnostics')
    if any(issue['severity'] == 'error' for issue in report['issues']):
        row('Source diagnostics', 'Inspect unreadable textures or invalid geometry', 'attention', 'diagnostics')
    review = metadata.get('review_state', 'review')
    return dict(version=1, target=target, budget=budget, id=folder.name,
                name=metadata.get('display_name', settings.get('name', 'Model')),
                source_glb_sha256=digest, checks=checks, human_review=review,
                status='attention' if any(check['status'] == 'attention' for check in checks) else 'checked')


def verified_hashes(payload, ids):
    if not isinstance(payload, dict) or set(payload) != set(ids) or any(
        not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in payload.values()
    ):
        raise ValueError('Run the export check for every selected model.')
    return payload
