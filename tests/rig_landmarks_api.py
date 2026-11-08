"""Isolated ASGI legacy/REST rig validation; workers and inference never run."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,os,runpy,struct,sys,socket,threading,time
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
            print('PASS: legacy + REST persist exact landmarks, reject incomplete sets and skinned inputs, preserve old no-marker flow')
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
