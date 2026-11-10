"""Cached profiles through UI/REST contracts; no inference or real jobs."""
from pathlib import Path
from contextlib import contextmanager
from tempfile import TemporaryDirectory
import os, sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

@contextmanager
def isolated_workspace(name):
    with TemporaryDirectory(prefix='trellis-test-'+name+'-') as directory:
        folder=Path(directory)
        values={f'TRELLIS_{key}':str(folder/path) for key,path in [
            ('OUTPUT_DIR','outputs'),('TRASH_DIR','trash'),('STATE_DIR','state'),
            ('EXPORT_DIR','exports'),('PROFILES_FILE','profiles.json')]}
        previous={key:os.environ.get(key) for key in values};os.environ.update(values)
        try:yield folder
        finally:
            for key,value in previous.items():
                if value is None:os.environ.pop(key,None)
                else:os.environ[key]=value

with isolated_workspace('humanoid_profiles'):
    import copy, io, json, requests, socket, threading, time, uvicorn
    from contextlib import contextmanager
    from unittest.mock import patch
    from PIL import Image
    import app
    from api_contract import Generation, MultiViewGeneration
    from generation_profiles import builtin_profiles
    from generation_options import DENSE_MASTER, DEFAULT_GENERATION, DEFAULT_PROFILE, options_for_job
    from pixal3d_worker import sampling_options

    before = copy.deepcopy(builtin_profiles()[0])
    standard_options = copy.deepcopy(app.PIXAL_CHARACTER_OPTIONS)
    standard_profile = copy.deepcopy(app.PIXAL_CHARACTER_PROFILE)
    assert DEFAULT_PROFILE == Generation.model_fields['profile_id'].default == 'game_d'
    assert MultiViewGeneration.model_fields['profile_id'].default == 'character_pixal'
    assert DEFAULT_GENERATION == dict(preset='game_d', resolution=1024, triangles=28000, texture_size=2048)
    d = before['settings']['advanced']
    assert [d[k+'_steps'] for k in ('structure','shape','texture')] == [20,20,20]
    assert [d[k+'_guidance'] for k in ('structure','shape','texture')] == [7.5,7.5,2]
    assert d['mesh_mode']=='remesh' and d['repair_holes'] and d['pre_simplify']
    assert (d['remesh_band'],d['remesh_project'],d['qem_edge_weight'],d['qem_skinny_weight']) == (1,0,.01,.001)

    stream=io.BytesIO();Image.new('RGBA',(32,40),(70,100,120,255)).save(stream,'PNG');png=stream.getvalue()
    def image():return ('reference.png',png,'image/png')
    def send(client, path, *, expected=202, **kwargs):
        response=client.post(path,**kwargs)
        assert response.status_code==expected,(path,response.status_code,response.text)
        return response.json()
    def settings(identifier):return json.loads((app.OUTPUT/identifier/'settings.json').read_text())
    class Client:
        def __init__(self,base):self.base=base
        def request(self, method, path, **kwargs):
            return requests.request(method,self.base+path,timeout=10,**kwargs)
        def get(self,path,**kwargs):return self.request('GET',path,**kwargs)
        def post(self,path,**kwargs):return self.request('POST',path,**kwargs)
        def put(self,path,**kwargs):return self.request('PUT',path,**kwargs)
    @contextmanager
    def connection():
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app.app,host='127.0.0.1',port=port,log_level='error',lifespan='off'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            for _ in range(100):
                if server.started:break
                time.sleep(.05)
            assert server.started,'Isolated HTTP fixture did not start'
            yield Client('http://127.0.0.1:'+str(port))
        finally:
            server.should_exit=True;thread.join(5)
            assert not thread.is_alive(),'Isolated HTTP fixture did not stop'

    with patch.object(app,'status',return_value={'ready':True}), \
         patch.object(app,'generation_capabilities',return_value=([512,1024],[1024,2048])), \
         patch.object(app,'pixal3d_runtime_status',return_value={'ready':True,'face_projection':{'ready':False}}), \
         patch.object(app,'launch_worker') as launch, connection() as client:
        # No lifespan worker: each route writes only to the isolated queue.
        profiles=client.get('/api/profiles').json()
        assert profiles['default_profile']=='game_d' and profiles['profiles'][0]==before
        experimental=next(p for p in profiles['profiles'] if p['id']=='humanoid_experimental')
        assert experimental['experimental'] and experimental['keep_finishing_source']
        assert experimental['asset_type']=='humanoid'
        assert experimental['settings']['triangles']==250000
        assert experimental['settings']['advanced']['mesh_mode']=='direct'
        assert experimental['settings']['advanced']['texture_guidance']==2
        dense = next(p for p in profiles['profiles'] if p['id'] == DENSE_MASTER)
        assert dense['builtin'] and dense['keep_finishing_source'] and dense['name'] == 'Dense Master'
        dense_settings = dense['settings']
        dense_finish = dict(mesh_mode='direct', pre_simplify=False, repair_holes=False)
        assert dense_settings['resolution'] == 1024 and dense_settings['triangles'] == 1_000_000
        assert dense_settings['advanced'] == {**d, **dense_finish}
        send(client,'/api/profiles/humanoid_experimental',expected=405,json={})
        denied=client.put('/api/profiles/humanoid_experimental',json={'name':'Overwrite','settings':before['settings']})
        assert denied.status_code==403
        denied = client.put(f'/api/profiles/{DENSE_MASTER}', json={'name':'Overwrite','settings':before['settings']})
        assert denied.status_code == 403

        front=send(client,'/api/v1/uploads',expected=201,files={'file':image()})['id']
        side=send(client,'/api/v1/uploads',expected=201,files={'file':image()})['id']
        default=send(client,'/api/v1/generations',json={'upload_id':front})
        s=settings(default['id'])
        assert s['preset']=='game_d' and s['triangles']==28000 and s['texture_size']==2048
        assert s['asset_type']=='object' and not s.get('keep_finishing_source',False)
        assert s['generation_options']==options_for_job({'preset':'game_d'})

        for overrides in ({},{'triangles':12000,'keep_finishing_source':False,'asset_type':'object'}):
            result=send(client,'/api/v1/generations',json={'upload_id':front,'profile_id':'humanoid_experimental','overrides':overrides})
            s=settings(result['id'])
            assert s['preset']=='humanoid_experimental' and s['triangles']==overrides.get('triangles',250000)
            assert s['generation_options']['mesh_mode']=='direct' and s['texture_size']==2048
            assert s['asset_type']=='humanoid' and s['keep_finishing_source'] is True
            assert s['profile']['id']=='humanoid_experimental'
            assert s['profile_snapshot']['generation_options']==s['generation_options']
            assert s['profile_snapshot']['triangles']==s['triangles']
        # The legacy form uses preset as the actual recipe; profile_id remains a label.
        result=send(client,'/api/generate',expected=200,files={'image':image()},data={
            'preset':'game_d','profile_id':'humanoid_experimental','queue_only':'true'})
        s=settings(result['id'])
        assert s['preset']=='game_d' and s['triangles']==28000 and not s.get('keep_finishing_source',False)

        typed_mono = send(client, '/api/v1/generations', json={
            'upload_id': front, 'profile_id': DENSE_MASTER, 'overrides': {'keep_finishing_source': False}})
        multipart_mono = send(client, '/api/generate', expected=200, files={'image': image()}, data={
            'preset': DENSE_MASTER, 'queue_only': 'true', 'keep_finishing_source': 'false'})
        for result in (typed_mono, multipart_mono):
            s = settings(result['id'])
            assert s['preset'] == s['profile']['id'] == s['profile_snapshot']['id'] == DENSE_MASTER
            assert s['keep_finishing_source'] is True and s['asset_type'] == 'object'
            assert s['resolution'] == 1024 and s['triangles'] == 1_000_000 and s['texture_size'] == 2048
            assert s['generation_options'] == {**options_for_job({'preset':'game_d'}), **dense_finish}
            assert s['profile_snapshot']['generation_options'] == s['generation_options']

        cap=app.pixal3d_capabilities()
        assert cap['profile']==standard_profile and cap['default_profile']=='character_pixal'
        assert [p['id'] for p in cap['profiles']]==['character_pixal','humanoid_experimental',DENSE_MASTER]
        files={'image':image(),'left_image':image()}
        for profile in ('character_pixal','humanoid_experimental'):
            result=send(client,'/api/generate-multiview',files=files,data={'profile_id':profile,'keep_finishing_source':'false'})
            s=settings(result['id']);exp=profile=='humanoid_experimental'
            assert s['preset']==profile and s['triangles']==(250000 if exp else 10600)
            assert s['keep_finishing_source'] is exp
            assert s['generation_options']=={**standard_options,'mesh_mode':'direct' if exp else 'remesh'}
            sampling=sampling_options(options_for_job(s),s['seed'],s['settings_version'])
            assert [sampling[k]['steps'] for k in ('structure','shape_low','shape_high','texture')]==[12,20,20,12]
            assert s['generation_options']['texture_guidance']==1
            assert s['profile_snapshot']['generation_options']==s['generation_options']
        typed=send(client,'/api/v1/generations/multiview',json={
            'upload_id':front,'left_upload_id':side,'profile_id':'humanoid_experimental','triangles':12000})
        s=settings(typed['id'])
        assert s['preset']=='humanoid_experimental' and s['triangles']==12000 and s['keep_finishing_source']
        typed_dense = send(client, '/api/v1/generations/multiview', json={
            'upload_id': front, 'left_upload_id': side, 'profile_id': DENSE_MASTER, 'keep_finishing_source': False})
        s = settings(typed_dense['id'])
        assert s['preset'] == s['profile']['id'] == s['profile_snapshot']['id'] == DENSE_MASTER
        assert s['keep_finishing_source'] is True and s['asset_type'] == 'humanoid'
        assert s['resolution'] == 1024 and s['triangles'] == 1_000_000 and s['texture_size'] == 2048
        assert s['generation_options'] == {**standard_options, **dense_finish}
        assert s['profile_snapshot']['generation_options'] == s['generation_options']
        send(client,'/api/generate-multiview',expected=400,files=files,data={'profile_id':'unknown'})
        send(client,'/api/v1/generations/multiview',expected=422,json={'upload_id':front,'left_upload_id':side,'profile_id':'unknown'})
        assert builtin_profiles()[0]==before
        assert app.PIXAL_CHARACTER_OPTIONS==standard_options and app.PIXAL_CHARACTER_PROFILE==standard_profile
        launch.assert_not_called()
    assert 'torch' not in sys.modules
    print('PASS: unchanged D, standard Pixal and Humanoid Experimental; Dense Master mono/multiview recipes, forced cache, snapshots and REST contracts.')
