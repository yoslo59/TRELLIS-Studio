"""Exercise the actual LAN application and its isolated Studio routes without GPU inference."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import base64
import hashlib
import hmac
import io
import json
import socket
import struct
import sys
import threading
import time

from PIL import Image
import requests
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server_app import app_for
from studio_workspace import write


PASSWORD = 'TRELLIS isolated fixture 77'
HEADERS = {'X-Trellis-Request': '1'}
MIB = 1024**2


@contextmanager
def serve(data, setup_token=None):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    app = app_for(data, base, setup_token)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(150):
            if server.started:
                break
            time.sleep(.04)
        assert server.started
        yield base, app.state
    finally:
        server.should_exit = True
        thread.join(8)
        assert not thread.is_alive()


def call(base, method, path, expected=200, client=requests, **kwargs):
    headers = {**HEADERS, **kwargs.pop('headers', {})}
    result = client.request(method, base + path, headers=headers, timeout=20, **kwargs)
    assert result.status_code == expected, (method, path, result.status_code, result.text[:300])
    return result


def login(base, name, code='', expected=200):
    session = requests.Session()
    result = call(base, 'POST', '/server/api/login', expected, session,
                  json=dict(email=f'{name}@example.test', password=PASSWORD, code=code))
    return session, result.json()


def account(name):
    return dict(first_name=name, last_name='Test', email=f'{name}@example.test', password=PASSWORD)


def totp(secret):
    digest = hmac.new(base64.b32decode(secret), struct.pack('>Q', int(time.time() // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f'{(int.from_bytes(digest[offset:offset+4], "big") & 0x7fffffff) % 1000000:06d}'


def eventually(predicate):
    for _ in range(70):
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError('Server did not reach the expected state')


def run():
    with TemporaryDirectory(prefix='trellis-server-test-') as folder:
        data = Path(folder).resolve()
        token = 'fixture-token-for-local-installation-only'
        with serve(data, token) as (base, _):
            call(base, 'POST', '/server/api/setup', 403, json={**account('admin'), 'token': 'incorrect'})
            admin_user = call(base, 'POST', '/server/api/setup', 201, json={**account('admin'), 'token': token}).json()['user']
            assert admin_user['role'] == 'admin' and admin_user['disk_limit'] is None and admin_user['credits_available'] is None
            call(base, 'POST', '/server/api/setup', 409, json={**account('other'), 'token': token})
        with serve(data) as (base, state):
            store = state.store
            call(base, 'GET', '/api/jobs', 401)
            call(base, 'POST', '/server/api/setup', 404, json={**account('other'), 'token': token})
            admin, _ = login(base, 'admin')
            call(base, 'GET', '/server/api/admin/users', 403, admin)
            secret = call(base, 'POST', '/server/api/mfa/setup', client=admin, json={'password': PASSWORD}).json()['secret']
            codes = call(base, 'POST', '/server/api/mfa/enable', client=admin, json={'code': totp(secret)}).json()['recovery_codes']
            call(base, 'GET', '/server/api/me', 401, admin)
            login(base, 'admin', expected=401)
            admin, _ = login(base, 'admin', codes[0])
            for name in ('alice', 'bob'):
                call(base, 'POST', '/server/api/register', 201, json=account(name))
                login(base, name, expected=403)
            users = call(base, 'GET', '/server/api/admin/users', client=admin).json()['users']
            alice_id = next(user['id'] for user in users if user['email'] == 'alice@example.test')
            bob_id = next(user['id'] for user in users if user['email'] == 'bob@example.test')
            for identifier in (alice_id, bob_id):
                call(base, 'PATCH', '/server/api/admin/users/' + identifier, client=admin,
                     json=dict(status='active', disk_limit_bytes=128 * MIB, credit_delta=1, reason='Fixture'))
            alice, _ = login(base, 'alice')
            bob, _ = login(base, 'bob')
            call(base, 'GET', '/server/api/admin/users', 403, alice)
            call(base, 'PATCH', '/server/api/admin/users/' + alice_id, 403, alice, json={'disk_limit_bytes': 2**32})
            call(base, 'PATCH', '/server/api/me', 400, alice, json={'credits': 100})
            call(base, 'PATCH', '/server/api/me', 403, alice, headers={'Origin': 'https://unrelated.example.test'}, json={'bio': 'Rejected'})
            saved = call(base, 'PATCH', '/server/api/me', client=alice, json={'bio': 'Mon atelier', 'social_links': {'github': 'https://github.com/test'}}).json()['user']
            assert saved['bio'] == 'Mon atelier' and saved['social_links']['github'].startswith('https://')

            call(base, 'PATCH', '/server/api/admin/queue', client=admin, json={'paused': True})
            stream = io.BytesIO()
            Image.new('RGB', (32, 32), '#e0bb90').save(stream, format='PNG')
            png = stream.getvalue()
            call(base, 'POST', '/server/api/profile/avatar', client=alice, files={'file': ('avatar.png', png, 'image/png')})
            call(base, 'GET', '/server/api/avatar/' + alice_id, client=alice)
            call(base, 'GET', '/server/api/avatar/' + alice_id, 404, bob)
            request_data = dict(files={'image': ('ref.png', png, 'image/png')}, data={'name': 'Alice’s mesh'})
            cookies = alice.cookies.get_dict()
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda _: requests.post(base + '/api/generate', cookies=cookies,
                                                                 headers=HEADERS, timeout=20, **request_data), range(2)))
            assert sorted(r.status_code for r in results) == [200, 402], [(r.status_code, r.text) for r in results]
            identifier = next(r.json()['id'] for r in results if r.ok)
            alice_path = data / 'users' / alice_id / 'outputs'
            assert [path.name for path in alice_path.iterdir()] == [identifier]
            call(base, 'PATCH', '/api/queue/' + identifier, client=alice, json={'priority': 1})
            call(base, 'GET', '/api/jobs/' + identifier, client=alice)
            for path in ('/api/jobs/' + identifier, '/api/jobs/' + identifier + '/files/input.png', '/api/v1/jobs/' + identifier):
                call(base, 'GET', path, 404, bob)
            call(base, 'POST', '/api/jobs/' + identifier + '/cancel', 404, bob)
            assert call(base, 'GET', '/api/jobs', client=bob).json() == []
            call(base, 'POST', '/api/shutdown', 404, alice)
            call(base, 'PATCH', '/server/api/admin/users/' + bob_id, client=admin, json={'disk_limit_bytes': 0})
            call(base, 'POST', '/api/generate', 413, bob, **request_data)
            assert not list((data / 'users' / bob_id / 'outputs').iterdir())

            workspace = state.runtime.workspaces[alice_id]
            class FixtureProcess:
                completed = False
                def poll(self):
                    return 0 if self.completed else None
            process = FixtureProcess()
            def fake_launch(path, script, source_id=None):
                workspace.studio.PROCESS = process
                workspace.studio.ACTIVE = path.name
            workspace.launch = fake_launch
            try:
                call(base, 'PATCH', '/server/api/admin/queue', client=admin, json={'paused': False})
                eventually(lambda: store.jobs(alice_id)[0]['state'] == 'running')
                write(alice_path / identifier / 'status.json', {'state': 'done', 'stage': 'Fixture complete', 'progress': 100})
            finally:
                process.completed = True
            eventually(lambda: store.jobs(alice_id)[0]['state'] == 'done')
            assert store.get_user(alice_id)['credits'] == 0
            assert len([row for row in store.user_ledger(alice_id) if row['delta'] == -1]) == 1

            key = call(base, 'POST', '/server/api/tokens', 201, alice, json={'label': 'Fixture client'}).json()
            auth = {'Authorization': 'Bearer ' + key['token']}
            call(base, 'GET', '/api/v1/jobs/' + identifier, headers=auth)
            call(base, 'GET', '/api/jobs/' + identifier, 401, headers=auth)
            call(base, 'GET', '/server/api/admin/users', 401, headers=auth)
            upload = call(base, 'POST', '/api/v1/uploads', 201, headers=auth,
                          files={'file': ('reference.png', png, 'image/png')}).json()['id']
            call(base, 'POST', '/api/v1/generations', 402, headers=auth, json={'upload_id': upload})
            assert [path.name for path in alice_path.iterdir()] == [identifier]
            call(base, 'DELETE', '/server/api/tokens/' + key['key']['id'], 404, bob)

            reference = {'upload_id': upload, 'remove_bg': False,
                         'reference_edits': {'front': {'crop': [.05, .05, .95, .95]}}}
            preview_url = '/api/v1/references/preview'
            preview = call(base, 'POST', preview_url, headers=auth, json=reference).json()
            assert preview['engine'] == 'trellis2' and [view['role'] for view in preview['views']] == ['front']
            view = preview['views'][0]
            for field in ('original', 'cutout', 'framed'):
                kind, content = view[field].split(',', 1)
                assert kind == 'data:image/png;base64'
                with Image.open(io.BytesIO(base64.b64decode(content))) as frame:
                    assert max(frame.size) <= 240
            call(base, 'POST', preview_url, 400, headers=auth, json={**reference, 'left_upload_id': upload})
            multi_preview = call(base, 'POST', preview_url, headers=auth,
                                 json={**reference, 'engine': 'pixal3d', 'left_upload_id': upload,
                                       'back_upload_id': upload, 'right_upload_id': upload}).json()
            assert {view['role'] for view in multi_preview['views']} == {'front', 'left', 'back', 'right'}
            call(base, 'POST', '/api/v1/generations', 402, headers=auth,
                 json={'upload_id': upload, 'overrides': {'remove_bg': False},
                       'reference_edits': reference['reference_edits']})

            call(base, 'PATCH', '/server/api/admin/users/' + alice_id, client=admin,
                 json={'disk_limit_bytes': 256 * MIB})
            call(base, 'PATCH', '/server/api/admin/queue', client=admin, json={'paused': True})
            (alice_path / identifier / 'raw_state.pt').write_bytes(b'fixture cache')
            write(alice_path / identifier / 'result.json', {'raw_triangles': 324_500})
            model = '/api/v1/models/' + identifier
            board = model + '/finishing-board'
            options = call(base, 'GET', board, headers=auth).json()
            assert options['source_id'] == identifier and options['source_triangles'] == 324_500
            assert options['triangle_presets']['medium'] == 28_000 and options['repair_surface']
            call(base, 'GET', board, 404, bob)
            call(base, 'POST', model + '/refinish', 422, headers=auth,
                 json={'triangles': 28_000, 'budget_preset': 'medium'})
            batch = call(base, 'POST', board, 202, headers=auth,
                         json={'variants': [{'budget_preset': 'low'}, {'triangles': 150_000}]}).json()
            assert len(batch['ids']) == batch['count'] == 2 and batch['source_id'] == identifier
            call(base, 'PATCH', board, 409, headers=auth, json={'selected_id': batch['ids'][0]})
            assert call(base, 'PATCH', board, headers=auth, json={'selected_id': None}).json() == {'selected_id': None}
            repaired = call(base, 'POST', model + '/refinish', 202, headers=auth,
                            json={'triangles': 28_000, 'repair_surface': True, 'bake_normals': False}).json()
            assert repaired['state'] == 'queued'
            repaired_job = call(base, 'GET', repaired['status_url'], headers=auth).json()
            assert repaired_job['settings']['finish_source_id'] == identifier
            assert repaired_job['settings']['repair_surface'] is True
            call(base, 'POST', repaired['status_url'] + '/cancel', headers=auth)
            retry = call(base, 'POST', '/api/v1/queue/' + repaired['id'] + '/retry', 202,
                         headers=auth, json={'triangles': 36_000}).json()
            assert retry['retry_of'] == repaired['id'] and retry['id'] != repaired['id']
            retried_job = call(base, 'GET', retry['status_url'], headers=auth).json()
            assert retried_job['settings']['finish_source_id'] == identifier
            assert retried_job['settings']['triangles'] == 36_000 and retried_job['settings']['repair_surface']
            assert store.get_user(alice_id)['credits'] == 0
            contract = call(base, 'GET', '/api/openapi.json', client=alice).json()
            assert 'TrellisApiKey' in contract['components']['securitySchemes']
            paths = contract['paths']
            assert {'get', 'post', 'patch'} <= set(paths['/api/v1/models/{identifier}/finishing-board'])
            assert 'post' in paths['/api/v1/references/preview']
            assert 'post' in paths['/api/v1/queue/{identifier}/retry']
            assert '/api/v1/client-config' not in paths
            assert 'post' in paths['/api/v1/models/{identifier}/animation-copy']
            assert {'get', 'post', 'delete'} <= set(paths['/api/v1/models/{identifier}/animations'])
            for variant in (*batch['ids'], retry['id']):
                call(base, 'POST', '/api/v1/jobs/' + variant + '/cancel', headers=auth)

            edition = call(base, 'GET', '/api/v1/status', headers=auth).json()
            assert edition['edition'] == 'server' and {'animation_batch', 'animation_clip_removal'} <= set(edition['features'])
            animated = model + '/animation-copy'
            model_animations = model + '/animations'
            motions = {'clips': [{'clip': 'walk', 'duration': 2.5}, {'clip': 'run', 'duration': 3}]}
            call(base, 'POST', model_animations, 409, headers=auth, json=motions)
            call(base, 'GET', model_animations, 409, headers=auth)
            call(base, 'POST', animated, 422, headers=auth, json={'clips': [motions['clips'][0]] * 2})
            call(base, 'POST', animated, 409, headers=auth, json=motions)
            call(base, 'PATCH', '/server/api/admin/users/' + bob_id, client=admin, json={'disk_limit_bytes': 128 * MIB})
            call(base, 'POST', animated, 404, bob, json=motions)

            source = alice_path / identifier
            gltf = json.dumps(dict(asset={'version': '2.0'}, meshes=[{'primitives': []}],
                                   skins=[{'joints': [0]}], animations=[{'name': 'TS_idle_2.5s'}])).encode()
            gltf += b' ' * (-len(gltf) % 4)
            mesh = struct.pack('<4sIIII', b'glTF', 2, 20 + len(gltf), len(gltf), 0x4e4f534a) + gltf
            (source/'model.glb').write_bytes(mesh)
            settings = json.loads((source/'settings.json').read_text())
            settings.update(animation_clips=[{'id': 'idle', 'duration': 2.5}], rig_options={'height_m': 1.69})
            write(source/'settings.json', settings)
            workspace.studio.find_blender = lambda: Path('fixture-blender')
            job_paths = set(alice_path.iterdir())
            call(base, 'PATCH', '/server/api/admin/users/' + alice_id, client=admin,
                 json={'disk_limit_bytes': 16 * MIB})
            call(base, 'POST', animated, 413, headers=auth, json=motions)
            call(base, 'POST', model_animations, 413, headers=auth, json=motions)
            assert set(alice_path.iterdir()) == job_paths and (source/'model.glb').read_bytes() == mesh
            call(base, 'PATCH', '/server/api/admin/users/' + alice_id, client=admin,
                 json={'disk_limit_bytes': 80 * MIB})
            listing = call(base, 'GET', model_animations, headers=auth).json()
            assert listing['animations'][0]['name'] == 'TS_idle_2.5s'
            call(base, 'DELETE', model_animations, 409, headers=auth,
                 json={'index': 0, 'clip': 'TS_idle_2.5s', 'revision': '0'})
            edit = call(base, 'DELETE', model_animations, 202, headers=auth,
                        json={'index': 0, 'clip': 'TS_idle_2.5s', 'revision': listing['revision']}).json()
            assert edit['id'] == identifier and edit['operation_id'] != identifier
            call(base, 'GET', edit['status_url'], 404, bob)
            call(base, 'POST', model_animations, 409, headers=auth, json=motions)
            assert call(base, 'GET', model_animations, headers=auth).json() == listing
            call(base, 'POST', edit['status_url']+'/cancel', headers=auth)
            assert set(alice_path.iterdir()) == job_paths and (source/'model.glb').read_bytes() == mesh

            child = call(base, 'POST', animated, 202, headers=auth, json=motions).json()
            child_path = alice_path / child['id']
            job = call(base, 'GET', child['status_url'], headers=auth).json()
            assert child['state'] == 'queued' and job['settings']['parent_id'] == identifier
            recipe = json.loads((child_path/'animation-request.json').read_text())
            assert recipe['linked'] and recipe['settings']['rig_options'] == settings['rig_options']
            assert [clip['id'] for clip in recipe['clips']] == ['idle', 'walk', 'run']
            assert (child_path/'source.glb').read_bytes() == mesh and not (child_path/'model.glb').exists()
            assert next(row for row in store.jobs(alice_id) if row['id'] == child['id'])['credit_cost'] == 0
            call(base, 'POST', child['status_url']+'/cancel', headers=auth)
            assert not (child_path/'source.glb').exists() and not (child_path/'animation-request.json').exists()

            over = call(base, 'POST', model_animations, 202, headers=auth, json=motions).json()
            operation_id = over['operation_id']
            assert over['id'] == identifier
            over_path = workspace.state / 'animation-ops' / operation_id
            animation_process = FixtureProcess()

            def launch_animation(path, script, source_id=None):
                assert path == over_path and script == 'animation_worker.py'
                workspace.studio.PROCESS = animation_process
                workspace.studio.ACTIVE = path.name

            workspace.launch = launch_animation
            call(base, 'PATCH', '/server/api/admin/queue', client=admin, json={'paused': False})
            try:
                eventually(lambda: any(row['id'] == operation_id and row['state'] == 'running'
                                       for row in store.jobs(alice_id)))
                with (over_path/'model.blend').open('wb') as stream:
                    stream.truncate(85 * MIB)
                (over_path/'model.glb').write_bytes(mesh)
                write(over_path/'result.json', {})
                write(over_path/'status.json', dict(state='ready', stage='Fixture complete', progress=100))
            finally:
                animation_process.completed = True
            eventually(lambda: any(row['id'] == operation_id and row['state'] == 'error'
                                   for row in store.jobs(alice_id)))
            assert not (over_path/'model.blend').exists() and not (over_path/'source.glb').exists()
            assert (source/'model.glb').read_bytes() == mesh and store.get_user(alice_id)['credits'] == 0

            call(base, 'PATCH', '/server/api/admin/users/' + bob_id, client=admin, json={'disk_limit_bytes': 70 * MIB})
            bob_workspace = state.runtime.workspaces[bob_id]
            bob_process = FixtureProcess()
            def launch_bob(path, script, source_id=None):
                bob_workspace.studio.PROCESS = bob_process
                bob_workspace.studio.ACTIVE = path.name
            bob_workspace.launch = launch_bob
            bob_job = call(base, 'POST', '/api/generate', client=bob, **request_data).json()['id']
            bob_output = bob_workspace.outputs / bob_job
            try:
                eventually(lambda: store.jobs(bob_id)[0]['state'] == 'running')
                # A worker can produce more bytes than its admission estimate.
                with (bob_output / 'raw_state.pt').open('wb') as stream:
                    stream.truncate(75 * MIB)
                write(bob_output / 'status.json', {'state': 'done', 'stage': 'Fixture complete', 'progress': 100})
            finally:
                bob_process.completed = True
            eventually(lambda: store.jobs(bob_id)[0]['state'] == 'error')
            assert store.get_user(bob_id)['credits_available'] == 1
            assert not (bob_output / 'raw_state.pt').exists() and (bob_output / 'input.png').is_file()

            call(base, 'PATCH', '/server/api/admin/users/' + alice_id, client=admin, json={'status': 'disabled'})
            call(base, 'GET', '/api/jobs', 401, alice)
            call(base, 'GET', '/server/api/me', 401, alice)
            call(base, 'GET', '/api/v1/jobs/' + identifier, 401, headers=auth)
        with serve(data) as (base, state):
            admin, _ = login(base, 'admin', codes[1])
            assert state.store.get_user(alice_id)['credits'] == 0
            assert len(call(base, 'GET', '/server/api/admin/users', client=admin).json()['users']) == 3
    print('PASS: accounts, ownership, API keys, REST previews, finishing, retry, in-place animations, quotas, revocation and restart.')


if __name__ == '__main__':
    run()
