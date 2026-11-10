"""Exercise corrected retries through the local API and scheduler without GPU inference."""
import json
from pathlib import Path
import socket
import sys
from threading import Thread
import time
from unittest.mock import patch

import requests
import uvicorn

from _support import isolated_workspace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MONO, PIXAL, LEGACY = ('a' * 32, 'b' * 32, 'c' * 32)


def run():
    with isolated_workspace('queue-retry'):
        import app
        from generation_options import TRIANGLE_MAX

        app.generation_capabilities = lambda: ([512, 1024], [1024, 2048])
        app.pixal3d_runtime_status = lambda root: {
            'ready': True, 'message': '', 'face_projection': {'ready': False, 'message': 'Install face detector.'},
        }
        status = app.status
        app.status = lambda: {**status(), 'ready': True}

        def fixture(identifier, references, **options):
            folder = app.OUTPUT / identifier
            folder.mkdir()
            for filename in (*references, 'generation.log', 'model.glb', 'raw_state.pt'):
                (folder / filename).write_bytes(filename.encode())
            settings = dict(name='QA fixture', preset='game_d', engine='trellis2', triangles=28000,
                            texture_size=2048, resolution=1024, pipeline_type='1024_cascade',
                            seed=117, height_m=0.6, generation_options={'structure_steps': 20})
            settings.update(options)
            app.write_json(folder / 'settings.json', settings)
            app.write_json(folder / 'status.json', dict(state='error', stage='Finishing failed', progress=60,
                                                        error='CUDA out of memory (fixture)'))
            app.write_json(folder / 'metadata.json', dict(display_name='QA fixture', favorite=True,
                                                          reference_prompt='Fixture reference', tags=['QA']))
            app.STUDIO.workspace.enqueue(identifier)
            return {entry.name: entry.read_bytes() for entry in folder.iterdir()}

        app.STUDIO.workspace.queue['paused'] = True
        mono = fixture(MONO, ['input.png', 'prepared.png', 'conditioning.png'],
                       remove_bg=True, reference_edits={'front': {'keep': []}})
        pixal = fixture(PIXAL, ['input.png', 'input-left.png', 'input-back.png', 'prepared.png', 'prepared-left.png'],
                        engine='pixal3d', preset='character_pixal', pipeline_type='pixal3d_1024_multiview',
                        reference_edits={}, side_angle=90, views=[
                            {'role': 'front', 'azimuth': 0, 'file': 'input.png'},
                            {'role': 'left', 'azimuth': 90, 'file': 'input-left.png'},
                            {'role': 'back', 'azimuth': 180, 'file': 'input-back.png'},
                        ])
        fixture(LEGACY, ['input.png'])
        calls = []

        def launch(folder, script):
            settings = json.loads((folder / 'settings.json').read_bytes())
            calls.append((settings.get('retry_of'), script, settings))
            app.write_json(folder / 'status.json', dict(state='done', stage='Test worker completed', progress=100))

        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app.app, host='127.0.0.1', port=port, log_level='error'))
        thread = Thread(target=server.run, daemon=True)

        def send(method, path, code=200, **kwargs):
            response = requests.request(method, f'http://127.0.0.1:{port}' + path, timeout=6, **kwargs)
            assert response.status_code == code, (method, path, response.status_code, response.text)
            return response.json()

        def retry(identifier, code=202, **kwargs):
            return send('POST', f'/api/queue/{identifier}/retry', code, **kwargs)

        def queued(identifier):
            return next(item for item in send('GET', '/api/queue')['items'] if item['id'] == identifier)

        with patch.object(app, 'launch_worker', side_effect=launch):
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(.05)
                assert server.started
                assert queued(MONO)['error'] == 'CUDA out of memory (fixture)'
                retry(MONO, 400, data='malformed JSON')
                retry(MONO, 400, json={'resolution': 1536})
                retry(MONO, 400, json={'triangles': TRIANGLE_MAX + 1})
                retry(MONO, 400, json={'seed': 9})
                retry(MONO, 400, json={'pre_simplify': 'yes'})
                with patch.object(app.STUDIO.workspace, 'save_queue', side_effect=OSError('fixture disk failure')):
                    retry(MONO, 503, json={})
                retry(PIXAL, 400, json={'resolution': 512})
                missing = retry(PIXAL, 409, json={})
                assert 'prepared-back.png' in missing['detail']
                assert {folder.name for folder in app.OUTPUT.iterdir()} == {MONO, PIXAL, LEGACY}

                mono_retry = retry(MONO, json={'resolution': 512, 'texture_size': 1024, 'triangles': TRIANGLE_MAX,
                                              'height_m': 1.2, 'priority': 2, 'mesh_mode': 'direct',
                                              'pre_simplify': False, 'repair_holes': False})
                (app.OUTPUT / PIXAL / 'prepared-back.png').write_bytes(b'saved back preparation')
                pixal_retry = retry(PIXAL, json={'texture_size': 1024, 'triangles': 16000})
                retry(mono_retry['id'], 409, json={})
                for response, source, baseline in [(mono_retry, MONO, mono), (pixal_retry, PIXAL, pixal)]:
                    assert response['retry_of'] == source and response['paused']
                    attempt = queued(response['id'])
                    settings = attempt['settings']
                    child = app.OUTPUT / response['id']
                    original = app.OUTPUT / source
                    assert attempt['state'] == 'queued' and settings['seed'] == 117
                    assert settings['retry_of'] == settings['parent_id'] == source
                    assert settings['reference_edits'] == json.loads(baseline['settings.json'])['reference_edits']
                    for filename, content in baseline.items():
                        assert (original / filename).read_bytes() == content
                        if filename.endswith('.png'):
                            assert (child / filename).read_bytes() == content
                    assert not any((child / name).exists() for name in ('generation.log', 'model.glb', 'raw_state.pt'))
                    assert queued(source)['state'] == 'error'
                assert (app.OUTPUT / pixal_retry['id'] / 'prepared-back.png').read_bytes() == b'saved back preparation'
                send('PATCH', f'/api/queue/{LEGACY}', json={'retry': True})
                assert queued(LEGACY)['state'] == 'queued'
                send('PATCH', '/api/queue', json={'paused': False})
                for _ in range(100):
                    if len(calls) == 3:
                        break
                    time.sleep(.05)
                assert len(calls) == 3, calls
                mono_call, pixal_call = (next(call for call in calls if call[0] == source) for source in (MONO, PIXAL))
                assert mono_call[1] == 'worker.py' and pixal_call[1] == 'pixal3d_worker.py'
                assert mono_call[2]['pipeline_type'] == '512' and mono_call[2]['height_m'] == 1.2
                assert mono_call[2]['triangles'] == TRIANGLE_MAX and mono_call[2]['texture_size'] == 1024
                assert mono_call[2]['generation_options']['structure_steps'] == 20
                assert mono_call[2]['generation_options']['mesh_mode'] == 'direct'
                assert mono_call[2]['generation_options']['pre_simplify'] is False
                assert pixal_call[2]['views'] == json.loads(pixal['settings.json'])['views']
                assert pixal_call[2]['pipeline_type'] == 'pixal3d_1024_multiview'
            finally:
                server.should_exit = True
                thread.join(timeout=8)
                assert not thread.is_alive()

    print('Queue correction, source preservation, rollback and worker dispatch passed.')


if __name__ == '__main__':
    run()
