"""Opt-in LAN edition of TRELLIS Studio."""
import argparse
import asyncio
import base64
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from getpass import getpass
import hmac
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
import uvicorn

from server_runtime import ROOT, ServerRuntime, remove_child, tree_bytes
from server_store import DEFAULT_DISK_LIMIT, SESSION_SECONDS, Store, StoreError
from studio_version import VERSION
from studio_workspace import ID, read, write


SESSION = 'trellis_server_session'
PROFILE = {'first_name', 'last_name', 'email', 'bio', 'website', 'social_links'}
UNSAFE = {'POST', 'PATCH', 'PUT', 'DELETE'}
UPLOAD_MAX = 800 * 1024**2
AVATAR_MAX = 2 * 1024**2
PROTECTED = {'/api/shutdown', '/api/v1/client-config'}


async def json_body(request):
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > 32768:
            raise HTTPException(413, 'Request is too large.')
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError) as error:
        raise HTTPException(400, 'Expected a JSON object.') from error


def profile(data):
    return {key: value for key, value in data.items() if key in PROFILE}


def user_view(user):
    avatar = f"/server/api/avatar/{user['id']}?v={user['avatar']}" if user['avatar'] else ''
    links = user['social_links']
    if isinstance(links, list):
        links = {'other': links[0]} if links else {}
    return {**user, 'avatar': avatar, 'social_links': links, 'disk_limit': user['disk_limit_bytes'],
            'used_bytes': user['disk_used_bytes'], 'mfa_enabled': user['totp_enabled'],
            'mfa_setup_required': user['role'] == 'admin' and not user['totp_enabled']}


def bearer(request):
    if not request.url.path.startswith('/api/v1/'):
        return None
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    return token if scheme.lower() == 'bearer' else None


def totp_qr_data_url(uri):
    try:
        import cv2
    except ImportError:
        return None

    params = cv2.QRCodeEncoder_Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    try:
        modules = cv2.QRCodeEncoder_create(params).encode(uri)
    except cv2.error:
        return None

    image = ImageOps.expand(Image.fromarray(modules), border=4, fill=255)
    image = image.resize((image.width * 5, image.height * 5), Image.Resampling.NEAREST)
    with io.BytesIO() as output:
        image.save(output, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')


class RateLimit:
    def __init__(self):
        self.attempts = defaultdict(deque)

    def check(self, group, request, limit, window):
        now = time.monotonic()
        address = request.client.host if request.client else ''
        if len(self.attempts) > 2048:
            self.attempts = defaultdict(deque, {key: values for key, values in self.attempts.items()
                                                if values and values[-1] > now - 3600})
            if len(self.attempts) > 2048:
                raise HTTPException(429, 'Too many requests. Try again later.')
        values = self.attempts[group, address]
        while values and values[0] < now - window:
            values.popleft()
        if len(values) >= limit:
            raise HTTPException(429, 'Too many attempts. Try again later.')
        values.append(now)


class ScopedResponse(Response):
    def __init__(self, studio, request):
        super().__init__()
        self.studio = studio
        self.request = request

    async def __call__(self, scope, receive, send):
        inner = {**scope, 'trellis.server': True, 'extensions': {}}
        async def private_send(message):
            if message['type'] == 'http.response.start':
                headers = [(k, v) for k, v in message['headers'] if k.lower() not in (b'cache-control', b'set-cookie')]
                message = {**message, 'headers': [*headers, (b'cache-control', b'private, no-store'),
                                                 (b'x-content-type-options', b'nosniff')]}
            await send(message)
        await self.studio(inner, self.request.receive, private_send)

    async def buffer(self):
        status = 500
        headers = []
        data = bytearray()

        async def collect(message):
            nonlocal status, headers
            if message['type'] == 'http.response.start':
                status = message['status']
                headers = message['headers']
            elif message['type'] == 'http.response.body':
                data.extend(message.get('body', b''))
                if len(data) > 4 * 1024**2:
                    raise HTTPException(500, 'Unexpected response size.')

        await self(self.request.scope, self.request.receive, collect)
        result = Response(bytes(data), status_code=status)
        result.raw_headers = headers
        return result


def app_for(data, public_url, setup_token=None, on_setup=None):
    data = Path(data).resolve()
    store = Store(data / 'server.sqlite3')
    runtime = ServerRuntime(data, store)
    limiter = RateLimit()
    origin = public_url.rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('Configure the public HTTP or HTTPS origin without a path.')

    @asynccontextmanager
    async def lifespan(_):
        if setup_token is None:
            if not store.has_admin():
                raise RuntimeError('Create the initial administrator through the installer first.')
            store.clear_storage_reservations()
            await runtime.start()
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(title='TRELLIS Server', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.runtime = runtime

    @app.middleware('http')
    async def protect(request, call_next):
        path = request.url.path
        host = request.headers.get('host', '').lower()
        local_health = path == '/server/api/health' and request.client and request.client.host in ('127.0.0.1', '::1')
        if host != parsed.netloc.lower() and not local_health:
            return JSONResponse({'detail': 'Use the configured server address.'}, status_code=400)
        if setup_token is not None and path not in ('/server/setup', '/server/api/setup', '/server/api/health') and not path.startswith('/web/'):
            return JSONResponse({'detail': 'Finish server installation first.'}, status_code=503)
        if request.method in UNSAFE:
            supplied = request.headers.get('origin')
            same_site = request.headers.get('sec-fetch-site')
            explicit = request.headers.get('x-trellis-request') == '1' or bearer(request) is not None
            if supplied and supplied != origin or same_site in ('cross-site', 'same-site') or not (explicit or supplied == origin or same_site == 'same-origin'):
                return JSONResponse({'detail': 'Request origin rejected.'}, status_code=403)
            raw_size = request.headers.get('content-length', '0')
            limit = AVATAR_MAX + 32768 if path == '/server/api/profile/avatar' else 32768 if path.startswith('/server/api/') else UPLOAD_MAX
            if not raw_size.isdecimal() or len(raw_size) > 12 or int(raw_size) > limit:
                return JSONResponse({'detail': 'Request is too large.'}, status_code=413)
            if request.headers.get('transfer-encoding'):
                return JSONResponse({'detail': 'Content-Length is required.'}, status_code=411)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if path in ('/', '/developers') or path.startswith(('/api/', '/server/')):
            response.headers['Cache-Control'] = 'private, no-store'
        return response

    @app.exception_handler(StoreError)
    async def store_error(_, error):
        return JSONResponse({'detail': str(error), 'code': error.code}, status_code=error.status)

    def actor(request, admin=False, ready=True):
        token = bearer(request)
        user = store.authenticate_api(token) if token is not None else store.authenticate(request.cookies.get(SESSION))
        if admin and user['role'] != 'admin':
            raise StoreError('Administrator access required.', 403, 'admin_required')
        if ready and user['password_change_required']:
            raise StoreError('Change your temporary password first.', 403, 'password_change_required')
        if ready and user['role'] == 'admin' and not user['totp_enabled']:
            raise StoreError('Configure double authentication for this administrator.', 403, 'mfa_setup_required')
        return user

    def portal():
        return FileResponse(ROOT / 'web' / 'server.html', media_type='text/html')

    @app.get('/server/api/health')
    def health():
        return dict(service='trellis-server', version=VERSION, setup_complete=store.has_admin())

    @app.get('/server/setup')
    def setup_page():
        if setup_token is None:
            raise HTTPException(404)
        return portal()

    @app.post('/server/api/setup', status_code=201)
    async def setup(request: Request):
        if not setup_token or not request.client or request.client.host not in ('127.0.0.1', '::1'):
            raise HTTPException(404)
        limiter.check('setup', request, 10, 300)
        fields = await json_body(request)
        token = fields.get('token', '')
        if not isinstance(token, str) or not hmac.compare_digest(token.encode(), setup_token.encode()):
            raise HTTPException(403, 'This installation link is invalid.')
        user = await asyncio.to_thread(store.bootstrap_admin, profile(fields), fields.get('password'))
        if on_setup:
            asyncio.get_running_loop().call_later(1.0, on_setup)
        return {'user': user_view(user)}

    @app.get('/server/login')
    def login_page():
        return portal()

    @app.post('/server/api/register', status_code=201)
    async def register(request: Request):
        limiter.check('register', request, 8, 3600)
        fields = await json_body(request)
        await asyncio.to_thread(store.register, profile(fields), fields.get('password'))
        return {'status': 'pending'}

    @app.post('/server/api/login')
    async def login(request: Request):
        limiter.check('login', request, 20, 300)
        fields = await json_body(request)
        result = await asyncio.to_thread(store.login, fields.get('email'), fields.get('password'), fields.get('code', ''))
        response = JSONResponse({'user': user_view(result['user'])})
        response.set_cookie(SESSION, result['token'], httponly=True, secure=parsed.scheme == 'https',
                            samesite='strict', max_age=SESSION_SECONDS, path='/')
        return response

    @app.post('/server/api/logout')
    async def logout(request: Request):
        token = request.cookies.get(SESSION)
        if token:
            store.logout(token)
        response = JSONResponse({'ok': True})
        response.delete_cookie(SESSION, path='/')
        return response

    @app.get('/server/account')
    def account(request: Request):
        try:
            actor(request, ready=False)
        except StoreError:
            return RedirectResponse('/server/login')
        return portal()

    @app.get('/server/admin')
    def admin_page(request: Request):
        try:
            user = actor(request, admin=True, ready=False)
        except StoreError as error:
            if error.status == 401:
                return RedirectResponse('/server/login')
            raise
        if user['password_change_required'] or not user['totp_enabled']:
            return RedirectResponse('/server/account#security')
        return portal()

    @app.get('/server/api/me')
    async def me(request: Request):
        user = actor(request, ready=False)
        await runtime.usage(user['id'])
        return dict(user=user_view(store.get_user(user['id'])), server=dict(name='TRELLIS Server', version=VERSION, setup_complete=True))

    @app.get('/server/api/credits')
    def credits(request: Request):
        return {'credits_available': actor(request)['credits_available']}

    @app.patch('/server/api/me')
    async def update_me(request: Request):
        user = actor(request)
        fields = await json_body(request)
        return {'user': user_view(store.update_profile(user['id'], fields))}

    @app.post('/server/api/password')
    async def change_password(request: Request):
        user = actor(request, ready=False)
        fields = await json_body(request)
        limiter.check('password', request, 12, 300)
        await asyncio.to_thread(store.change_password, user['id'], fields.get('current_password'), fields.get('new_password'))
        return {'ok': True}

    @app.post('/server/api/mfa/setup')
    async def mfa_setup(request: Request):
        user = actor(request, ready=False)
        if user['password_change_required']:
            raise StoreError('Change your temporary password first.', 403)
        fields = await json_body(request)
        limiter.check('mfa_setup', request, 12, 300)
        setup = await asyncio.to_thread(store.begin_totp, user['id'], fields.get('password'))
        setup['qr_data_url'] = await asyncio.to_thread(totp_qr_data_url, setup['uri'])
        return setup

    @app.post('/server/api/mfa/enable')
    async def mfa_enable(request: Request):
        user = actor(request, ready=False)
        fields = await json_body(request)
        limiter.check('mfa_enable', request, 12, 300)
        return {'recovery_codes': store.confirm_totp(user['id'], fields.get('code'))}

    @app.post('/server/api/mfa/disable')
    async def mfa_disable(request: Request):
        user = actor(request)
        if user['role'] == 'admin':
            raise StoreError('Administrators must use double authentication.', 403)
        fields = await json_body(request)
        limiter.check('mfa_disable', request, 12, 300)
        await asyncio.to_thread(store.disable_totp, user['id'], fields.get('password'), fields.get('code'))
        return {'ok': True}

    @app.post('/server/api/profile/avatar')
    async def avatar_upload(request: Request):
        user = actor(request)
        async with request.form(max_files=1, max_fields=0) as form:
            file = form.get('file') or form.get('avatar')
            if not hasattr(file, 'read'):
                raise HTTPException(400, 'Choose a JPEG, PNG or WebP avatar.')
            content = await file.read(AVATAR_MAX + 1)
        if not content or len(content) > AVATAR_MAX:
            raise HTTPException(413, 'The avatar must be smaller than 2 MiB.')
        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width * image.height > 16_000_000:
                    raise ValueError()
                avatar = ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'), (256, 256))
                out = io.BytesIO()
                avatar.save(out, format='PNG')
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
            raise HTTPException(400, 'Choose a valid avatar image.') from error
        workspace = await runtime.workspace(user['id'])
        async with workspace.lock:
            parent = workspace.directory / 'avatars'
            parent.mkdir(exist_ok=True)
            filename = uuid.uuid4().hex + '.png'
            target = parent / filename
            previous = store.get_user(user['id'])['avatar']
            old = parent / previous
            prior_bytes = old.stat().st_size if previous and old.is_file() else 0
            used = await runtime.usage(user['id'])
            current = store.get_user(user['id'])
            if current['disk_limit_bytes'] is not None and used - prior_bytes + out.tell() + current['storage_reserved_bytes'] > current['disk_limit_bytes']:
                raise StoreError('The avatar would exceed your disk quota.', 413, 'disk_quota')
            target.write_bytes(out.getvalue())
            try:
                updated = store.set_avatar(user['id'], filename)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            if previous:
                old.unlink(missing_ok=True)
            await runtime.usage(user['id'])
        return {'user': user_view(store.get_user(updated['id']))}

    @app.get('/server/api/avatar/{identifier}')
    def avatar_file(identifier: str, request: Request):
        user = actor(request, ready=False)
        if not ID.fullmatch(identifier) or identifier != user['id'] and user['role'] != 'admin':
            raise HTTPException(404)
        if identifier != user['id']:
            actor(request, admin=True)
        filename = store.get_user(identifier)['avatar']
        target = runtime.user_path(identifier) / 'avatars' / filename
        if not re.fullmatch(r'[a-f0-9]{32}\.png', filename) or not target.is_file() or target.resolve() != target:
            raise HTTPException(404)
        return FileResponse(target, media_type='image/png')

    @app.get('/server/api/usage')
    async def usage(request: Request):
        user = actor(request)
        directory = runtime.user_path(user['id'])
        await runtime.usage(user['id'])

        def inventory(label, parent):
            result = []
            if parent.is_dir():
                for folder in parent.iterdir():
                    if not ID.fullmatch(folder.name) or folder.resolve() != folder or not folder.is_dir():
                        continue
                    state = read(folder / 'status.json', {})
                    if label == 'exports' and state.get('state') in ('queued', 'running'):
                        continue
                    metadata = read(folder / ('upload.json' if label == 'uploads' else 'metadata.json'), {})
                    name = metadata.get('display_name', metadata.get('name', state.get('target', folder.name[:8])))
                    result.append(dict(id=folder.name, name=name, bytes=tree_bytes(folder)))
            return result

        trash, exports, uploads, outputs, caches = await asyncio.gather(
            asyncio.to_thread(inventory, 'trash', directory / 'trash'),
            asyncio.to_thread(inventory, 'exports', directory / 'exports'),
            asyncio.to_thread(inventory, 'uploads', directory / 'state' / 'uploads'),
            asyncio.to_thread(tree_bytes, directory / 'outputs'),
            asyncio.to_thread(tree_bytes, directory / 'outputs', caches_only=True),
        )
        user = store.get_user(user['id'])
        breakdown = dict(models=max(0, outputs-caches), caches=caches, exports=sum(item['bytes'] for item in exports),
                         trash=sum(item['bytes'] for item in trash))
        breakdown['other'] = max(0, user['disk_used_bytes'] - sum(breakdown.values()))
        return dict(user=user_view(user), ledger=store.user_ledger(user['id']), trash=trash, exports=exports, uploads=uploads, breakdown=breakdown)

    @app.get('/server/api/tokens')
    def list_tokens(request: Request):
        user = actor(request)
        return {'tokens': store.list_api_tokens(user['id'])}

    @app.post('/server/api/tokens', status_code=201)
    async def create_token(request: Request):
        user = actor(request)
        fields = await json_body(request)
        key = store.create_api_token(user['id'], fields.get('label'))
        return {'token': key.pop('token'), 'key': key}

    @app.delete('/server/api/tokens/{identifier}')
    def revoke_token(identifier: str, request: Request):
        user = actor(request)
        store.revoke_api_token(user['id'], identifier)
        return {'ok': True}

    @app.delete('/server/api/{collection}/{identifier}')
    async def purge(collection: str, identifier: str, request: Request):
        user = actor(request)
        if collection not in ('trash', 'exports', 'uploads') or not ID.fullmatch(identifier):
            raise HTTPException(404)
        workspace = await runtime.workspace(user['id'])
        parent = workspace.state / 'uploads' if collection == 'uploads' else workspace.directory / collection
        folder = parent / identifier
        async with workspace.lock:
            if not folder.is_dir() or folder.resolve() != folder:
                raise HTTPException(404)
            if collection == 'exports' and any(row['id'] == identifier and row['state'] in ('reserved', 'running') for row in store.jobs(user['id'])):
                raise HTTPException(409, 'Wait for the export to finish.')
            if collection == 'trash':
                for path in workspace.outputs.glob('*/settings.json'):
                    if read(path, {}).get('finish_source_id') == identifier:
                        raise HTTPException(409, 'Restore the master or remove the variants that still use its cache.')
            await asyncio.to_thread(remove_child, parent, folder)
            await runtime.usage(user['id'])
        return {'ok': True}

    @app.get('/server/api/admin/users')
    def list_users(request: Request):
        user = actor(request, admin=True)
        return {'users': [user_view(entry) for entry in store.list_users(user['id'])]}

    @app.post('/server/api/admin/users', status_code=201)
    async def create_user(request: Request):
        user = actor(request, admin=True)
        fields = await json_body(request)
        if set(fields) - PROFILE - {'password', 'disk_limit_bytes', 'disk_limit', 'credits'}:
            raise HTTPException(400, 'Unknown account fields.')
        result = await asyncio.to_thread(store.create_user, user['id'], profile(fields), fields.get('password'),
                                         fields.get('disk_limit_bytes', fields.get('disk_limit', DEFAULT_DISK_LIMIT)), fields.get('credits', 0))
        return {**result, 'user': user_view(result['user'])}

    @app.patch('/server/api/admin/users/{identifier}')
    async def update_user(identifier: str, request: Request):
        user = actor(request, admin=True)
        fields = await json_body(request)
        if 'disk_limit' in fields:
            fields['disk_limit_bytes'] = fields.pop('disk_limit')
        return {'user': user_view(store.update_user(user['id'], identifier, fields))}

    @app.post('/server/api/admin/users/{identifier}/credits')
    async def credits(identifier: str, request: Request):
        user = actor(request, admin=True)
        fields = await json_body(request)
        return {'user': user_view(store.update_user(user['id'], identifier, {'credit_delta': fields.get('amount'), 'reason': fields.get('reason')}))}

    @app.delete('/server/api/admin/users/{identifier}/sessions')
    def revoke_sessions(identifier: str, request: Request):
        user = actor(request, admin=True)
        store.revoke_sessions(user['id'], identifier)
        return {'ok': True}

    @app.post('/server/api/admin/users/{identifier}/reset-password')
    async def reset_password(identifier: str, request: Request):
        user = actor(request, admin=True)
        return {'temporary_password': await asyncio.to_thread(store.reset_password, user['id'], identifier)}

    @app.post('/server/api/admin/users/{identifier}/reset-mfa')
    def reset_mfa(identifier: str, request: Request):
        user = actor(request, admin=True)
        store.reset_totp(user['id'], identifier)
        return {'ok': True}

    @app.get('/server/api/admin/audit')
    def audit(request: Request):
        user = actor(request, admin=True)
        names = {entry['id']: entry['first_name'] + ' ' + entry['last_name'] for entry in store.list_users(user['id'])}
        return {'entries': [{**entry, 'actor_name': names.get(entry['actor_id'], 'Serveur'), 'subject_name': names.get(entry['target_id'], '')}
                            for entry in store.audit(user['id'])]}

    @app.get('/server/api/admin/status')
    def admin_status(request: Request):
        user = actor(request, admin=True)
        users = store.list_users(user['id'])
        queue = runtime.queue()
        return dict(users=len(users), pending_users=sum(u['status'] == 'pending' for u in users),
                    used_bytes=sum(u['disk_used_bytes'] for u in users), free_bytes=shutil.disk_usage(data).free,
                    active_jobs=sum(item['state'] == 'running' for item in queue['items']), paused=queue['paused'])

    @app.get('/server/api/admin/queue')
    def admin_queue(request: Request):
        actor(request, admin=True)
        return runtime.queue()

    @app.patch('/server/api/admin/queue')
    async def pause_queue(request: Request):
        actor(request, admin=True)
        fields = await json_body(request)
        if type(fields.get('paused')) is not bool:
            raise HTTPException(400, 'Choose pause or resume.')
        runtime.set_paused(fields['paused'])
        return runtime.queue()

    @app.post('/server/api/admin/queue/{identifier}/{job_id}/cancel')
    async def cancel_queue(identifier: str, job_id: str, request: Request):
        actor(request, admin=True)
        if not ID.fullmatch(job_id) or not ID.fullmatch(identifier) or not any(row['id'] == job_id and row['state'] in ('reserved', 'running') for row in store.jobs(identifier)):
            raise HTTPException(404)
        workspace = await runtime.workspace(identifier)
        async with workspace.lock:
            if (workspace.exports / job_id).is_dir():
                await workspace.studio.cancel_export(job_id)
            else:
                await workspace.studio.cancel(job_id)
        return {'ok': True}

    @app.get('/')
    def studio_page(request: Request):
        try:
            user = actor(request, ready=False)
        except StoreError:
            return RedirectResponse('/server/login')
        if user['password_change_required'] or user['role'] == 'admin' and not user['totp_enabled']:
            return RedirectResponse('/server/account#security')
        html = (ROOT / 'web' / 'studio.html').read_text(encoding='utf-8')
        shell = '<link rel="stylesheet" href="/web/server.css"><script type="module" src="/web/server-shell.js"></script>'
        return HTMLResponse(html.replace('</head>', shell + '</head>', 1))

    @app.api_route('/api/{path:path}', methods=['GET', 'HEAD', 'POST', 'PATCH', 'PUT', 'DELETE'])
    async def studio_api(path: str, request: Request):
        user = actor(request)
        route = '/api/' + path
        if route in PROTECTED or path.startswith(('rig-pilot', 'transfers/')) or path.endswith('/files/Blender.zip'):
            raise HTTPException(404, 'This operation is unavailable in server mode.')
        if path.startswith('v1/transfers/'):
            raise HTTPException(404, 'Restore collections from the server console.')
        if request.method == 'POST' and re.fullmatch(r'(jobs/[a-f0-9]{32}/(animations|prepare/motion)|v1/models/[a-f0-9]{32}/(animations|motion-edits))', path):
            raise HTTPException(409, 'In-place animation editing is available in the desktop edition.')
        workspace = await runtime.workspace(user['id'])
        scoped = ScopedResponse(workspace.studio.app, request)
        writes_inspection = request.method == 'GET' and re.fullmatch(r'(jobs/[a-f0-9]{32}/(inspect|diagnostics)|v1/models/[a-f0-9]{32}/diagnostics)', path)
        if not writes_inspection and request.method not in UNSAFE:
            published = re.fullmatch(r'(v1/)?(jobs|models|exports)/([a-f0-9]{32})/(files/(model\.(glb|stl|blend)|Unity\.zip)|download)', path)
            if published:
                await runtime.settle(workspace)
                job = next((row for row in store.jobs(user['id']) if row['id'] == published[3]), None)
                if job and job['state'] != 'done':
                    raise HTTPException(409, 'This result is not ready to download.')
            return scoped
        if writes_inspection and request.headers.get('sec-fetch-site') in ('cross-site', 'same-site'):
            raise HTTPException(403, 'Request origin rejected.')
        reclaim = request.method == 'DELETE' or path.endswith('/cancel') or path == 'library/trash' or re.fullmatch(r'(v1/)?trash/[a-f0-9]{32}/restore', path)
        length = int(request.headers.get('content-length', '0'))
        match = re.fullmatch(r'jobs/([a-f0-9]{32})/presentation/library-turntable', path)
        if match and request.method == 'POST':
            clip = workspace.outputs / match[1] / 'Presentation' / 'turntable.webm'
            if clip.is_file():
                length += clip.stat().st_size
        return await runtime.submit(user, workspace, route, length, scoped.buffer, reclaim)

    @app.get('/developers')
    def developers(request: Request):
        actor(request)
        html = (ROOT / 'web' / 'developers.html').read_text(encoding='utf-8')
        return HTMLResponse(html.replace('<body>', '<body data-edition="server">', 1))

    app.mount('/web', StaticFiles(directory=ROOT / 'web'), name='web')
    app.mount('/vendor', StaticFiles(directory=ROOT / 'node_modules' / 'three'), name='vendor')
    app.mount('/reference-assets', StaticFiles(directory=ROOT / 'node_modules' / 'swagger-ui-dist'), name='reference-assets')
    return app


@contextmanager
def exclusive_instance(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'instance.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'\0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('A TRELLIS Server instance is already using these data.') from error
        yield


async def serve(server, runtime, directory):
    async def stop_requests():
        request = directory / 'stop.request'
        while not server.should_exit:
            if request.is_file():
                nonce = request.read_text(encoding='utf-8-sig').strip()
                request.unlink(missing_ok=True)
                if re.fullmatch(r'[a-fA-F0-9-]{16,80}', nonce):
                    busy = runtime.busy()
                    if not busy:
                        runtime.stopping = True
                    write(directory / 'stop.response', dict(nonce=nonce, stopped=not busy, busy=busy))
                    if not busy:
                        server.should_exit = True
            await asyncio.sleep(.5)

    monitor = asyncio.create_task(stop_requests())
    try:
        await server.serve()
    finally:
        monitor.cancel()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--setup-only', action='store_true')
    parser.add_argument('--setup-token-file', type=Path)
    parser.add_argument('--recover-admin', metavar='EMAIL')
    args = parser.parse_args()
    settings = read(args.config, {}) if args.config else {}
    directory = Path(args.data_dir or settings.get('data_dir') or ROOT / 'server-data').resolve()
    if ROOT.is_relative_to(directory) or any(directory.is_relative_to(ROOT / name) for name in ('outputs', 'trash', 'exports', 'web', 'config', 'models', 'engine', '.venv')):
        parser.error('Choose a separate server data folder.')
    host = args.host or settings.get('host', '127.0.0.1')
    port = args.port or settings.get('port', 7862)
    tls_cert, tls_key = settings.get('tls_cert'), settings.get('tls_key')
    if bool(tls_cert) != bool(tls_key):
        parser.error('Provide both the HTTPS certificate and its private key.')
    scheme = 'https' if tls_cert else 'http'
    public_url = settings.get('public_url', f'{scheme}://{host}:{port}')
    if not 1 <= port <= 65535:
        parser.error('Use a valid port.')
    if args.setup_only:
        if args.setup_token_file is None:
            parser.error('Setup requires a local installer token.')
        host, tls_cert, tls_key, public_url = '127.0.0.1', None, None, f'http://127.0.0.1:{port}'
    else:
        loopback = host == 'localhost' or ipaddress.ip_address(host).is_loopback
        if not loopback and not tls_cert:
            parser.error('LAN access requires an HTTPS certificate and key.')
        if urlsplit(public_url).scheme != scheme:
            parser.error('The server address and HTTPS settings do not match.')
    with exclusive_instance(directory):
        store = Store(directory / 'server.sqlite3')
        if args.recover_admin:
            new = getpass('New administrator password: ')
            if new != getpass('Repeat password: '):
                parser.error('The passwords do not match.')
            store.recover_admin(args.recover_admin, new)
            print('Administrator password reset. Enroll double authentication at the next sign-in.')
            return
        if args.setup_only and store.has_admin():
            return
        if not args.setup_only and not store.has_admin():
            parser.error('Install and create the initial administrator before starting the server.')
        if not args.setup_only and not loopback and not any(user['role'] == 'admin' and user['status'] == 'active'
                                                          and not user['password_change_required'] for user in store.all_users()):
            parser.error('Change the initial administrator password locally before enabling LAN access.')
        token = args.setup_token_file.read_text(encoding='utf-8').strip() if args.setup_only else None
        if args.setup_only and (len(token) < 32 or len(token) > 200):
            parser.error('Invalid installer token.')
        app = app_for(directory, public_url, token, lambda: setattr(server, 'should_exit', True))
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, ssl_certfile=tls_cert, ssl_keyfile=tls_key,
                                               proxy_headers=False, workers=1))
        if args.setup_only:
            server.run()
        else:
            (directory / 'stop.request').unlink(missing_ok=True)
            asyncio.run(serve(server, app.state.runtime, directory))


if __name__ == '__main__':
    main()
