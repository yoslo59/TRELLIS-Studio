"""Private Studio workspaces and a single server-wide compute queue."""
import asyncio
from collections import defaultdict
from importlib.util import module_from_spec, spec_from_file_location
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import sys

from fastapi import HTTPException
from starlette.responses import Response

from animation_edits import publish_operation, recover as recover_animation
from generation_options import GENERATION_PRESETS
from server_store import StoreError
from studio_workspace import ID, finishing_source, read, write


ROOT = Path(__file__).resolve().parent
MIB = 1024**2
LOG = logging.getLogger('trellis.server')
SAFE_SCRIPTS = {'worker.py', 'pixal3d_worker.py', 'import_worker.py', 'rig_worker.py', 'print_worker.py', 'asset_work.py', 'animation_worker.py'}
REFERENCE_FILES = {'settings.json', 'metadata.json', 'status.json', 'generation.log', 'server-work.json',
                   'input.png', 'left.png', 'right.png', 'back.png', 'conditioning.png', 'prepared.png',
                   'prepared-left.png', 'prepared-right.png', 'prepared-back.png'}
TERMINAL = {'done', 'error', 'cancelled'}


def tree_bytes(root, *, caches_only=False):
    total = 0
    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(entry.path)
                        elif stat.S_ISREG(info.st_mode) and (not caches_only or entry.name == 'raw_state.pt'):
                            total += info.st_size
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            continue
    return total


def remove_child(parent, path):
    """Remove only an explicitly identified child of a private workspace directory."""
    if path.parent != parent or path.resolve() != path:
        raise ValueError('Unsafe workspace path.')
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def children(folder):
    return set(folder.iterdir()) if folder.is_dir() else set()


def reserve_bytes(settings, script, folder):
    if script == 'worker.py' and settings.get('source_kind') == 'refinish':
        return 32 * MIB
    if script in ('worker.py', 'pixal3d_worker.py'):
        return (256 if settings.get('keep_finishing_source') else 64) * MIB
    if script == 'animation_worker.py' and (
        settings.get('source_kind') == 'animation_copy' or (folder/'operation.json').is_file()
    ):
        # Blender creates a staging GLB, the final GLB and a packed Blender file.
        source_bytes = (folder / 'source.glb').stat().st_size
        return 32 * MIB + 3 * source_bytes + tree_bytes(folder / 'LODs')
    return 32 * MIB


class PrivateStudio:
    def __init__(self, directory, user_id):
        self.directory = directory
        self.user_id = user_id
        self.lock = asyncio.Lock()
        self.outputs = directory / 'outputs'
        self.exports = directory / 'exports'
        self.state = directory / 'state'
        self.animation_operations = self.state / 'animation-ops'
        self.trash = directory / 'trash'
        self.active = None
        self.module_name = '_trellis_workspace_' + user_id
        spec = spec_from_file_location(self.module_name, ROOT / 'app.py')
        self.studio = module_from_spec(spec)
        self.studio.WORKSPACE_CONFIG = dict(outputs=self.outputs, exports=self.exports, state=self.state,
                                           trash=self.trash, profiles=self.state / 'profiles.json')
        sys.modules[self.module_name] = self.studio
        try:
            spec.loader.exec_module(self.studio)
        except Exception:
            sys.modules.pop(self.module_name, None)
            raise
        self.workspace = self.studio.STUDIO.workspace
        self.launch = self.studio.launch_worker
        self.studio.launch_worker = self.enqueue_operation

    async def start(self):
        await self.studio.STUDIO.start()

    async def close(self):
        await self.studio.STUDIO.close()
        await asyncio.to_thread(self.studio.stop_worker)
        await asyncio.to_thread(self.studio.stop_export)
        for stream in (self.studio.LOG_HANDLE, self.studio.EXPORT_LOG):
            if stream:
                stream.close()
        sys.modules.pop(self.module_name, None)

    def enqueue_operation(self, folder, script, source_id=None):
        write(folder / 'server-work.json', dict(script=script, source_id=source_id))
        self.workspace.enqueue(folder.name)

    def job_spec(self, folder):
        settings = read(folder / 'settings.json', {})
        work = read(folder / 'server-work.json', {})
        if folder.parent == self.animation_operations:
            return 'animation_worker.py', None, settings
        if settings.get('source_kind') == 'refinish':
            return 'worker.py', finishing_source(folder, settings), settings
        if work.get('script') in SAFE_SCRIPTS:
            return work['script'], work.get('source_id'), settings
        script = 'pixal3d_worker.py' if settings.get('engine') == 'pixal3d' else 'worker.py'
        return script, None, settings

    def pending(self):
        if self.workspace.queue.get('paused'):
            return []
        return [entry for entry in self.workspace.ordered() if not entry.get('held') and not entry.get('cancelled')
                and read(self.job_folder(entry['id']) / 'status.json', {}).get('state') == 'queued']

    def is_animation_operation(self, identifier):
        folder = self.animation_operations / identifier
        return bool(ID.fullmatch(identifier) and folder.is_dir() and folder.resolve() == folder)

    def job_folder(self, identifier):
        if self.is_animation_operation(identifier):
            return self.animation_operations / identifier
        return self.outputs / identifier

    def busy(self):
        return any(process and process.poll() is None for process in (self.studio.PROCESS, self.studio.EXPORT_PROCESS)) or bool(self.studio.TERRAIN_ACTIVE)

    def snapshot(self, path):
        folders = [self.outputs, self.exports, self.state / 'uploads', self.state / 'imports',
                   self.animation_operations]
        for identifier in set(path.split('/')):
            if ID.fullmatch(identifier):
                model = self.outputs / identifier
                folders.extend(model / name for name in ('Revisions', 'Blender', 'Diagnostics', 'Presentation'))
        return {folder: children(folder) for folder in folders}


class ServerRuntime:
    def __init__(self, data, store):
        self.data = Path(data).resolve()
        self.store = store
        self.workspaces = {}
        self.load_lock = asyncio.Lock()
        self.scheduler = None
        self.paused_path = self.data / 'queue-control.json'
        self.paused = bool(read(self.paused_path, {}).get('paused'))
        self.current_user = None
        self.stopping = False

    def user_path(self, identifier):
        if not ID.fullmatch(identifier):
            raise ValueError('Invalid workspace identifier.')
        parent = self.data / 'users'
        path = parent / identifier
        if path.resolve() != path:
            raise ValueError('Unsafe workspace location.')
        return path

    async def workspace(self, identifier):
        async with self.load_lock:
            if identifier not in self.workspaces:
                workspace = await asyncio.to_thread(PrivateStudio, self.user_path(identifier), identifier)
                await workspace.start()
                admitted = {job['id'] for job in self.store.jobs(identifier) if job['state'] not in TERMINAL}
                for entry in workspace.workspace.queue['items']:
                    folder = workspace.job_folder(entry['id'])
                    status = folder / 'status.json'
                    if entry['id'] not in admitted and read(status, {}).get('state') == 'queued':
                        write(status, dict(state='error', stage='Interrupted before queue admission', progress=0))
                        if folder.parent == workspace.animation_operations:
                            await self.trim_animation_operation(workspace, folder)
                self.workspaces[identifier] = workspace
            return self.workspaces[identifier]

    async def usage(self, identifier):
        amount = await asyncio.to_thread(tree_bytes, self.user_path(identifier))
        self.store.sync_usage(identifier, amount)
        return amount

    def set_paused(self, paused):
        write(self.paused_path, {'paused': paused})
        self.paused = paused

    async def start(self):
        pending_users = {row['user_id'] for row in self.store.jobs() if row['state'] not in TERMINAL}
        for identifier in pending_users:
            workspace = await self.workspace(identifier)
            rows = [row for row in self.store.jobs(identifier) if row['state'] not in TERMINAL]
            async with workspace.lock:
                await self.reconcile(workspace, rows)
        self.scheduler = asyncio.create_task(self.run())

    async def close(self):
        self.stopping = True
        if self.scheduler:
            self.scheduler.cancel()
            try:
                await self.scheduler
            except asyncio.CancelledError:
                pass
        await asyncio.gather(*(workspace.close() for workspace in self.workspaces.values()))

    def busy(self):
        return any(workspace.lock.locked() or workspace.busy() for workspace in self.workspaces.values())

    async def rollback(self, workspace, original_queue, before):
        if workspace.studio.EXPORT_TASK and workspace.exports / workspace.studio.EXPORT_TASK not in before[workspace.exports]:
            await asyncio.to_thread(workspace.studio.stop_export)
            if workspace.studio.EXPORT_LOG:
                workspace.studio.EXPORT_LOG.close()
                workspace.studio.EXPORT_LOG = None
            workspace.studio.EXPORT_PROCESS = None
            workspace.studio.EXPORT_TASK = None
        for folder, previous in before.items():
            for path in children(folder) - previous:
                await asyncio.to_thread(remove_child, folder, path)
        workspace.workspace.queue = original_queue
        workspace.workspace.save_queue()
        await self.usage(workspace.user_id)

    async def submit(self, user, workspace, path, length, invoke, reclaim=False):
        from copy import deepcopy

        identifier = user['id']
        async with workspace.lock:
            user = self.store.get_user(identifier)
            if self.stopping or user['status'] != 'active':
                raise HTTPException(403, 'Account inactive or server stopping.')
            limit = user['disk_limit_bytes']
            used = await self.usage(identifier)
            if limit is not None and not reclaim and used + user['storage_reserved_bytes'] + length + 4096 > limit:
                raise StoreError('Your disk quota has been reached. Free space in your library.', 413, 'disk_quota')
            if not reclaim and shutil.disk_usage(self.data).free < length + 64 * MIB:
                raise HTTPException(507, 'The server does not have enough free disk space.')
            original_queue = deepcopy(workspace.workspace.queue)
            before = workspace.snapshot(path)
            try:
                response = await invoke()
            except Exception:
                await self.rollback(workspace, original_queue, before)
                raise
            if response.status_code >= 400:
                if any(children(folder) - previous for folder, previous in before.items()):
                    await self.rollback(workspace, original_queue, before)
                return response
            try:
                used = await self.usage(identifier)
                current = self.store.get_user(identifier)
                if current['status'] != 'active':
                    raise StoreError('This account was deactivated.', 403, 'account_inactive')
                limit = current['disk_limit_bytes']
                if limit is not None and not reclaim and used + current['storage_reserved_bytes'] > limit:
                    raise StoreError('The operation would exceed your disk quota.', 413, 'disk_quota')
                previous_ids = {entry['id'] for entry in original_queue['items']}
                jobs = []
                for entry in workspace.workspace.queue['items']:
                    job_id = entry['id']
                    if job_id in previous_ids or entry.get('cancelled'):
                        continue
                    folder = workspace.job_folder(job_id)
                    script, _, settings = workspace.job_spec(folder)
                    generation = script in ('worker.py', 'pixal3d_worker.py') and settings.get('source_kind') != 'refinish' and settings.get('preset') in GENERATION_PRESETS
                    jobs.append(dict(job_id=job_id, generation=generation, reserve_bytes=reserve_bytes(settings, script, folder)))
                jobs.extend(dict(job_id=path.name, generation=False, reserve_bytes=32 * MIB)
                            for path in children(workspace.exports) - before[workspace.exports])
                if jobs:
                    self.store.reserve_jobs(identifier, jobs)
            except (StoreError, OSError):
                await self.rollback(workspace, original_queue, before)
                raise
            if reclaim:
                pending = [row for row in self.store.jobs(identifier) if row['state'] not in TERMINAL]
                await self.reconcile(workspace, pending)
            return response

    async def trim_failed(self, workspace, folder):
        if folder.parent == workspace.outputs and ID.fullmatch(folder.name):
            for path in children(folder):
                if path.name not in REFERENCE_FILES:
                    await asyncio.to_thread(remove_child, folder, path)

    async def trim_animation_operation(self, workspace, folder):
        if folder.parent == workspace.animation_operations and ID.fullmatch(folder.name):
            for path in children(folder):
                if path.name not in ('operation.json', 'status.json', 'generation.log'):
                    await asyncio.to_thread(remove_child, folder, path)

    async def reconcile_animation(self, workspace, row, folder):
        state = read(folder/'status.json', {})
        process = workspace.studio.PROCESS
        active = workspace.studio.ACTIVE == folder.name and process and process.poll() is None
        used = await self.usage(workspace.user_id)
        user = self.store.get_user(workspace.user_id)
        limit = user['disk_limit_bytes']
        over = limit is not None and used + max(0, user['storage_reserved_bytes'] - row['reserve_bytes']) > limit
        if (active or state.get('state') == 'queued') and not over:
            return
        if active:
            await asyncio.to_thread(workspace.studio.stop_worker)

        operation = read(folder/'operation.json', {})
        model_id = operation.get('model_id', '')
        source = workspace.outputs / model_id if ID.fullmatch(model_id) else None
        if source is not None and (source.resolve() != source or not source.is_dir()):
            source = None
        marker = source/'animation-transaction.json' if source else None
        committed = (source and not marker.is_file()
                     and read(source/'status.json', {}).get('last_animation_operation') == folder.name)
        terminal = state.get('state')
        if (terminal == 'ready' and not over) or committed:
            try:
                if source is None:
                    raise ValueError('The source model is no longer available.')
                await asyncio.to_thread(publish_operation, source, folder, state, operation.get('revision'))
                state = dict(state='done', stage='Animations saved to model', progress=100)
            except (OSError, ValueError) as error:
                if marker and marker.is_file():
                    raise
                state = dict(state='error', stage='Animation update failed', progress=0, error=str(error))
        elif (over and terminal != 'cancelled') or terminal not in TERMINAL:
            if marker and marker.is_file():
                await asyncio.to_thread(recover_animation, source)
                backup = source / ('.animation-' + folder.name)
                if backup.is_dir():
                    await asyncio.to_thread(remove_child, source, backup)
            error = ('Disk quota reached. Free space before retrying.' if over
                     else 'Server stopped during the animation update.')
            state = dict(state='error', stage='Animation update interrupted', progress=0, error=error)
        write(folder/'status.json', state)
        await self.trim_animation_operation(workspace, folder)
        self.store.finish_job(workspace.user_id, folder.name, state['state'])
        await self.usage(workspace.user_id)
        if workspace.active == folder.name:
            workspace.active = None

    async def settle(self, workspace):
        rows = [row for row in self.store.jobs(workspace.user_id) if row['state'] not in TERMINAL]
        if rows:
            async with workspace.lock:
                await self.reconcile(workspace, rows)

    async def reconcile(self, workspace, rows):
        user = self.store.get_user(workspace.user_id)
        for row in rows:
            job_id = row['id']
            if workspace.is_animation_operation(job_id):
                await self.reconcile_animation(workspace, row, workspace.job_folder(job_id))
                continue
            export = (workspace.exports / job_id).is_dir()
            folder = (workspace.exports if export else workspace.outputs) / job_id
            state = read(folder / 'status.json', {})
            process = workspace.studio.EXPORT_PROCESS if export else workspace.studio.PROCESS
            active = (workspace.studio.EXPORT_TASK if export else workspace.studio.ACTIVE) == job_id and process and process.poll() is None
            used = await self.usage(workspace.user_id) if active or state.get('state') in TERMINAL else user['disk_used_bytes']
            limit = user['disk_limit_bytes']
            over = limit is not None and used + max(0, user['storage_reserved_bytes'] - row['reserve_bytes']) > limit
            if active and not over:
                continue
            if active:
                stop = workspace.studio.stop_export if export else workspace.studio.stop_worker
                await asyncio.to_thread(stop)
            elif not over and state.get('state') == 'queued' and not export:
                continue
            if export:
                if workspace.studio.EXPORT_LOG and workspace.studio.EXPORT_TASK == job_id:
                    workspace.studio.EXPORT_LOG.close()
                    workspace.studio.EXPORT_LOG = None
                for name in ('sources', 'package', 'package.zip.tmp'):
                    await asyncio.to_thread(remove_child, folder, folder / name)
                used = await self.usage(workspace.user_id)
                over = limit is not None and used + max(0, user['storage_reserved_bytes'] - row['reserve_bytes']) > limit
            terminal = state.get('state', 'error')
            trim = False
            if not export and folder.is_dir():
                script, _, settings = workspace.job_spec(folder)
                trim = script in ('worker.py', 'pixal3d_worker.py') or settings.get('source_kind') == 'animation_copy'
            if over or terminal not in TERMINAL:
                if folder.is_dir():
                    error = 'Disk quota reached. Free space before retrying.' if over else 'Server stopped during the operation.'
                    write(folder / 'status.json', dict(state='error', stage='Operation interrupted', progress=0, error=error))
                    if export:
                        await asyncio.to_thread(remove_child, folder, folder / 'package.zip')
                    elif trim:
                        await self.trim_failed(workspace, folder)
                terminal = 'error'
            elif terminal != 'done' and trim:
                await self.trim_failed(workspace, folder)
            self.store.finish_job(workspace.user_id, job_id, terminal)
            await self.usage(workspace.user_id)
            if workspace.active == job_id:
                workspace.active = None

    async def run_once(self):
        reservations = defaultdict(list)
        for row in self.store.jobs():
            if row['state'] not in TERMINAL:
                reservations[row['user_id']].append(row)
        for identifier, rows in reservations.items():
            workspace = self.workspaces.get(identifier)
            if workspace and not workspace.lock.locked():
                async with workspace.lock:
                    await self.reconcile(workspace, rows)
        if self.paused or self.stopping or any(workspace.studio.PROCESS and workspace.studio.PROCESS.poll() is None for workspace in self.workspaces.values()):
            return
        ordered = list(self.workspaces)
        if self.current_user in ordered:
            position = ordered.index(self.current_user) + 1
            ordered = ordered[position:] + ordered[:position]
        for identifier in ordered:
            workspace = self.workspaces[identifier]
            if workspace.lock.locked() or self.store.get_user(identifier)['status'] != 'active':
                continue
            async with workspace.lock:
                eligible = {row['id'] for row in reservations[identifier] if row['state'] == 'reserved'}
                entry = next((item for item in workspace.pending() if item['id'] in eligible), None)
                if entry is None:
                    continue
                folder = workspace.job_folder(entry['id'])
                script, source_id, settings = workspace.job_spec(folder)
                if settings.get('source_kind') == 'refinish' and not source_id:
                    write(folder / 'status.json', dict(state='error', stage='Missing source cache', progress=0))
                    self.store.finish_job(identifier, entry['id'], 'error')
                    continue
                status = read(folder / 'status.json', {})
                status.update(state='running', stage='Starting server calculation', progress=1)
                write(folder / 'status.json', status)
                try:
                    self.store.mark_running(identifier, entry['id'])
                    workspace.launch(folder, script, source_id=source_id)
                except (OSError, StoreError):
                    LOG.exception('Could not launch worker for %s', entry['id'])
                    write(folder / 'status.json', dict(state='error', stage='Worker launch failed', progress=0))
                    if folder.parent == workspace.animation_operations:
                        await self.trim_animation_operation(workspace, folder)
                        await self.usage(identifier)
                    elif settings.get('source_kind') == 'animation_copy':
                        await self.trim_failed(workspace, folder)
                        await self.usage(identifier)
                    self.store.finish_job(identifier, entry['id'], 'error')
                    continue
                workspace.active = entry['id']
                self.current_user = identifier
                return

    async def run(self):
        while True:
            try:
                await self.run_once()
            except Exception:
                LOG.exception('Server queue reconciliation failed')
            await asyncio.sleep(1)

    def queue(self):
        items = []
        for row in self.store.jobs():
            if row['state'] in TERMINAL:
                continue
            user = self.store.get_user(row['user_id'])
            workspace = self.workspaces.get(row['user_id'])
            if workspace is None:
                continue
            job = workspace.job_folder(row['id'])
            if not job.is_dir():
                job = workspace.exports / row['id']
            status = read(job / 'status.json', {})
            name = read(job/'operation.json', {}).get('name')
            if not name:
                name = read(job / 'metadata.json', {}).get('display_name', read(job / 'settings.json', {}).get('name', 'Export'))
            items.append(dict(id=row['id'], user_id=user['id'], user_name=f"{user['first_name']} {user['last_name']}",
                              name=name, state=status.get('state', 'queued'), stage=status.get('stage', ''), progress=status.get('progress', 0)))
        return dict(paused=self.paused, items=items)
