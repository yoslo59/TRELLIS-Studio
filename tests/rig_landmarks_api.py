"""Isolated ASGI legacy/REST rig validation; workers and inference never run."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,os,runpy,shutil,struct,subprocess,sys,socket,threading,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import requests,uvicorn
options=runpy.run_path(str(ROOT/'tests/rig_landmarks.py'),run_name='fixture')['fixture']()[-1]

with TemporaryDirectory(prefix='trellis-rig-api-') as temporary:
    folder=Path(temporary)
    for key,name in [('OUTPUT_DIR','outputs'),('TRASH_DIR','trash'),('EXPORT_DIR','exports'),('STATE_DIR','state'),('PROFILES_FILE','profiles.json')]:
        os.environ['TRELLIS_'+key]=str(folder/name)
    import app
    for identifier,skinned in [('a'*32,False),('b'*32,True)]:
        item=app.OUTPUT/identifier;item.mkdir(parents=True)
        data=dict(asset={'version':'2.0'},meshes=[{'primitives':[]}])
        if skinned:data['skins']=[{'joints':[0]}]
        raw=json.dumps(data).encode();raw+=b' '*((-len(raw))%4)
        (item/'model.glb').write_bytes(struct.pack('<4sIIII',b'glTF',2,20+len(raw),len(raw),0x4e4f534a)+raw)
        app.write_json(item/'settings.json',dict(name='Fixture',asset_type='imported'))
        app.write_json(item/'metadata.json',dict(display_name='Fixture'))
        app.write_json(item/'status.json',dict(state='done',stage='Fixture',progress=100))
    def launch(job,script,**kwargs):
        assert script=='rig_worker.py'
        app.write_json(job/'status.json',dict(state='queued',stage='Test only',progress=2))

    def simulate_verification_failure(job):
        verification = dict(passed=False, error='The final rig did not preserve the joint landmarks.',
                            tolerance_m=.005, errors_m={'left_wrist': .061})

        def run(command, **_):
            if command[0] == 'wsl.exe':
                app.write_json(job/'rig-work'/'landmarks-report.json',
                               dict(final_rig_verification=verification))
                raise subprocess.CalledProcessError(15, command)
            assert any(str(argument).endswith('prepare_rig.py') for argument in command)

        with (
            patch('studio_import.find_blender', return_value=Path('fixture-blender')),
            patch.object(subprocess, 'check_output', return_value='/fixture'),
            patch.object(subprocess, 'run', side_effect=run),
            patch.object(sys, 'argv', ['rig_worker.py', str(job)]),
            patch('traceback.print_exc') as log_failure,
        ):
            runpy.run_path(str(ROOT/'rig_worker.py'), run_name='__main__')
        log_failure.assert_called_once()
        return verification

    def main(base):
        with requests.Session() as client:
            for route,code in [('/api/jobs/'+'a'*32+'/rig',201),('/api/v1/models/'+'a'*32+'/rig',202)]:
                r=client.post(base+route,json=options,timeout=5);assert r.status_code==code,r.text
                saved=json.loads((app.OUTPUT/r.json()['id']/'settings.json').read_text())
                assert saved['rig_options']==options
                bad={**options,'landmarks':{'chin':[0,.9]}}
                r=client.post(base+route,json=bad,timeout=5);assert r.status_code in (400,422),r.text
            for route in ['/api/jobs/'+'b'*32+'/rig','/api/v1/models/'+'b'*32+'/rig']:
                r=client.post(base+route,json=options,timeout=5);assert r.status_code==409,r.text
                r=client.post(base+route,json={},timeout=5);assert r.status_code in (201,202),r.text
                saved=json.loads((app.OUTPUT/r.json()['id']/'settings.json').read_text())
                assert 'landmarks' not in saved['rig_options']

            r = client.post(base+'/api/v1/models/'+'a'*32+'/rig', json=options, timeout=5)
            assert r.status_code == 202, r.text
            identifier = r.json()['id']
            job = app.OUTPUT/identifier
            verification = simulate_verification_failure(job)
            shutil.copyfile(job/'source.glb', job/'model.glb')
            app.write_json(job/'result.json', dict(partial=True))
            failed = client.get(base+'/api/v1/jobs/'+identifier, timeout=5).json()
            assert failed['state'] == 'error' and failed['settings']['parent_id'] == 'a'*32
            assert failed['error_code'] == 'rig_landmark_verification_failed'
            assert failed['error'] == verification['error'] and not failed['media']['available']
            assert failed['error_details'] == {key: verification[key] for key in ('tolerance_m', 'errors_m')}
            legacy = base+'/api/jobs/'+identifier
            typed = base+'/api/v1/models/'+identifier
            assert client.get(legacy+'/rig-preview-model', timeout=5).status_code == 200
            for prefix in (legacy, typed):
                for filename in ('model.glb', 'result.json'):
                    assert client.get(prefix+'/files/'+filename, timeout=5).status_code == 409
            app.write_json(job/'status.json', dict(state='running', operation='animation'))
            assert client.get(legacy+'/files/model.glb', timeout=5).status_code == 200
            assert client.get(typed+'/files/model.glb', timeout=5).status_code == 200
            print('PASS: rig options, preserved verification error, preview access and final asset gating')
    try:
        with patch.object(app,'require_idle'),patch.object(app,'components',return_value={'unirig':{'ready':True}}),patch.object(app,'find_blender',return_value=Path('fixture-blender')),patch.object(app,'launch_worker',side_effect=launch):
            with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            server=uvicorn.Server(uvicorn.Config(app.app,host='127.0.0.1',port=port,log_level='error',lifespan='off'))
            thread=threading.Thread(target=server.run,daemon=True);thread.start()
            try:
                for _ in range(100):
                    if server.started:break
                    time.sleep(.05)
                assert server.started,'Isolated API server did not start'
                assert 'rig_landmarks' in app.status()['features']
                assert app.status()['default_profile']=='game_d'
                main('http://127.0.0.1:'+str(port))
            finally:server.should_exit=True;thread.join(timeout=8)
    finally:
        if app.LOG_HANDLE:app.LOG_HANDLE.close()
