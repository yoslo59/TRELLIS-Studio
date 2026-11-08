
"""Pixal3D multipart, typed REST and queue dispatch regressions without inference."""
from pathlib import Path
from tempfile import TemporaryDirectory
import io
import json
import os
import socket
import subprocess
import sys
import time

from PIL import Image
import requests

ROOT = Path(__file__).resolve().parents[1]

def png(colour=(90, 110, 120, 255)):
    stream = io.BytesIO()
    Image.new('RGBA', (36, 40), colour).save(stream, 'PNG')
    return stream.getvalue()

def upload(data=None):
    return ('reference.png', png() if data is None else data, 'image/png')

with TemporaryDirectory(prefix='trellis-pixal-api-') as tmp:
    root = Path(tmp)
    with socket.socket() as port_probe:
        port_probe.bind(('127.0.0.1', 0))
        port = port_probe.getsockname()[1]
    env = dict(os.environ, PYTHONUTF8='1', TRELLIS_PORT=str(port))
    for key, name in [('OUTPUT_DIR','outputs'), ('TRASH_DIR','trash'), ('EXPORT_DIR','exports'),
                      ('STATE_DIR','state'), ('PROFILES_FILE','profiles.json')]:
        env['TRELLIS_'+key] = str(root / name)

    bootstrap = """
from pathlib import Path
import app
import uvicorn
app.pixal3d_runtime_status = lambda root: {
    'ready': (app.OUTPUT.parent / 'ready').is_file(), 'message': 'Install Pixal3D.',
    'face_projection': {
        'ready': (app.OUTPUT.parent / 'face-ready').is_file(), 'message': 'Install face detector.',
    },
}
def launch(folder, script, **kwargs):
    app.write_json(folder / 'worker-test.json', {'script': script})
    app.write_json(folder / 'status.json', {'state': 'done', 'stage': 'Test worker completed', 'progress': 100})
app.launch_worker = launch
config = uvicorn.Config(app.app, host='127.0.0.1', port=app.PORT, log_level='warning')
app.SERVER = uvicorn.Server(config)
app.SERVER.run()
"""
    base = 'http://127.0.0.1:' + str(port)
    def request(method, route, expected=200, **kwargs):
        response = requests.request(method, base + route, timeout=8, **kwargs)
        assert response.status_code == expected, (route, response.status_code, response.text[:500])
        return response
    with (root / 'server.log').open('wb') as log:
        proc = subprocess.Popen([sys.executable, '-c', bootstrap], cwd=ROOT, env=env,
                                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(80):
                try:
                    status = request('GET', '/api/status').json()
                    break
                except requests.ConnectionError:
                    time.sleep(.1)
            else:
                raise RuntimeError((root/'server.log').read_text(encoding='utf-8'))
            assert status['default_profile'] == 'game_d' and not status['pixal3d']['ready']
            page = request('GET', '/')
            assert page.headers['cache-control'] == 'no-cache, must-revalidate'
            assert status['ui_revision'] in page.text
            card = page.text.split('<section class="reference-card"', 1)[1].split('</section>', 1)[0]
            assert card.index('id="image"') < card.index('id="pixalMode"') < card.index('id="supplementalViews"')
            files = {'image': upload(), 'left_image': upload()}
            request('POST', '/api/generate-multiview', 503, files=files)
            (root/'ready').touch()
            request('POST', '/api/generate-multiview', 400, files={'image': upload()})
            request('POST', '/api/generate-multiview', 400, files=files, data={'side_angle': 45})
            request('POST', '/api/generate-multiview', 400, files={**files, 'right_image': upload(b'broken')})
            request('POST', '/api/generate-multiview', 400, files={**files, 'right_image': upload(png((0,0,0,0)))})
            denied = request('POST', '/api/generate-multiview', 503, files=files, data={'reproject_face':'true'})
            assert denied.json()['detail'] == 'Install face detector.'
            assert not any((root/'outputs').iterdir())

            request('PATCH', '/api/queue', json={'paused': True})
            result = request('POST', '/api/generate-multiview', 202, data={
                'name': 'Pilot character', 'side_angle': 90, 'height_m': 1.59, 'keep_finishing_source': 'true',
            }, files={**files, 'back_image': upload(), 'right_image': upload()}).json()
            first = result['id']
            record = request('GET', '/api/jobs/' + first).json()
            s = record['settings']
            assert record['state']=='queued' and s['engine']=='pixal3d' and s['asset_type']=='humanoid'
            assert s['profile']['id']=='character_pixal' and s['triangles']==10600 and s['texture_size']==2048
            assert s['settings_version']==3
            assert s['height_m']==1.59 and s['keep_finishing_source']
            assert 'reproject_face' not in s
            assert [(v['role'],v['azimuth']) for v in s['views']]==[('front',0),('left',90),('back',180),('right',-90)]
            for view in s['views']:
                r = request('GET','/api/jobs/'+first+'/files/'+view['file'])
                assert r.headers['content-type'].startswith('image/png')
                with Image.open(io.BytesIO(r.content)) as i:
                    assert i.mode=='RGBA'
            request('GET','/api/jobs/'+first+'/files/raw_state.pt',404)

            front = request('POST','/api/v1/uploads',201,files={'file':upload()}).json()['id']
            right = request('POST','/api/v1/uploads',201,files={'file':upload()}).json()['id']
            (root/'face-ready').touch()
            assert request('GET','/api/status').json()['pixal3d']['face_projection']['ready']
            api = request('POST','/api/v1/generations/multiview',202,json={
                'upload_id':front, 'right_upload_id':right, 'parent_id':first, 'texture_size':1024, 'priority':2,
                'reproject_face':True,
            }).json()
            second = api['id']
            s = request('GET','/api/jobs/'+second).json()['settings']
            assert s['parent_id']==first and s['texture_size']==1024
            assert s['settings_version']==3
            assert s['reproject_face'] is True
            assert [v['azimuth'] for v in s['views']]==[0,-40]
            request('POST','/api/jobs/'+first+'/cancel',200)
            request('PATCH','/api/queue/'+first,json={'retry':True})
            request('PATCH','/api/queue',json={'paused':False})
            for ident in [second,first]:
                for _ in range(50):
                    if request('GET','/api/jobs/'+ident).json()['state']=='done':
                        break
                    time.sleep(.1)
                assert json.loads((root/'outputs'/ident/'worker-test.json').read_text())['script']=='pixal3d_worker.py'
            d = request('GET','/api/profiles').json()
            assert d['default_profile']=='game_d' and d['profiles'][0]['settings']['triangles']==28000
            print('Pixal3D readiness, face opt-in, UI revision, four views, typed API, queue retry/dispatch and default D passed.')
        finally:
            if proc.poll() is None:
                try:
                    requests.post(base+'/api/shutdown', timeout=3)
                    proc.wait(timeout=8)
                except (requests.RequestException, subprocess.TimeoutExpired):
                    proc.terminate()
                    proc.wait(timeout=8)
