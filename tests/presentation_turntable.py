"""Isolated persistence and media-serving checks for optional card turntables."""
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from unittest.mock import patch

import requests
import uvicorn


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODEL = 'a' * 32
HEADER = b'\x1aE\xdf\xa3\x87\x42\x82\x84webm'
ORIGINAL = HEADER + b'ordinary recording'
COMPACT = HEADER + b'compact recording'


def run():
    with TemporaryDirectory(prefix='trellis-presentation-test-') as temporary:
        root = Path(temporary)
        for key, name in [('OUTPUT_DIR', 'outputs'), ('TRASH_DIR', 'trash'), ('EXPORT_DIR', 'exports'),
                          ('STATE_DIR', 'state'), ('PROFILES_FILE', 'profiles.json')]:
            os.environ['TRELLIS_' + key] = str(root / name)
        import app
        from studio_api import LIBRARY_TURNTABLE_LIMIT, save_library_turntable
        from studio_workspace import read

        folder = app.OUTPUT / MODEL
        folder.mkdir()
        app.write_json(folder / 'settings.json', dict(name='Fixture', asset_type='object'))
        app.write_json(folder / 'status.json', dict(state='done', stage='Fixture', progress=100))
        target = folder / 'Presentation'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app.app, host='127.0.0.1', port=port, log_level='error'))
        thread = Thread(target=server.run, daemon=True)
        url = f'http://127.0.0.1:{port}/api/jobs/{MODEL}'
        presentation = url + '/presentation'
        preview = presentation + '/library-turntable'
        media = preview + '.webm'

        def upload(kind, data, expected=200):
            recipe = dict(width=384, height=384, fps=12, duration=6)
            response = requests.post(presentation, timeout=6, data=dict(kind=kind, recipe=json.dumps(recipe)),
                                     files={'file': ('turntable.webm', data, 'video/webm')})
            assert response.status_code == expected, (response.status_code, response.text)
            return response.json()

        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started
            assert requests.get(media, timeout=6).status_code == 404
            assert requests.post(preview, timeout=6).status_code == 404
            assert 'library_turntable' not in upload('turntable', ORIGINAL)
            assert requests.get(media, timeout=6).status_code == 404

            reused = requests.post(preview, timeout=6)
            assert reused.status_code == 200, reused.text
            first = reused.json()['library_turntable']
            assert requests.get(media, params={'v': first['revision']}, timeout=6).content == ORIGINAL
            upload('turntable', COMPACT)
            assert requests.get(media, timeout=6).content == ORIGINAL

            replacement = upload('library_turntable', COMPACT)['library_turntable']
            assert replacement['revision'] != first['revision']
            assert not (target / first['file']).exists()
            assert requests.get(media, params={'v': first['revision']}, timeout=6).status_code == 404
            video = requests.get(media, params={'v': replacement['revision']}, timeout=6)
            assert video.content == COMPACT and video.headers['content-type'] == 'video/webm'
            assert video.headers['content-disposition'].startswith('inline')
            part = requests.get(media, headers={'Range': 'bytes=0-3'}, timeout=6)
            assert part.status_code == 206 and part.content == HEADER[:4]
            assert requests.get(url, timeout=6).json()['presentation']['library_turntable'] == replacement

            upload('library_turntable', b'not a webm', 400)
            upload('library_turntable', HEADER + bytes(LIBRARY_TURNTABLE_LIMIT), 400)
            assert requests.get(media, timeout=6).content == COMPACT
            paths = set(target.iterdir())
            with patch('studio_api.write', side_effect=OSError('fixture manifest failure')):
                try:
                    save_library_turntable(target, ORIGINAL, {})
                except OSError:
                    pass
                else:
                    raise AssertionError('Failed manifest write must report its error')
            assert set(target.iterdir()) == paths
            assert read(target / 'presentation.json')['library_turntable'] == replacement

            removed = requests.delete(preview, timeout=6)
            assert removed.status_code == 200 and 'library_turntable' not in removed.json()
            assert not (target / replacement['file']).exists()
            assert requests.get(media, timeout=6).status_code == 404
            assert requests.get(presentation + '/turntable.webm', timeout=6).content == COMPACT
            print('PASS: opt-in, reuse, revisioned inline WebM, range, limits and removal')
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    run()
