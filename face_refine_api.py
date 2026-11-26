"""Account-scoped face previews and immutable, derived collection models."""
import asyncio
import hashlib
import json
import math
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import time
import uuid

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image

from studio_import import validate_glb
from studio_workspace import ID, read, write


PREVIEW_KIND = 'face_refine_preview'
COPY_KIND = 'face_refinement'
SCRIPT = 'face_refine_worker.py'
TTL = 24 * 3600
MAX_IMAGE_BYTES = 24 * 1024 ** 2
MAX_IMAGE_PIXELS = 16_000_000


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def unsupported(path):
    from face_refine_geometry import supported_surface
    try:
        doc = validate_glb(path)
        part = supported_surface(doc)
        material = doc['materials'][part['material']]['pbrMetallicRoughness']
        base = material.get('baseColorTexture')
        if not base:
            return 'A base color texture is needed to locate the face on the model.'
        texture = doc['textures'][base['index']]
        if base.get('texCoord', 0) != 0 or base.get('extensions') or texture.get('extensions'):
            return 'Use a base-color image with standard UV coordinates.'
    except ValueError as error:
        return str(error)
    except (KeyError, IndexError, TypeError, OSError):
        return 'Choose a readable, textured GLB character.'
    return ''


def crop_box(raw):
    try:
        crop = json.loads(raw)
    except ValueError:
        crop = None
    if (not isinstance(crop, list) or len(crop) != 4
            or any(type(n) not in (float, int) or not math.isfinite(n) or not 0 <= n <= 1 for n in crop)
            or min(crop[2:]) <= 0 or crop[0] + crop[2] > 1.000001 or crop[1] + crop[3] > 1.000001):
        raise HTTPException(400, 'Choose a crop inside the portrait.')
    return crop


async def upload_image(image, target):
    try:
        size = 0
        with target.open('wb') as stream:
            while block := await image.read(1024 ** 2):
                size += len(block)
                if size > MAX_IMAGE_BYTES:
                    raise HTTPException(413, 'Choose a portrait smaller than 24 MB.')
                stream.write(block)
        with Image.open(target) as source:
            if (source.format not in ('PNG', 'JPEG', 'WEBP') or min(source.size) < 2
                    or source.width * source.height > MAX_IMAGE_PIXELS):
                raise ValueError()
            source.verify()
    except (ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, 'Choose a PNG, JPEG or WebP portrait up to 16 megapixels.') from None
    finally:
        await image.close()


def checked_strength(raw):
    if (not isinstance(raw, dict) or set(raw) != {'strength'} or type(raw['strength']) not in (float, int)
            or not math.isfinite(raw['strength']) or not 0 <= raw['strength'] <= 1):
        raise HTTPException(400, 'Choose an intensity between 0 and 100%.')
    return raw['strength']


def clear_copy_inputs(folder):
    for name in ('patch.bin', 'face-request.json', 'face-copy.tmp.glb'):
        (folder / name).unlink(missing_ok=True)
    for partial in folder.glob('face-*.part'):
        if partial.is_file() and partial.resolve() == partial:
            partial.unlink()


def remove_draft_data(folder):
    for name in ('portrait.png', 'patch.bin', 'head.obj', 'patch.bin.tmp'):
        try:
            (folder / name).unlink(missing_ok=True)
        except PermissionError:
            # A browser may still be reading the patch on Windows. The expiry pass retries.
            pass


def prune(g, now):
    root = g['STUDIO'].workspace.root.resolve() / 'face-previews'
    if not root.is_dir() or root.resolve() != root:
        return
    for folder in root.iterdir():
        if (not ID.fullmatch(folder.name) or folder.resolve() != folder or not folder.is_dir()
                or g['active_job'](folder.name)):
            continue
        status = folder / 'status.json'
        if not status.exists() or status.stat().st_mtime > now - TTL:
            continue
        state = read(status, {})
        if state.get('state') in ('queued', 'running'):
            g['STUDIO'].cancel_pending(folder.name, folder)
            write(status, dict(state='cancelled', stage='Preview expired', progress=0, discarded=True))
            remove_draft_data(folder)
        else:
            handle = g.get('LOG_HANDLE')
            if handle and Path(handle.name) == folder / 'generation.log':
                handle.close()
            try:
                shutil.rmtree(folder)
            except PermissionError:
                pass


def install(app, g):
    import face_refine_engine as engine
    studio = g['STUDIO']
    root = studio.workspace.root.resolve() / 'face-previews'
    base = '/api/jobs/{identifier}/face-refine'
    previews = base + '/previews'
    single = previews + '/{preview_id}'

    def workspace_root():
        if root.resolve() != root:
            raise HTTPException(403, 'Invalid preview directory.')
        root.mkdir(parents=True, exist_ok=True)

    def model(identifier):
        folder, record = studio.available(identifier)
        source = folder / 'model.glb'
        if source.resolve() != source or not source.is_file():
            raise HTTPException(409, 'Choose a completed character model.')
        return folder, record, source

    def reference(folder, record):
        from model_finishing_api import reference_lineage
        for ancestor, settings in reference_lineage(g['OUTPUT'], studio, folder, record):
            # Only generation images and explicitly retained face portraits are trusted.
            # Several other preparations copy the imported model's rendered thumbnail.
            if (settings.get('source_kind') not in (None, '', COPY_KIND)
                    or not settings.get('source_kind') and (settings.get('original_file') or settings.get('source_filename'))):
                continue
            image = ancestor / 'input.png'
            if image.is_file() and image.resolve() == image:
                return image
        return None

    def lookup(identifier, preview_id):
        if not ID.fullmatch(identifier) or not ID.fullmatch(preview_id):
            raise HTTPException(404)
        folder = root / preview_id
        if not folder.is_dir() or folder.resolve() != folder:
            raise HTTPException(404, 'Preview no longer available.')
        info = read(folder / 'settings.json', {})
        if info.get('parent_id') != identifier or info.get('source_kind') != PREVIEW_KIND:
            raise HTTPException(404, 'Preview no longer available.')
        return folder, info

    def queue(folder, parent):
        if g.get('WORKSPACE_CONFIG'):
            g['launch_worker'](folder, SCRIPT, source_id=parent)
        else:
            studio.workspace.enqueue(folder.name)

    def state(folder):
        status = read(folder / 'status.json', {})
        if status.get('discarded'):
            raise HTTPException(410, 'Preview discarded.')
        pending = g.get('WORKSPACE_CONFIG', {}).get('publication_pending')
        active = g['active_job'](folder.name)
        if status.get('state') == 'done' and (active or pending and pending(folder.name)):
            return dict(state='running', stage='Finishing preview', progress=99)
        if status.get('state') == 'running' and not active and not (pending and pending(folder.name)):
            return dict(state='error', stage='Preview interrupted', progress=0,
                        error='The preview stopped. Run the preview again.')
        return status

    def suggestion(source):
        try:
            return engine.suggest_crop(source)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.get(base)
    async def options(identifier: str):
        folder, record, source = model(identifier)
        reason = await asyncio.to_thread(unsupported, source)
        cap = engine.capability()
        image = reference(folder, record)
        crop = None
        if image and not reason and cap['available']:
            try:
                crop = await asyncio.to_thread(engine.suggest_crop, image)
            except (ValueError, OSError):
                # A legacy reference may be corrupt or too large. A new portrait remains usable.
                image = None
        return dict(available=not reason and cap['available'], reason=reason or cap['reason'], crop=crop,
                    reference_url=f'/api/jobs/{image.parent.name}/files/input.png' if image else None)

    @app.post(base + '/detect')
    async def detect(identifier: str, image: UploadFile = File(...)):
        model(identifier)
        cap = engine.capability()
        if not cap['available']:
            raise HTTPException(503, cap['reason'])
        with TemporaryDirectory(prefix='face-detect-') as temp:
            path = Path(temp) / 'reference'
            await upload_image(image, path)
            return dict(crop=await asyncio.to_thread(suggestion, path))

    @app.post(previews, status_code=202)
    async def create(identifier: str, crop: str = Form(...), image: UploadFile | None = File(None)):
        box = crop_box(crop)
        async with g['LOCK']:
            folder, record, source = model(identifier)
            cap = engine.capability()
            if not cap['available']:
                raise HTTPException(503, cap['reason'])
            reason = await asyncio.to_thread(unsupported, source)
            if reason:
                raise HTTPException(400, reason)
            workspace_root()
            candidate = root / uuid.uuid4().hex
            with TemporaryDirectory(prefix='.face-', dir=root) as temporary:
                tmp = Path(temporary)
                ref = reference(folder, record)
                if image:
                    ref = tmp / 'reference'
                    await upload_image(image, ref)
                if not ref:
                    raise HTTPException(400, 'Choose a portrait for this character.')
                try:
                    await asyncio.to_thread(engine.prepare_portrait, ref, box, tmp / 'portrait.png')
                except (ValueError, OSError) as error:
                    raise HTTPException(400, str(error)) from None
                if image:
                    ref.unlink()
                settings = dict(parent_id=identifier, source_kind=PREVIEW_KIND,
                                name=record['metadata'].get('display_name') or record['settings']['name'],
                                created=time.time())
                write(tmp / 'settings.json', settings)
                write(tmp / 'face-request.json', dict(source=str(source), crop=box,
                      replacement=bool(image), sha256=await asyncio.to_thread(file_hash, source)))
                write(tmp / 'status.json', dict(state='queued', stage='Waiting to refine the face', progress=0))
                tmp.rename(candidate)
            try:
                queue(candidate, identifier)
            except Exception:
                shutil.rmtree(candidate)
                raise
            return dict(id=candidate.name, state='queued')

    @app.get(single)
    async def status(identifier: str, preview_id: str):
        folder, _ = lookup(identifier, preview_id)
        data = state(folder)
        if data.get('state') == 'done':
            data = dict(data, patch_url=f'/api/jobs/{identifier}/face-refine/previews/{preview_id}/patch')
        return dict(data, id=preview_id)

    @app.get(single + '/patch')
    def patch(identifier: str, preview_id: str):
        folder, _ = lookup(identifier, preview_id)
        path = folder / 'patch.bin'
        if state(folder).get('state') != 'done' or not path.is_file() or path.resolve() != path:
            raise HTTPException(409, 'The face preview is not ready.')
        return FileResponse(path, media_type='application/octet-stream', headers={'Cache-Control': 'private, no-store'})

    @app.delete(single)
    async def discard(identifier: str, preview_id: str):
        async with g['LOCK']:
            folder, _ = lookup(identifier, preview_id)
            if g.get('ACTIVE') == preview_id and g['active_job'](preview_id):
                await asyncio.to_thread(g['stop_worker'])
            studio.cancel_pending(preview_id, folder)
            write(folder / 'status.json', dict(state='cancelled', stage='Preview discarded', progress=0, discarded=True))
            remove_draft_data(folder)
        return dict(ok=True)

    @app.post(single + '/copy', status_code=202)
    async def save(identifier: str, preview_id: str, request: Request):
        try:
            strength = checked_strength(await request.json())
        except ValueError:
            raise HTTPException(400, 'Provide the preview intensity.') from None
        async with g['LOCK']:
            draft, _ = lookup(identifier, preview_id)
            # Network retries must return the same job rather than create another large GLB.
            target = g['OUTPUT'] / uuid.uuid5(uuid.UUID(hex=preview_id), 'copy').hex
            if (g['TRASH'] / target.name).is_dir():
                raise HTTPException(409, 'This copy is in the trash. Restore it or run a new preview.')
            if target.is_dir():
                saved = read(target / 'settings.json', {})
                if saved.get('face_refinement', {}).get('strength') != strength:
                    raise HTTPException(409, 'This preview has already been saved with a different intensity.')
                return dict(id=target.name, state=read(target / 'status.json')['state'])
            if state(draft).get('state') != 'done':
                raise HTTPException(409, 'Wait for the preview to finish.')
            folder, record, source = model(identifier)
            recipe = read(draft / 'face-request.json')
            if await asyncio.to_thread(file_hash, source) != recipe['sha256']:
                raise HTTPException(409, 'The source model has changed. Run the preview again.')
            suffix = ' · Refined face'
            display = record['metadata'].get('display_name') or record['settings']['name']
            settings = dict(record['settings'], parent_id=identifier, source_kind=COPY_KIND,
                            asset_type='imported', created=time.time(), face_refinement=dict(strength=strength, crop=recipe['crop']))
            for key in ('finish_source_id', 'keep_finishing_source', 'workflow_id', 'workflow_input', 'profile_snapshot',
                        'original_file', 'source_filename', 'creature_phase'):
                settings.pop(key, None)
            metadata = dict(record['metadata'], display_name=display[:80-len(suffix)].rstrip()+suffix,
                            review_state='review', favorite=False)
            from asset_taxonomy import ASSET_TYPES
            if not metadata.get('asset_type') and record['settings'].get('asset_type') in ASSET_TYPES:
                metadata['asset_type'] = record['settings']['asset_type']
            with TemporaryDirectory(prefix='.face-copy-', dir=g['OUTPUT']) as temporary:
                tmp = Path(temporary)
                shutil.copy2(draft / 'patch.bin', tmp / 'patch.bin')
                parent_image = reference(folder, record)
                image = parent_image if not recipe.get('replacement') and parent_image else draft / 'portrait.png'
                shutil.copy2(image, tmp / 'input.png')
                write(tmp / 'face-request.json', dict(source=str(source), sha256=recipe['sha256'],
                      source_bytes=source.stat().st_size, result=record.get('result') or {}))
                write(tmp / 'metadata.json', metadata)
                write(tmp / 'settings.json', settings)
                write(tmp / 'status.json', dict(state='queued', stage='Waiting to save the face copy', progress=0, operation=COPY_KIND))
                tmp.rename(target)
            try:
                queue(target, identifier)
            except Exception:
                shutil.rmtree(target)
                raise
            return dict(id=target.name, state='queued')

    return discard
