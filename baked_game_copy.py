"""Optional CPU reduction of a selected static HD GLB, with a new PBR bake.

This path never reads a neural cache and never changes its donor. PyMeshLab is
an optional GPL-3.0 dependency; Blender runs in a private factory-startup scene.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from api_contract import PROTECTED_REGION_COORD_LIMIT, PROTECTED_REGION_LIMIT, PROTECTED_REGION_RADIUS_LIMIT
from glb_data import accessor, placed_vertices, read, static_placement
from studio_import import find_blender

PYMESHLAB_VERSION = '2025.7.post1'
MAX_TRIANGLES = 6_000_000
LIMITATIONS = [
    'Reduction preserves a weighted approximation, not every opening or thin part.',
    'Existing holes, intersecting surfaces and nonmanifold topology are not repaired.',
    'Automatic UVs and ray transfer require visual review; a hit is not proof of correct surface correspondence.',
    'This static copy has no animation-ready edge-flow guarantee.',
]


def normalize_game_copy_options(options):
    if not isinstance(options, dict):
        raise ValueError('Game-copy options must be an object.')
    unknown = set(options) - {'profile', 'triangles', 'texture_size', 'bake_normals', 'protected_regions'}
    if unknown:
        raise ValueError('Unknown game-copy option: ' + ', '.join(sorted(unknown)))
    profile = options.get('profile', 'object')
    if profile not in ('object', 'humanoid'):
        raise ValueError('Choose the object or humanoid profile.')
    triangles = options.get('triangles', 14000 if profile == 'humanoid' else 28000)
    size = options.get('texture_size', 2048)
    normals = options.get('bake_normals', True)
    if type(triangles) is not int or not 5000 <= triangles <= 150000:
        raise ValueError('Choose a target from 5,000 to 150,000 triangles.')
    if type(size) is not int or size not in (1024, 2048, 4096):
        raise ValueError('Choose a 1K, 2K or 4K texture atlas.')
    if type(normals) is not bool:
        raise ValueError('Normal-map baking must be true or false.')
    regions = options.get('protected_regions', [])
    if not isinstance(regions, list) or len(regions) > PROTECTED_REGION_LIMIT:
        raise ValueError(f'Choose up to {PROTECTED_REGION_LIMIT} protected regions.')
    normalized = []
    for region in regions:
        if not isinstance(region, dict) or set(region) != {'center', 'radius', 'strength'}:
            raise ValueError('Each protected region needs a center, radius and strength.')
        center, radius, strength = region['center'], region['radius'], region['strength']
        if not isinstance(center, list) or len(center) != 3 or any(
                type(value) not in (int, float) or not -PROTECTED_REGION_COORD_LIMIT <= value <= PROTECTED_REGION_COORD_LIMIT
                for value in center):
            raise ValueError('Protected region centers must contain three finite GLB coordinates.')
        if type(radius) not in (int, float) or not 0 < radius <= PROTECTED_REGION_RADIUS_LIMIT:
            raise ValueError('A protected region radius must be positive and finite.')
        if type(strength) not in (int, float) or not 0 <= strength <= 1:
            raise ValueError('Protected region strength must be between 0 and 1.')
        normalized.append(dict(center=[float(value) for value in center], radius=float(radius), strength=float(strength)))
    result = dict(profile=profile, triangles=triangles, texture_size=size, bake_normals=normals)
    if normalized:
        result['protected_regions'] = normalized
    return result


def game_copy_dependencies():
    """No native module import or model loading during capability checks."""
    try:
        version = importlib.metadata.version('pymeshlab') if importlib.util.find_spec('pymeshlab') else None
    except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
        version = None
    blender = bool(find_blender())
    ready = version == PYMESHLAB_VERSION and blender
    reason = None if ready else ('Install Blender to create a baked game copy.' if not blender else
        f'Install the optional PyMeshLab {PYMESHLAB_VERSION} dependency in the Studio Python environment.')
    return dict(ready=ready, reason=reason, blender=blender, pymeshlab=version, required_pymeshlab=PYMESHLAB_VERSION)


def _hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _material(data):
    return data['materials'][data['meshes'][0]['primitives'][0]['material']]


def _source(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 500 * 1024 * 1024:
        raise ValueError('Choose an embedded GLB no larger than 500 MiB.')
    data, binary = read(path)
    if len(data.get('meshes', [])) != 1 or data.get('skins') or data.get('animations'):
        raise ValueError('Choose a static, single-mesh HD model before rigging or animation.')
    primitives = data['meshes'][0].get('primitives', [])
    if len(primitives) != 1 or primitives[0].get('mode', 4) != 4 or primitives[0].get('targets'):
        raise ValueError('The HD model must contain one static triangle primitive.')
    if data.get('extensionsRequired'):
        raise ValueError('Compressed or extension-dependent GLBs are not supported by this bake.')
    instances = 0
    for node in data.get('nodes', []):
        if 'mesh' in node:
            instances += 1
            if node['mesh'] != 0:
                raise ValueError('Mesh instances are not supported by this bake.')
    if instances != 1:
        raise ValueError('Choose a GLB containing exactly one mesh instance.')
    primitive = primitives[0]
    attrs = primitive.get('attributes', {})
    color_attributes = {'COLOR_0', 'COLOR_1'}
    if primitive.get('extensions') or set(attrs) - {'POSITION', 'NORMAL', 'TANGENT', 'TEXCOORD_0'} - color_attributes:
        raise ValueError('Vertex colours, skin weights and additional UV sets are not supported by this bake.')
    materials = data.get('materials', [])
    if type(primitive.get('material')) is not int or not 0 <= primitive['material'] < len(materials):
        raise ValueError('Choose a single-material HD model with standard PBR textures.')
    # Unused archived material entries are harmless (e.g. a retextured donor).
    material = _material(data)
    if (material.get('alphaMode', 'OPAQUE') != 'OPAQUE' or material.get('extensions')
            or material.get('occlusionTexture') or material.get('emissiveTexture')
            or any(material.get('emissiveFactor', [0, 0, 0]))):
        raise ValueError('This bake supports opaque base colour, roughness, metallic and normal maps only; AO, emission and material extensions are not supported.')
    pbr = material.get('pbrMetallicRoughness', {})
    for info in (pbr.get('baseColorTexture'), pbr.get('metallicRoughnessTexture'), material.get('normalTexture')):
        if info and (info.get('texCoord', 0) != 0 or info.get('extensions')):
            raise ValueError('Use the first UV map without texture transforms for this bake.')
    vertices = accessor(data, binary, attrs['POSITION'])
    normals = accessor(data, binary, attrs['NORMAL'])
    uv = accessor(data, binary, attrs['TEXCOORD_0'])
    faces = accessor(data, binary, primitive['indices']).reshape(-1, 3)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not 3 <= len(vertices) <= 6_000_000
            or normals.shape != vertices.shape or uv.shape != (len(vertices), 2)
            or not 1 <= len(faces) <= MAX_TRIANGLES or faces.dtype.kind not in ('i', 'u')
            or faces.min() < 0 or faces.max() >= len(vertices)
            or not all(np.isfinite(a).all() for a in (vertices, normals, uv))):
        raise ValueError('The HD model has invalid or oversized geometry, normals or UVs.')
    # Blender's glTF importer uses automatic normals where custom normals are zero.
    lengths = np.linalg.norm(normals, axis=1)
    if ((lengths > 0) & (lengths < .1)).any():
        raise ValueError('The HD model has invalid or oversized geometry, normals or UVs.')
    # Game Asset's baked donor carries white colour layers with no visible effect.
    for name in color_attributes.intersection(attrs):
        colors = accessor(data, binary, attrs[name])
        if colors.shape not in ((len(vertices), 3), (len(vertices), 4)) or not np.all(colors == 1):
            raise ValueError('Only neutral white vertex colour layers are supported by this bake.')
    # Bound decoded image memory and reject unsupported external/image encodings.
    for image in data.get('images', []):
        if image.get('mimeType', '') not in ('image/png', 'image/jpeg') or 'bufferView' not in image:
            raise ValueError('Use embedded PNG or JPEG textures for this bake.')
        view = data['bufferViews'][image['bufferView']]
        start = view.get('byteOffset', 0)
        import io
        with Image.open(io.BytesIO(binary[start:start + view['byteLength']])) as decoded:
            if max(decoded.size) > 8192 or decoded.width * decoded.height > 8192 ** 2:
                raise ValueError('Source textures must be no larger than 8K.')
    placement = static_placement(data, allow_identity=True)
    extent = np.ptp(placed_vertices(vertices, placement), axis=0)
    if not np.isfinite(extent).all() or extent.max() <= 1e-8:
        raise ValueError('The HD model has no usable dimensions.')
    return data, vertices, faces, placement


def inspect_baked_game_copy(model_path):
    dependencies = game_copy_dependencies()
    try:
        data, vertices, faces, _ = _source(model_path)
        profiles = ['object']
        try:
            _region_weights(vertices, 'humanoid')
            profiles.append('humanoid')
        except ValueError:
            pass
        return dict(supported=True, reason=None, ready=dependencies['ready'], dependencies=dependencies,
                    triangles=len(faces), vertices=len(vertices), profiles=profiles,
                    texture_sizes=[1024, 2048, 4096], triangle_limits=[5000, 150000],
                    has_normal_map=bool(_material(data).get('normalTexture')), protected_region_limit=PROTECTED_REGION_LIMIT)
    except (OSError, ValueError, KeyError, IndexError, TypeError, OverflowError) as error:
        return dict(supported=False, reason=str(error), ready=dependencies['ready'], dependencies=dependencies)


def _region_weights(vertices, profile):
    weights = np.ones(len(vertices), dtype=np.float64)
    if profile == 'object':
        return weights, dict(kind='uniform', boundary_weight=5)
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    height = float(high[1] - low[1])
    if height <= 1e-8:
        raise ValueError('The humanoid profile requires an upright Y-up T-pose.')
    center = (low + high) * .5
    x = (vertices[:, 0] - center[0]) / height
    y = (vertices[:, 1] - low[1]) / height
    z = (vertices[:, 2] - center[2]) / height
    extent = (high - low) / height
    # Broad shape checks only, never an anatomical detector. Front is glTF +Z.
    face = (y > .845) & (abs(x) < .075) & (z > -.032)
    left = (x < -.38) & (y > .65) & (y < .86)
    right = (x > .38) & (y > .65) & (y < .86)
    if not (.72 <= extent[0] <= 1.4 and .04 <= extent[2] <= .6 and
            all(mask.sum() >= 10 for mask in (face, left, right))):
        raise ValueError('Humanoid protection requires an upright Y-up T-pose facing +Z, with both hands extended. Choose Object for other shapes or poses.')
    hands = left | right
    accessories = (y > .38) & (y < .62) & (abs(x) > .06) & (z > -.032)
    weights[face] = 40
    weights[hands] = 25
    weights[accessories] = 12
    bins = np.minimum((y * 438).astype(np.int32), 438)
    silhouette = np.zeros(len(vertices), bool)
    for angle in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        projection = x * np.cos(angle) + z * np.sin(angle)
        upper = np.full(439, -np.inf)
        np.maximum.at(upper, bins, projection)
        silhouette |= projection >= upper[bins] - .00086
    weights[silhouette] = np.maximum(weights[silhouette], 4)
    return weights, dict(kind='bbox-scaled T-pose regions, not anatomical detection', front='+Z', up='+Y',
                         face_vertices=int(face.sum()), hand_vertices=int(hands.sum()),
                         accessory_vertices=int(accessories.sum()), silhouette_vertices=int(silhouette.sum()),
                         weights=dict(face=40, hands=25, accessories=12, silhouette=4, other=1), boundary_weight=5)


def _add_protected_regions(vertices, weights, regions, profile):
    """Add the strongest local contribution per vertex to the existing profile."""
    maximum_boost = 44. if profile == 'humanoid' else 220.
    boosts = np.zeros(len(vertices), dtype=np.float64)
    batch_size = 64 * 1024
    summaries = []
    for region in regions:
        center = np.asarray(region['center'], dtype=np.float64)
        radius = region['radius']
        strength = region['strength'] * maximum_boost
        affected = 0
        if strength > 0:
            for start in range(0, len(vertices), batch_size):
                batch = slice(start, start + batch_size)
                distance = np.linalg.norm(vertices[batch] - center, axis=1)
                fraction = np.minimum(distance, radius) / radius
                influence = np.minimum(2 * (1 - fraction), 1) ** 1.5 * strength
                np.maximum(boosts[batch], influence, out=boosts[batch])
                affected += int(np.count_nonzero(influence))
        summaries.append({**region, 'affected_vertices': affected})
    weights += boosts
    return dict(coordinate_space='glTF Y-up model coordinates', regions=summaries,
                affected_vertices=int(np.count_nonzero(boosts)), maximum_weight_boost=maximum_boost,
                falloff='Full contribution through half radius, fading to zero at radius; strongest region wins at overlaps.')


def _clean(vertices, faces):
    valid = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    faces = faces[valid]
    area = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                                   vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1)
    faces = faces[area > 0]
    _, unique = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    return faces[np.sort(unique)]


def _coverage(glb, hit_path, size):
    data, binary = read(glb)
    primitive = data['meshes'][0]['primitives'][0]
    faces = accessor(data, binary, primitive['indices']).reshape(-1, 3)
    uv = accessor(data, binary, primitive['attributes']['TEXCOORD_0'])
    cover = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(cover)
    for triangle in uv[faces]:
        draw.polygon([tuple(v) for v in np.rint(triangle * (size - 1)).astype(int)], fill=255)
    interior = np.asarray(cover.filter(ImageFilter.MinFilter(3))) > 0
    hit = np.asarray(Image.open(hit_path).convert('RGB'))[:, :, 0] > 250
    count = int(interior.sum())
    return dict(uv_occupancy_percent=round(float((np.asarray(cover) > 0).mean() * 100), 4),
                interior_texels=count, bake_hit_percent=round(float(hit[interior].mean() * 100), 4) if count else 0,
                miss_texels=int((interior & ~hit).sum()),
                method='Actual exported UV triangles; one-pixel interior; independent unpadded white-donor bake. Hits do not establish semantic correspondence.')


def _topology(vertices, faces):
    welded, inverse = np.unique(vertices, axis=0, return_inverse=True)
    faces = inverse[faces]
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return dict(exact_position_vertices=len(welded), boundary_edges=int((counts == 1).sum()),
                nonmanifold_edges=int((counts > 2).sum()),
                interpretation='Exact-position welded edge counts; not a watertightness or intersection certificate.')


def run_baked_game_copy(source_path, output_glb, *, options, progress=None):
    """Produce one new GLB and return its report. Callback: (stage, fraction)."""
    options = normalize_game_copy_options(options)
    dependencies = game_copy_dependencies()
    if not dependencies['ready']:
        raise RuntimeError(dependencies['reason'])
    source, output = Path(source_path), Path(output_glb)
    if source.resolve() == output.resolve() or output.exists() or output.is_symlink():
        raise ValueError('Choose a new output file; the HD donor and existing files are never overwritten.')
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise ValueError('The game-copy staging folder must already exist.')
    started = time.monotonic()
    notify = progress or (lambda stage, fraction: None)
    notify('Validating the selected HD model', .02)
    original_hash = _hash(source)
    with TemporaryDirectory(prefix='.game-bake-', dir=output.parent) as directory:
        stage = Path(directory)
        donor = stage / 'donor.glb'
        shutil.copyfile(source, donor)
        if _hash(donor) != original_hash or _hash(source) != original_hash:
            raise ValueError('The selected HD model changed while preparing the copy.')
        data, native, faces, placement = _source(donor)
        if _material(data).get('normalTexture') and not options['bake_normals']:
            raise ValueError('Enable normal-map baking to preserve this donor\'s existing normal detail.')
        original_triangles = len(faces)
        vertices, inverse = np.unique(native, axis=0, return_inverse=True)
        faces = _clean(vertices, inverse[faces].astype(np.int32))
        source_triangles = len(faces)
        if not source_triangles:
            raise ValueError('The source contains no nondegenerate triangles.')
        weights, protection = _region_weights(vertices, options['profile'])
        regions = options.get('protected_regions', [])
        marked = False
        if regions:
            protection['manual'] = _add_protected_regions(vertices, weights, regions, options['profile'])
            marked = protection['manual']['affected_vertices'] > 0
        notify('Reducing geometry with protected boundaries', .12)
        os.environ.setdefault('OMP_NUM_THREADS', '3')
        import pymeshlab
        parameters = dict(targetfacenum=options['triangles'], targetperc=0, qualitythr=.3,
                          preservenormal=True, preservetopology=False, optimalplacement=True,
                          planarquadric=False, autoclean=False, selected=False,
                          qualityweight=options['profile'] == 'humanoid' or marked, preserveboundary=True, boundaryweight=5.)
        attempts = []
        for attempt in range(2):
            meshset = pymeshlab.MeshSet()
            meshset.add_mesh(pymeshlab.Mesh(vertex_matrix=vertices.astype(np.float64), face_matrix=faces,
                                           v_scalar_array=weights))
            if source_triangles > parameters['targetfacenum']:
                meshset.apply_filter('meshing_decimation_quadric_edge_collapse', **parameters)
            reduced = meshset.current_mesh()
            reduced.compact()
            low_vertices = reduced.vertex_matrix()
            low_faces = _clean(low_vertices, reduced.face_matrix())
            attempts.append(dict(requested=parameters['targetfacenum'], cleaned_triangles=len(low_faces)))
            if (attempt == 0 and source_triangles > options['triangles'] and
                    0 < len(low_faces) < options['triangles'] * .98):
                # Coincident faces removed after collapse can lower the usable count.
                # One bounded calibration pass, always from the same immutable donor.
                parameters['targetfacenum'] = min(source_triangles,
                    round(parameters['targetfacenum'] * options['triangles'] / len(low_faces)))
            else:
                break
        if not len(low_faces) or not np.isfinite(low_vertices).all():
            raise RuntimeError('Reduction did not produce usable geometry.')
        # Protection coordinates belong to the source mesh. Bake the reduced target
        # in world space so it aligns with Blender's imported donor hierarchy.
        np.savez(stage / 'target.npz', vertices=placed_vertices(low_vertices, placement), faces=low_faces)
        request = dict(options=options, double_sided=_material(data).get('doubleSided', False))
        (stage / 'request.json').write_text(json.dumps(request), encoding='utf-8')
        notify('Unwrapping the reduced mesh', .35)
        command = [str(find_blender()), '--background', '--factory-startup', '--disable-autoexec', '--threads', '3',
                   '--python-exit-code', '1', '--python', str(Path(__file__).with_name('game_bake_blender.py')), '--', str(stage)]
        child = None
        try:
            with (stage / 'blender.log').open('w', encoding='utf-8') as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                deadline = time.monotonic() + 1800
                last = None
                while child.poll() is None:
                    if time.monotonic() > deadline:
                        raise TimeoutError('The game-copy bake exceeded 30 minutes.')
                    try:
                        state = json.loads((stage / 'progress.json').read_text(encoding='utf-8'))
                        current = (state['stage'], state['fraction'])
                        if current != last:
                            notify(*current)
                            last = current
                    except (OSError, ValueError, KeyError):
                        pass
                    time.sleep(.2)
                if child.returncode:
                    details = (stage / 'blender.log').read_text(encoding='utf-8', errors='replace')[-3500:]
                    print(details, file=sys.stderr)
                    raise RuntimeError('Blender could not complete the game-copy bake. Check the private worker log for details.')
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
        notify('Checking exported triangles and texture coverage', .94)
        finished = stage / 'model.glb'
        final_data, final_vertices, final_faces, final_placement = _source(finished)
        final_vertices = placed_vertices(final_vertices, final_placement)
        coverage = _coverage(finished, stage / 'hit_mask.png', options['texture_size'])
        bake = json.loads((stage / 'bake-report.json').read_text(encoding='utf-8'))
        warnings = list(LIMITATIONS)
        if options['profile'] == 'humanoid':
            warnings.append('Humanoid regions assume a centred upright T-pose facing +Z; they are bounding-box heuristics, not detected anatomy.')
        if regions:
            warnings.append('Protected regions give the selected surface extra priority within the triangle budget. Review the result at close range.')
            if any(region['strength'] > 0 and not region['affected_vertices'] for region in protection['manual']['regions']):
                warnings.append('Some protected regions contained no source vertices and could not affect the reduction.')
        if len(final_faces) > options['triangles'] * 1.02:
            warnings.append('Boundary constraints prevented the requested triangle budget; inspect the actual output count.')
        if coverage['bake_hit_percent'] < 99:
            warnings.append('Some UV interiors did not hit the donor; inspect seams, recesses and thin parts.')
        report = dict(version=1, operation='baked_game_copy', triangles=len(final_faces), vertices=len(final_vertices),
                      source_triangles=original_triangles,
                      source_sha256=original_hash, model_sha256=_hash(finished), options=options,
                      reduction=dict(algorithm='PyMeshLab QEM', version=PYMESHLAB_VERSION, parameters=parameters,
                                     attempts=attempts,
                                     exact_weld_vertices=len(vertices), cleaned_source_triangles=source_triangles,
                                     preparation='Exact position weld; remove zero-area and identical index faces only; no smoothing, reconstruction or hole filling.', protection=protection),
                      coverage=coverage, topology=_topology(final_vertices, final_faces), bake=bake,
                      warnings=warnings, elapsed_seconds=round(time.monotonic() - started, 3))
        if _hash(source) != original_hash:
            raise ValueError('The selected HD model changed during baking; this copy was not published.')
        if output.exists():
            raise ValueError('The output file appeared during baking; it was not overwritten.')
        # Exclusive creation avoids replacing a file that appeared after the check.
        with finished.open('rb') as incoming, output.open('xb') as outgoing:
            try:
                shutil.copyfileobj(incoming, outgoing)
            except BaseException:
                outgoing.close()
                output.unlink(missing_ok=True)
                raise
        notify('Baked game copy ready for review', 1.)
        return report
