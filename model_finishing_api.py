"""Derived model operations shared by the desktop and private server workspaces."""
import asyncio
import shutil
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from PIL import Image

from api_contract import (
    BakedGameCopy, GenerationReferenceTextureProjection, GenerationViewTextureProjection, Identifier,
    JobAccepted, MultiViewTextureProjection, TextureProjectionRequest,
)
from generation_view_projection import (
    digest, generation_reference, generation_reference_name, inspect_generation_reference, inspect_generation_views,
)
from generation_profiles import COMFY_INPUT_FILES, STANDALONE_PROFILES
from studio_workspace import ID, finishing_source, write


REFERENCE_LABELS = {
    'prepared': 'Cutout reference',
    'conditioning': 'Generation reference',
    'input': 'Original reference',
}
MULTIVIEW_OPERATION = 'multiview_texture_refinement'
TextureRefinementMethod = Literal['multiview', 'ultratex']
OPERATION_LABELS = {
    'texture_projection': 'Texture projection',
    'baked_game_copy': 'Baked game copy',
    MULTIVIEW_OPERATION: 'Multiview texture',
}
REFERENCE_LINEAGE = {'refinish', 'baked_game_copy', 'texture_projection', MULTIVIEW_OPERATION,
                     'restored', 'game', 'lod', 'face_refinement'}
REFERENCE_LINEAGE_LIMIT = 16


def existing_file(folder, name):
    path = folder / name
    return path if path.is_file() and path.resolve().parent == folder.resolve() else None


def check_reference_sizes(paths, max_pixels):
    for index, path in enumerate(paths, start=1):
        try:
            with Image.open(path) as image:
                pixels = image.width * image.height
        except Image.DecompressionBombError:
            pixels = max_pixels + 1
        except (OSError, ValueError):
            raise HTTPException(400, f'Choose a readable reference image for view {index}.') from None
        if pixels > max_pixels:
            raise HTTPException(400, f'Resize the reference for view {index} to at most 16 megapixels.')


def readable_cutout(path, max_pixels):
    if path is None or path.is_symlink():
        return False
    try:
        if path.stat().st_size > 5 * max_pixels:
            return False
        with Image.open(path) as image:
            if (image.format != 'PNG' or min(image.size) < 2
                    or image.width * image.height > max_pixels):
                return False
            alpha = image.convert('RGBA').getchannel('A').histogram()
            return sum(alpha[:64]) >= max(8, image.width * image.height * .01) and sum(alpha[97:]) >= 16
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def readable_workflow_image(path, max_pixels):
    if path is None or path.is_symlink():
        return False
    try:
        with Image.open(path) as image:
            if (image.format not in ('PNG', 'JPEG', 'WEBP') or min(image.size) < 2
                    or image.width * image.height > max_pixels):
                return False
            image.verify()
            return True
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def reference_lineage(output, services, folder, record):
    output = output.resolve()
    visited = set()
    for _ in range(REFERENCE_LINEAGE_LIMIT):
        if folder.resolve() != output / folder.name or folder.name in visited:
            break
        visited.add(folder.name)
        settings = record['settings']
        yield folder, settings
        if settings.get('source_kind') not in REFERENCE_LINEAGE:
            break
        parent = settings.get('parent_id')
        if not isinstance(parent, str) or not ID.fullmatch(parent) or parent in visited:
            break
        try:
            folder, record = services.available(parent)
        except HTTPException as error:
            if error.status_code not in (403, 404, 409):
                raise
            break

def install(router, app, upload):
    services = app['STUDIO']

    def model(identifier):
        folder, record = services.available(identifier)
        path = existing_file(folder, 'model.glb')
        if not path:
            raise HTTPException(409, 'Choose a completed model with a GLB file.')
        return folder, record, path

    def references(folder):
        return [
            dict(id=key, label=label, url=f'/api/v1/models/{folder.name}/files/{key}.png')
            for key, label in REFERENCE_LABELS.items() if existing_file(folder, f'{key}.png')
        ]

    async def prepare_copy(folder, record, source, operation, *, options=None, projections=(), generation_views=None,
                           generation_cutout=None, multiview_reference=None, texture_method='multiview', native_ultratex=False):
        target = app['OUTPUT'] / uuid.uuid4().hex
        label = 'UltraTex 2K' if operation == MULTIVIEW_OPERATION and texture_method == 'ultratex' else OPERATION_LABELS[operation]
        settings = {
            **record['settings'],
            'parent_id': folder.name,
            'source_kind': operation,
            'asset_type': 'imported',
            'created': time.time(),
        }
        for key in ('finish_source_id', 'keep_finishing_source', 'bake_normals',
                    'normal_source', 'normal_source_id', 'normal_source_model_sha256',
                    'normal_source_normalization', 'repair_surface', 'hd_reconstruction', 'wtivo_reconstruction',
                    'master_finishing'):
            settings.pop(key, None)
        if operation == 'baked_game_copy':
            settings.update(triangles=options['triangles'], texture_size=options['texture_size'])
        elif operation == MULTIVIEW_OPERATION:
            settings.update(texture_method=texture_method, multiview_texture_refinement=texture_method == 'multiview')
            if texture_method == 'ultratex':
                settings['texture_size'] = 2048
            source_id = finishing_source(folder, record['settings'])
            if source_id:
                settings['finish_source_id'] = source_id
        metadata = {**record['metadata'], 'review_state': 'review', 'favorite': False}
        display = metadata.get('display_name') or settings['name']
        suffix = ' · ' + label
        metadata['display_name'] = display[:80 - len(suffix)].rstrip() + suffix
        request = dict(operation=operation, result=record.get('result') or {})
        if operation == MULTIVIEW_OPERATION:
            request['texture_method'] = texture_method
        staged_references = {}
        projection_requests = []
        for index, (reference, view_options) in enumerate(projections, start=1):
            numbered = f'-{index}' if len(projections) > 1 else ''
            name = f'projection-source{numbered}{reference.suffix.lower()}'
            staged_references[name] = reference
            projection_requests.append(dict(reference_file=name, options=view_options))
        if generation_views is not None:
            request['generation_views'] = generation_views
        elif len(projection_requests) > 1:
            request['views'] = projection_requests
        elif projection_requests:
            request.update(projection_requests[0])
        else:
            request['options'] = options

        def stage():
            target.mkdir()
            retained = generation_views
            if retained is None and record['settings'].get('engine') == 'pixal3d':
                _, retained = inspect_generation_views(folder, source, record['settings'], True, '')
            if generation_cutout is not None:
                request['generation_reference'] = dict(source_sha256=digest(source),
                                                       reference_sha256=digest(generation_cutout))
            shutil.copy2(source, target / 'source.glb')
            for key in REFERENCE_LABELS:
                path = existing_file(folder, f'{key}.png')
                if path:
                    shutil.copy2(path, target / path.name)
            for name, reference in staged_references.items():
                shutil.copy2(reference, target / name)
            if multiview_reference is not None:
                prepared = target / (multiview_reference.name if native_ultratex else 'prepared.png')
                shutil.copy2(multiview_reference, prepared)
                request['multiview_reference'] = dict(reference_file=prepared.name,
                    source_sha256=digest(target / 'source.glb'), reference_sha256=digest(prepared))
                if native_ultratex:
                    request['multiview_reference']['native_ultratex'] = True
            if retained is not None:
                request['retained_generation_views'] = retained
                for view in retained['views']:
                    role = view['role']
                    if role != 'front':
                        shutil.copy2(generation_reference(folder, role), target / generation_reference_name(role))
            if generation_views is not None:
                for view in generation_views['views']:
                    role = view['role']
                    shutil.copy2(generation_reference(folder, role),
                                 target / generation_reference_name(role, staged=True))
            write(target / 'settings.json', settings)
            write(target / 'metadata.json', metadata)
            write(target / 'finishing-request.json', request)
            write(target / 'status.json', dict(state='queued', stage=label, progress=0, operation=operation))

        try:
            await asyncio.to_thread(stage)
            if operation == MULTIVIEW_OPERATION and not app.get('WORKSPACE_CONFIG'):
                services.workspace.enqueue(target.name)
            else:
                app['launch_worker'](target, 'model_finishing_worker.py', source_id=folder.name)
        except (OSError, ValueError):
            if target.is_dir() and target.resolve() == target and target.parent == app['OUTPUT']:
                await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
            raise HTTPException(500, 'Could not prepare the derived model. Check available disk space.') from None
        return dict(id=target.name, state='queued', status_url=f'/api/v1/jobs/{target.name}')

    @router.get('/models/{identifier}/texture-projection', tags=['Models'],
                summary='Check support for texture projection on a static model')
    async def projection_options(identifier: Identifier):
        from texture_projection import MAX_REFERENCE_PIXELS, inspect_texture_projection
        folder, record, source = model(identifier)
        support = await asyncio.to_thread(inspect_texture_projection, source)
        calibrated, _ = await asyncio.to_thread(inspect_generation_views, folder, source, record['settings'],
                                                support['supported'], support['reason'])
        aligned, _ = await asyncio.to_thread(inspect_generation_reference, folder, record,
                                              support['supported'], support['reason'], MAX_REFERENCE_PIXELS)
        return {**support, 'references': references(folder), 'max_views': 2,
                'views': ['front', 'back', 'left', 'right', 'top', 'bottom', 'custom'],
                'camera_projections': ['orthographic', 'perspective'],
                'linked_selection': True, 'mask_shapes': ['ellipse', 'rectangle', 'polygon'],
                'generation_views': calibrated, 'generation_reference': aligned}

    @router.post('/models/{identifier}/texture-projection', tags=['Models'], status_code=202,
                 response_model=JobAccepted, summary='Project manual or saved generation views onto a linked copy')
    async def project(identifier: Identifier, recipe: TextureProjectionRequest):
        from texture_projection import MAX_REFERENCE_PIXELS, inspect_texture_projection, normalize_projection_options
        if isinstance(recipe, GenerationViewTextureProjection):
            async with app['LOCK']:
                app['require_idle']()
                folder, record, source = model(identifier)
                support = await asyncio.to_thread(inspect_texture_projection, source)
                calibrated, provenance = await asyncio.to_thread(inspect_generation_views, folder, source,
                                                                   record['settings'], support['supported'], support['reason'])
                if not calibrated['supported']:
                    raise HTTPException(400, calibrated['reason'])
                return await prepare_copy(folder, record, source, 'texture_projection', generation_views=provenance)
        views = recipe.views if isinstance(recipe, MultiViewTextureProjection) else [recipe]
        try:
            options = [normalize_projection_options(
                view.model_dump(mode='json', exclude={'reference', 'upload_id', 'method'}, exclude_none=True))
                for view in views]
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        async with app['LOCK']:
            app['require_idle']()
            folder, record, source = model(identifier)
            support = await asyncio.to_thread(inspect_texture_projection, source)
            if not support['supported']:
                raise HTTPException(400, support['reason'])
            if isinstance(recipe, GenerationReferenceTextureProjection):
                aligned, cutout = await asyncio.to_thread(inspect_generation_reference, folder, record,
                                                           support['supported'], support['reason'], MAX_REFERENCE_PIXELS)
                if cutout is None:
                    raise HTTPException(400, aligned['reason'])
                return await prepare_copy(folder, record, source, 'texture_projection',
                                          projections=[(cutout, options[0])], generation_cutout=cutout)
            paths = []
            for view in views:
                if view.upload_id:
                    reference, _ = upload(view.upload_id, 'image')
                    reference = existing_file(reference.parent, reference.name)
                else:
                    reference = existing_file(folder, f'{view.reference}.png')
                if not reference or reference.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
                    raise HTTPException(400, 'Choose an available reference image first.')
                paths.append(reference)
            if len(paths) > 1:
                await asyncio.to_thread(check_reference_sizes, paths, MAX_REFERENCE_PIXELS)
            return await prepare_copy(folder, record, source, 'texture_projection',
                                      projections=list(zip(paths, options, strict=True)))

    def single_reference(settings):
        views = settings.get('views')
        return (settings.get('view_mode') in (None, 'single')
                and not (settings.get('engine') == 'pixal3d' and isinstance(views, list) and len(views) > 1))

    async def multiview_cutout(folder, record, max_pixels):
        for folder, settings in reference_lineage(app['OUTPUT'], services, folder, record):
            if not single_reference(settings):
                return None, 'Choose a model from a single reference for multiview texture refinement.'
            path = existing_file(folder, 'prepared.png')
            if await asyncio.to_thread(readable_cutout, path, max_pixels):
                return path, ''
        return None, 'This model has no saved cutout with a transparent background in its source lineage.'

    async def native_workflow_reference(folder, record, max_pixels):
        native = False
        for folder, settings in reference_lineage(app['OUTPUT'], services, folder, record):
            if not single_reference(settings):
                break
            if settings.get('workflow_id') not in STANDALONE_PROFILES:
                continue
            native = True
            filename = settings.get('workflow_input')
            if filename in COMFY_INPUT_FILES:
                path = existing_file(folder, filename)
                if await asyncio.to_thread(readable_workflow_image, path, max_pixels):
                    return path, True
        return None, native

    async def texture_options(folder, record, source, methods):
        from texture_projection import MAX_REFERENCE_PIXELS, inspect_texture_projection
        workflow_image, native = (await native_workflow_reference(folder, record, MAX_REFERENCE_PIXELS)
                                  if 'ultratex' in methods else (None, False))
        cutout, cutout_reason, legacy_support = None, '', None
        choices, references = {}, {}
        for method in methods:
            native_method = method == 'ultratex' and native
            if native_method:
                from native_ultratex.existing_mesh import inspect
                from native_game_profile import texture_runtime_status
                from native_pixal3d import texture_mask_status
                support = await asyncio.to_thread(inspect, source)
                reference, reason = workflow_image, 'This native model has no readable saved workflow reference.'
                sizes = (1024, 2048, 4096)
                advisor = 'Native UltraTex needs a square 1K, 2K or 4K source color atlas with UV0.'
            else:
                if legacy_support is None:
                    legacy_support = await asyncio.to_thread(inspect_texture_projection, source)
                    if legacy_support['supported']:
                        legacy_support = {key: legacy_support[key] for key in ('supported', 'reason', 'atlas', 'triangles')}
                        cutout, cutout_reason = await multiview_cutout(folder, record, MAX_REFERENCE_PIXELS)
                support = legacy_support
                reference, reason = cutout, cutout_reason
                if method == 'ultratex':
                    from ultratex_runtime import availability
                    sizes = (2048,)
                    advisor = 'UltraTex uses the existing 2K UV atlas. Choose a model with a square 2K texture.'
                else:
                    from multiview_texture_runtime import availability
                    sizes = (1024, 2048, 4096)
                    advisor = 'Use a square 1K, 2K or 4K color atlas for multiview texture refinement.'
            if not support['supported']:
                choices[method] = {**support, 'ready': False}
            elif reference is None:
                choices[method] = {**support, 'supported': False, 'ready': False, 'reason': reason}
            elif not any(support['atlas'] == [size, size] for size in sizes):
                choices[method] = {**support, 'supported': False, 'ready': False, 'reason': advisor}
            else:
                if native_method:
                    runtime = await asyncio.to_thread(texture_runtime_status)
                    if runtime['available']:
                        runtime = await asyncio.to_thread(texture_mask_status)
                else:
                    runtime = await asyncio.to_thread(availability, app['ROOT'])
                choices[method] = {**support, 'ready': runtime['available'], 'reason': runtime['reason']}
                references[method] = reference
            if native_method:
                choices[method]['native'] = True
        return choices, references

    @router.get('/models/{identifier}/multiview-texture-refinement', tags=['Models'],
                summary='Check multiview texture support for a completed model')
    async def multiview_texture_options(identifier: Identifier, include_methods: bool = False):
        choices, _ = await texture_options(*model(identifier),
                                           ('multiview', 'ultratex') if include_methods else ('multiview',))
        return {**choices['multiview'], 'methods': choices} if include_methods else choices['multiview']

    @router.post('/models/{identifier}/multiview-texture-refinement', tags=['Models'], status_code=202,
                 response_model=JobAccepted, summary='Queue multiview texture refinement on a linked copy')
    async def make_multiview_texture_copy(identifier: Identifier, texture_method: TextureRefinementMethod = 'multiview'):
        async with app['LOCK']:
            if app.get('SHUTTING_DOWN'):
                raise HTTPException(503, 'Studio is stopping.')
            folder, record, source = model(identifier)
            choices, references = await texture_options(folder, record, source, (texture_method,))
            support = choices[texture_method]
            if not support['supported']:
                raise HTTPException(400, support['reason'])
            if not support['ready']:
                raise HTTPException(503, support['reason'])
            return await prepare_copy(folder, record, source, MULTIVIEW_OPERATION,
                                      multiview_reference=references[texture_method], texture_method=texture_method,
                                      native_ultratex=support.get('native') is True)

    @router.get('/models/{identifier}/baked-game-copy', tags=['Models'],
                summary='Check support for a reduced and baked copy of the selected master')
    async def game_options(identifier: Identifier):
        from baked_game_copy import inspect_baked_game_copy
        _, record, source = model(identifier)
        support = await asyncio.to_thread(inspect_baked_game_copy, source)
        humanoid = ((record['metadata'].get('asset_type') or record['settings'].get('asset_type')) == 'humanoid'
                    and 'humanoid' in support.get('profiles', ('humanoid',)))
        return {**support, 'humanoid': humanoid, 'default_triangles': 14000 if humanoid else 28000}

    @router.post('/models/{identifier}/baked-game-copy', tags=['Models'], status_code=202,
                 response_model=JobAccepted, summary='Make a reduced copy with materials baked from the selected master')
    async def make_game_copy(identifier: Identifier, recipe: BakedGameCopy):
        from baked_game_copy import inspect_baked_game_copy, normalize_game_copy_options
        try:
            options = normalize_game_copy_options(recipe.model_dump(mode='json'))
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        async with app['LOCK']:
            app['require_idle']()
            folder, record, source = model(identifier)
            support = await asyncio.to_thread(inspect_baked_game_copy, source)
            if not support['supported']:
                raise HTTPException(400, support['reason'])
            if not support.get('ready', True):
                raise HTTPException(503, 'Install Blender and PyMeshLab to create a baked game copy.')
            if options['profile'] not in support.get('profiles', ('object', 'humanoid')):
                raise HTTPException(400, 'Choose Object or use an upright, frontal T-pose for the humanoid profile.')
            return await prepare_copy(folder, record, source, 'baked_game_copy', options=options)
