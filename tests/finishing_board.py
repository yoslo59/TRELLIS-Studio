"""Isolated queue, provenance and choice checks. No inference or user data."""
import json
import os
from pathlib import Path
import socket
import struct
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from unittest.mock import patch

import requests
import uvicorn


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = 'a' * 32
OTHER = 'b' * 32
SCENE = json.dumps(dict(asset={'version': '2.0'}, meshes=[{'primitives': []}])).encode()
SCENE += b' ' * (-len(SCENE) % 4)
GLB = struct.pack('<4sIIII', b'glTF', 2, 20 + len(SCENE), len(SCENE), 0x4e4f534a) + SCENE


def run():
    with TemporaryDirectory(prefix='trellis-finish-test-') as temporary:
        root = Path(temporary)
        for key, name in [('OUTPUT_DIR', 'outputs'), ('TRASH_DIR', 'trash'), ('EXPORT_DIR', 'exports'),
                          ('STATE_DIR', 'state'), ('PROFILES_FILE', 'profiles.json')]:
            os.environ['TRELLIS_' + key] = str(root / name)
        import app
        from studio_workspace import read

        for identifier in (SOURCE, OTHER):
            folder = app.OUTPUT / identifier
            folder.mkdir()
            (folder / 'raw_state.pt').write_bytes(b'decoded cache fixture')
            (folder / 'model.glb').write_bytes(GLB)
            (folder / 'input.png').write_bytes(b'reference fixture')
            app.write_json(folder / 'settings.json', dict(name='Cached source', asset_type='object',
                           engine='pixal3d', preset='game_d', keep_finishing_source=True, triangles=28000,
                           texture_size=2048, resolution=1024, seed=117, height_m=0.6))
            app.write_json(folder / 'status.json', dict(state='done', stage='Fixture', progress=100))
        source_folder = app.OUTPUT / SOURCE
        baseline = {filename: (source_folder / filename).read_bytes() for filename in
                    ('settings.json', 'model.glb', 'input.png', 'raw_state.pt')}
        calls = []

        def launch(folder, script, *, source_id):
            assert script == 'worker.py' and source_id == SOURCE, (script, source_id)
            calls.append(folder.name)
            settings = read(folder / 'settings.json')
            result = dict(triangles=settings['triangles'], texture_size=settings['texture_size'],
                          finish_source_id=source_id, neural_inferences=0)
            (folder / 'model.glb').write_bytes(GLB)
            app.write_json(folder / 'status.json', dict(state='done', stage='Fixture', progress=100, result=result))

        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app.app, host='127.0.0.1', port=port, log_level='error'))
        thread = Thread(target=server.run, daemon=True)
        url = f'http://127.0.0.1:{port}'
        route = f'/api/jobs/{SOURCE}/finishing-board'
        payload = {'variants': [
            {'triangles': 60000, 'mesh_mode': 'direct', 'repair_holes': False},
            {'triangles': 12000, 'texture_size': 1024},
        ]}

        def send(method, path, code=200, **kwargs):
            response = requests.request(method, url + path, timeout=6, **kwargs)
            assert response.status_code == code, (method, path, response.status_code, response.text)
            return response.json()

        def wait_for(identifier, expected):
            for _ in range(100):
                result = send('GET', '/api/jobs/' + identifier)
                if result['state'] == expected:
                    return result
                time.sleep(0.05)
            raise AssertionError((identifier, expected, result))

        with patch.object(app, 'launch_worker', side_effect=launch):
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.05)
                assert server.started
                board = send('GET', route)
                assert board['source_id'] == SOURCE and board['cache_bytes'] == len(baseline['raw_state.pt'])
                assert board['selected_id'] is None
                assert send('GET', '/api/status')['default_profile'] == 'game_d'
                send('PATCH', '/api/queue', json={'paused': True})
                send('POST', route, 400, json={'variants': [{'triangles': 80000}, {'seed': 7}]})
                send('POST', route, 400, json={'variants': [{}, {}]})
                send('POST', route, 400, data='invalid json')
                with patch.object(app.STUDIO.workspace, 'save_queue', side_effect=OSError('fixture disk failure')):
                    send('POST', route, 503, json=payload)
                assert {folder.name for folder in app.OUTPUT.iterdir()} == {SOURCE, OTHER}
                assert not send('GET', '/api/queue')['items']
                response = send('POST', route, 202, json=payload)
                assert response['paused'] and response['source_id'] == SOURCE and response['count'] == 2
                for identifier in response['ids']:
                    child = app.OUTPUT / identifier
                    state = send('GET', '/api/jobs/' + identifier)
                    settings = state['settings']
                    assert state['state'] == 'queued' and settings['source_kind'] == 'refinish'
                    assert settings['parent_id'] == SOURCE and settings['finish_source_id'] == SOURCE
                    assert settings['seed'] == 117 and settings['height_m'] == 0.6
                    assert not (child / 'raw_state.pt').exists()
                    assert (child / 'input.png').read_bytes() == baseline['input.png']
                    send('PATCH', route, 409, json={'selected_id': identifier})
                send('PATCH', route, 409, json={'selected_id': OTHER})

                cache = source_folder / 'raw_state.pt'
                cache.rename(source_folder / 'raw_state.saved')
                send('PATCH', '/api/queue', json={'paused': False})
                for identifier in response['ids']:
                    assert 'Restore the source' in wait_for(identifier, 'error')['error']
                assert not calls
                (source_folder / 'raw_state.saved').rename(cache)
                for identifier in response['ids']:
                    send('PATCH', '/api/queue/' + identifier, json={'retry': True})
                    assert wait_for(identifier, 'done')['result']['neural_inferences'] == 0
                assert calls == response['ids']
                send('PATCH', '/api/queue', json={'paused': True})
                refinish_route = f'/api/jobs/{SOURCE}/refinish'
                send('POST', refinish_route, 400, json={'triangles': 8000001, 'adaptive_finishing': True})
                adaptive = send('POST', refinish_route, 202, json={'triangles': 8000000, 'adaptive_finishing': True})
                state = wait_for(adaptive['id'], 'queued')
                assert adaptive['queued'] and adaptive['paused']
                assert state['settings']['adaptive_finishing'] is True
                assert state['settings']['triangles'] == 8000000
                options = state['settings']['generation_options']
                assert options['mesh_mode'] == 'direct' and not options['pre_simplify'] and not options['repair_holes']
                send('PATCH', '/api/queue', json={'paused': False})
                assert wait_for(adaptive['id'], 'done')['result']['neural_inferences'] == 0
                chosen = response['ids'][1]
                assert send('PATCH', route, json={'selected_id': chosen})['selected_id'] == chosen
                assert send('GET', f'/api/jobs/{chosen}/finishing-board')['selected_id'] == chosen
                assert read(source_folder / 'finishing-choice.json')['id'] == chosen
                assert {name: (source_folder / name).read_bytes() for name in baseline} == baseline
                assert send('PATCH', route, json={'selected_id': None})['selected_id'] is None
                print('PASS: atomic batch, cached worker dispatch, cache loss/retry, persisted choice and original preservation')
            finally:
                server.should_exit = True
                thread.join(timeout=8)


if __name__ == '__main__':
    run()
