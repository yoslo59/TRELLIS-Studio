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
    from generation_profiles import ProfileStore, builtin_profiles
    from generation_options import DENSE_MASTER, DEFAULT_GENERATION, DEFAULT_PROFILE, options_for_job
    from pixal3d_worker import sampling_options

    before = copy.deepcopy(builtin_profiles()[0])
    refinement_profiles = {p['id']: p['settings']['multiview_texture_refinement'] for p in builtin_profiles()}
    assert {identifier for identifier, enabled in refinement_profiles.items() if enabled} == {'high_detail', 'figure_master'}
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
        with patch.object(app, 'workflow_capability', return_value={'available': False, 'reason': 'Optional runtime missing.'}):
            profiles=client.get('/api/profiles').json()
        assert profiles['default_profile']=='game_d' and profiles['profiles'][0]==before
        assert profiles['startup_profile']=='high_detail'
        assert {p['id'] for p in profiles['profiles'] if not p.get('retired')} == {
            'high_detail', 'figure_master', 'game_asset_native', 'high_detail_native'}
        assert {p['id'] for p in profiles['profiles'] if p.get('retired')} == {'game_d', 'dense_master', 'detail_study'}
        high = next(p for p in profiles['profiles'] if p['id'] == 'high_detail')
        assert high['keep_finishing_source'] is True
        assert high['settings']['triangles'] == 300_000 and high['settings']['texture_size'] == 2048
        high_options = high['settings']['advanced']
        assert [high_options[f'{phase}_steps'] for phase in ('structure', 'shape', 'texture')] == [12, 25, 12]
        assert high_options['mesh_mode'] == 'remesh' and high_options['remesh_resolution'] == 1024
        assert not any(p['id']=='humanoid_experimental' for p in profiles['profiles'])
        experimental=next(p for p in app.builtin_profiles() if p['id']=='humanoid_experimental')
        assert experimental['experimental'] and experimental['keep_finishing_source']
        assert experimental['asset_type']=='humanoid'
        assert experimental['settings']['triangles']==250000
        assert experimental['settings']['advanced']['mesh_mode']=='direct'
        assert experimental['settings']['advanced']['texture_guidance']==2
        dense = next(p for p in profiles['profiles'] if p['id'] == DENSE_MASTER)
        assert dense['builtin'] and dense['keep_finishing_source'] and dense['name'] == 'Dense Master'
        dense_settings = dense['settings']
        dense_finish = dict(mesh_mode='direct', pre_simplify=False, repair_holes=False)
        assert dense_settings['resolution'] == 1024 and dense_settings['triangles'] == 1_200_000
        assert dense_settings['advanced'] == {**d, **dense_finish}
        send(client,'/api/profiles/humanoid_experimental',expected=405,json={})
        denied=client.put('/api/profiles/humanoid_experimental',json={'name':'Overwrite','settings':before['settings']})
        assert denied.status_code==403
        denied = client.put(f'/api/profiles/{DENSE_MASTER}', json={'name':'Overwrite','settings':before['settings']})
        assert denied.status_code == 403
        preference = 'multiview_texture_refinement'
        personal = send(client, '/api/profiles', expected=201, json={
            'name': 'Custom texture recipe', 'settings': {**before['settings'], preference: True}})
        profile_url = f"/api/profiles/{personal['id']}"
        assert personal['settings'][preference] is True
        stored = next(p for p in client.get('/api/profiles').json()['profiles'] if p['id'] == personal['id'])
        assert stored['settings'][preference] is True
        updated = client.put(profile_url, json={'name': personal['name'], 'settings': {**personal['settings'], preference: False}})
        assert updated.status_code == 200 and updated.json()['settings'][preference] is False
        malformed = client.put(profile_url, json={'name': personal['name'], 'settings': {**personal['settings'], preference: 'true'}})
        assert malformed.status_code == 400
        assert client.request('DELETE', profile_url).status_code == 200

        legacy_settings = {key: value for key, value in before['settings'].items() if key != preference}
        with TemporaryDirectory(prefix='trellis-legacy-profiles-') as directory:
            path = Path(directory) / 'profiles.json'
            path.write_text(json.dumps(dict(version=1, profiles=[dict(
                id='user_legacy', name='Legacy recipe', settings=legacy_settings)])), encoding='utf-8')
            assert ProfileStore(path).list()[-1]['settings'].get(preference, False) is False

        front=send(client,'/api/v1/uploads',expected=201,files={'file':image()})['id']
        side=send(client,'/api/v1/uploads',expected=201,files={'file':image()})['id']
        default=send(client,'/api/v1/generations',json={'upload_id':front})
        s=settings(default['id'])
        assert s['preset']=='game_d' and s['triangles']==28000 and s['texture_size']==2048
        assert s['asset_type']=='object' and not s.get('keep_finishing_source',False)
        assert s['generation_options']==options_for_job({'preset':'game_d'})
        for keep in (True, False):
            overrides = {} if keep else {'keep_finishing_source': False}
            result = send(client, '/api/v1/generations', json={
                'upload_id': front, 'profile_id': 'high_detail', 'overrides': overrides})
            recipe = settings(result['id'])
            assert recipe.get('keep_finishing_source', False) is keep
            assert recipe['triangles'] == 300_000 and recipe['generation_options']['shape_steps'] == 25

        with patch.object(app, 'multiview_texture_capability', return_value={'available': True, 'reason': ''}):
            figure = send(client, '/api/v1/generations', json={
                'upload_id': front, 'profile_id': 'figure_master'})
            figure_recipe = settings(figure['id'])
            assert figure_recipe['texture_method'] == 'multiview' and figure_recipe['keep_finishing_source']
            assert figure_recipe['resolution'] == 1024 and figure_recipe['triangles'] == 800_000
            f = figure_recipe['generation_options']
            assert [f[k + '_steps'] for k in ('structure', 'shape', 'texture')] == [30, 35, 12]
            assert [f[k + '_guidance'] for k in ('structure', 'shape')] == [6.5, 6.5]
            assert [f[k + '_rescale'] for k in ('structure', 'shape')] == [.2, .2]
            assert [f[k + '_rescale_t'] for k in ('structure', 'shape')] == [4, 4]
            assert f['mesh_mode'] == 'remesh' and f['remesh_resolution'] == 1024 and not f['pre_simplify']
            assert not figure_recipe.get('retexture_after_refinement')
            for profile_id, override, enabled in (
                    ('high_detail', {}, True),
                    ('high_detail', {preference: False}, False),
                    ('game_d', {preference: True}, True)):
                queued = send(client, '/api/v1/generations', json={
                    'upload_id': front, 'profile_id': profile_id, **override})
                actual = settings(queued['id'])
                assert actual.get(preference, False) is enabled
                assert actual['texture_size'] == 2048
            send(client, '/api/v1/generations', expected=422, json={
                'upload_id': front, preference: 'true'})
        with patch.object(app, 'ultratex_texture_capability', return_value={'available': True, 'reason': ''}):
            ultra = send(client, '/api/v1/generations', json={
                'upload_id': front, 'profile_id': 'high_detail', 'texture_method': 'ultratex',
                'multiview_texture_refinement': True})
            ultra_recipe = settings(ultra['id'])
            assert ultra_recipe['texture_method'] == 'ultratex' and not ultra_recipe.get(preference)
            send(client, '/api/v1/generations', expected=400, json={
                'upload_id': front, 'texture_method': 'ultratex', 'overrides': {'texture_size': 4096}})
            send(client, '/api/v1/generations/multiview', expected=400, json={
                'upload_id': front, 'left_upload_id': side, 'texture_method': 'ultratex'})
            send(client, '/api/v1/generations', expected=400, json={
                'upload_id': front, 'profile_id': 'figure_master', 'engine': 'pixal3d_single'})
            custom = send(client, '/api/profiles', expected=201, json={
                'name': 'UltraTex preference', 'settings': {**before['settings'], 'texture_method': 'ultratex'}})
            assert custom['settings']['texture_method'] == 'ultratex' and not custom['settings'][preference]
            assert client.request('DELETE', '/api/profiles/' + custom['id']).status_code == 200
        with patch.object(app, 'multiview_texture_capability',
                          return_value={'available': False, 'reason': 'Runtime missing.'}):
            fallback = send(client, '/api/v1/generations', json={
                'upload_id': front, 'profile_id': 'high_detail'})
            assert not settings(fallback['id']).get(preference)
            send(client, '/api/v1/generations', expected=503, json={
                'upload_id': front, preference: True})

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
            assert s['adaptive_finishing'] is s['profile_snapshot']['adaptive_finishing'] is True
            assert 'repair_surface' not in s and 'repair_surface' not in s['profile_snapshot']
            assert s['resolution'] == 1024 and s['triangles'] == 1_200_000 and s['texture_size'] == 2048
            assert s['generation_options'] == {**options_for_job({'preset':'game_d'}), **dense_finish}
            assert s['profile_snapshot']['generation_options'] == s['generation_options']
        replayed_mono = send(client, '/api/v1/generations', json={
            'upload_id': front, 'profile_id': DENSE_MASTER, 'overrides': {'triangles': 1_000_000}})
        s = settings(replayed_mono['id'])
        assert s['triangles'] == s['profile_snapshot']['triangles'] == 1_000_000
        assert s['adaptive_finishing'] is True

        cap=app.pixal3d_capabilities()
        assert cap['profile']==standard_profile and cap['default_profile']=='character_pixal'
        assert [p['id'] for p in cap['profiles']]==['character_pixal',DENSE_MASTER]
        assert cap['profiles'][-1]['triangles'] == 1_200_000
        for keep in (True, False):
            recipe = dict(upload_id=front, left_upload_id=side, profile_id='high_detail')
            if not keep:
                recipe['keep_finishing_source'] = False
            result = send(client, '/api/v1/generations/multiview', json=recipe)
            assert settings(result['id'])['keep_finishing_source'] is keep
        files={'image':image(),'left_image':image()}
        for profile in ('character_pixal','humanoid_experimental'):
            result=send(client,'/api/generate-multiview',files=files,data={'profile_id':profile,'keep_finishing_source':'false'})
            s=settings(result['id']);exp=profile=='humanoid_experimental'
            assert s['preset']==profile and s['triangles']==(250000 if exp else 10600)
            assert s['keep_finishing_source'] is exp
            assert 'adaptive_finishing' not in s
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
        assert s['adaptive_finishing'] is s['profile_snapshot']['adaptive_finishing'] is True
        assert 'repair_surface' not in s and 'repair_surface' not in s['profile_snapshot']
        assert s['resolution'] == 1024 and s['triangles'] == 1_200_000 and s['texture_size'] == 2048
        assert s['generation_options'] == {**standard_options, **dense_finish}
        assert s['profile_snapshot']['generation_options'] == s['generation_options']
        replayed_multiview = send(client, '/api/v1/generations/multiview', json={
            'upload_id': front, 'left_upload_id': side, 'profile_id': DENSE_MASTER, 'triangles': 1_000_000})
        s = settings(replayed_multiview['id'])
        assert s['triangles'] == s['profile_snapshot']['triangles'] == 1_000_000
        assert s['adaptive_finishing'] is True
        repaired = [
            send(client, '/api/v1/generations', json={
                'upload_id': front, 'profile_id': DENSE_MASTER, 'overrides': {'repair_surface': True}}),
            send(client, '/api/generate', expected=200, files={'image': image()}, data={
                'preset': DENSE_MASTER, 'queue_only': 'true', 'repair_surface': 'true'}),
            send(client, '/api/v1/generations/multiview', json={
                'upload_id': front, 'left_upload_id': side, 'profile_id': DENSE_MASTER, 'repair_surface': True}),
            send(client, '/api/generate-multiview', files=files, data={
                'profile_id': DENSE_MASTER, 'repair_surface': 'true'}),
        ]
        for result in repaired:
            s = settings(result['id'])
            assert s['repair_surface'] is True and s['profile_snapshot']['repair_surface'] is True
            assert s['adaptive_finishing'] is True
            assert s['keep_finishing_source'] is True and s['triangles'] == 1_200_000
            assert s['generation_options']['mesh_mode'] == 'direct'
            effective = options_for_job(s)
            assert effective['mesh_mode'] == 'remesh' and effective['remesh_project'] == .45
            assert effective['repair_holes'] is True and effective['pre_simplify'] is False
        send(client, '/api/v1/generations', expected=400, json={
            'upload_id': front, 'overrides': {'repair_surface': True}})
        send(client, '/api/v1/generations', expected=400, json={
            'upload_id': front, 'profile_id': DENSE_MASTER, 'overrides': {'repair_surface': 'true'}})
        send(client, '/api/v1/generations/multiview', expected=400, json={
            'upload_id': front, 'left_upload_id': side, 'repair_surface': True})
        reconstructed = [
            send(client, '/api/v1/generations', json={
                'upload_id': front, 'profile_id': DENSE_MASTER, 'overrides': {'hd_reconstruction': True}}),
            send(client, '/api/generate', expected=200, files={'image': image()}, data={
                'preset': DENSE_MASTER, 'queue_only': 'true', 'hd_reconstruction': 'true'}),
            send(client, '/api/v1/generations/multiview', json={
                'upload_id': front, 'left_upload_id': side, 'profile_id': DENSE_MASTER, 'hd_reconstruction': True}),
            send(client, '/api/generate-multiview', files=files, data={
                'profile_id': DENSE_MASTER, 'hd_reconstruction': 'true'}),
        ]
        for result in reconstructed:
            s = settings(result['id'])
            assert s['hd_reconstruction'] is True and s['profile_snapshot']['hd_reconstruction'] is True
            assert 'repair_surface' not in s and 'adaptive_finishing' not in s
            assert s['keep_finishing_source'] is True and s['triangles'] == 1_200_000
            effective = options_for_job(s)
            assert effective['mesh_mode'] == 'remesh' and effective['remesh_resolution'] == 768
            assert effective['remesh_project'] == 0 and effective['remesh_band'] == 1
            assert effective['repair_holes'] is True and effective['pre_simplify'] is False
        for overrides in ({'resolution': 512}, {'repair_surface': True}):
            send(client, '/api/v1/generations', expected=400, json={
                'upload_id': front, 'profile_id': DENSE_MASTER,
                'overrides': {'hd_reconstruction': True, **overrides}})
        send(client, '/api/v1/generations', expected=400, json={
            'upload_id': front, 'overrides': {'hd_reconstruction': True}})
        send(client, '/api/v1/generations/multiview', expected=422, json={
            'upload_id': front, 'left_upload_id': side, 'profile_id': DENSE_MASTER, 'hd_reconstruction': 'true'})
        send(client,'/api/generate-multiview',expected=400,files=files,data={'profile_id':'unknown'})
        send(client,'/api/v1/generations/multiview',expected=422,json={'upload_id':front,'left_upload_id':side,'profile_id':'unknown'})
        with patch.object(app, 'workflow_capability', return_value={'available': True, 'reason': ''}), \
             patch('native_high_detail.ultratex_status', return_value={'available': True}) as ultratex:
            catalogue = client.get('/api/v1/profiles').json()
            assert catalogue['startup_profile'] == 'high_detail_native'
            workflows = {p['id']: p for p in catalogue['profiles'] if 'workflow' in p}
            assert set(workflows) == {'game_asset_native', 'high_detail_native'}
            high = workflows['high_detail_native']
            assert high['settings']['texture_method'] == 'ultratex' and high['workflow']['texture_size'] == 2048
            materials = next(stage for stage in high['workflow']['advanced'] if stage['id'] == '25')
            assert next(field for field in materials['fields'] if field['name'] == 'texture_method')['default'] == 'ultratex'
            for profile_id, seed, stage in [('game_asset_native', 56, '3'), ('high_detail_native', 12345, '17')]:
                assert workflows[profile_id]['workflow']['available']
                assert 'Comfy' not in workflows[profile_id]['name']
                accepted = send(client, '/api/v1/generations', json={'upload_id': front, 'profile_id': profile_id,
                    'overrides': {'workflow_options': {stage: {'seed': seed + 1}}}})
                identifier = accepted['id']
                assert settings(identifier)['seed'] == seed + 1
                assert settings(identifier)['workflow_id'] == profile_id
                assert settings(identifier)['texture_method'] == 'ultratex' and settings(identifier)['texture_size'] == 2048
                source_name = settings(identifier)['workflow_input']
                assert (app.OUTPUT / identifier / source_name).read_bytes() == png
                get_source = client.get(f'/api/jobs/{identifier}/files/{source_name}')
                assert get_source.status_code == 200 and get_source.content == png
                send(client, '/api/jobs/' + identifier + '/cancel', expected=200)
                send(client, '/api/queue/' + identifier + '/retry', expected=400, json={'texture_size': 1024})
                retry = send(client, '/api/queue/' + identifier + '/retry', json={'height_m': 1.5})
                assert settings(retry['id'])['seed'] == seed + 1 and settings(retry['id'])['height_m'] == 1.5
                assert (app.OUTPUT / retry['id'] / settings(identifier)['workflow_input']).read_bytes() == png
                send(client, '/api/v1/generations', expected=400, json={
                    'upload_id': front, 'profile_id': profile_id, 'seed': 42})
            high_request = {'upload_id': front, 'profile_id': 'high_detail_native'}
            explicit = send(client, '/api/v1/generations', json={**high_request,
                'overrides': {'workflow_options': {'25': {'texture_method': 'trellis'}}}})
            send(client, '/api/jobs/' + explicit['id'] + '/cancel', expected=200)
            replay = send(client, '/api/queue/' + explicit['id'] + '/retry', json={'height_m': 2})
            assert settings(replay['id'])['texture_method'] == 'trellis' and settings(replay['id'])['texture_size'] == 4096
            ultratex.return_value = {'available': False, 'reason': 'UltraTex is unavailable.'}
            fallback = send(client, '/api/v1/generations', json=high_request)
            assert settings(fallback['id'])['texture_method'] == 'trellis' and settings(fallback['id'])['texture_size'] == 4096
            high = next(p for p in client.get('/api/v1/profiles').json()['profiles'] if p['id'] == 'high_detail_native')
            assert high['workflow']['texture_size'] == high['settings']['texture_size'] == 4096
            assert high['settings']['texture_method'] == 'trellis'
            send(client, '/api/generate', expected=409, files={'image': image()},
                 data={'profile_id': 'game_asset_comfy', 'queue_only': 'true'})
        with patch.object(app, 'workflow_capability', return_value={'available': False, 'reason': 'Component unavailable.'}):
            send(client, '/api/v1/generations', expected=503, json={
                'upload_id': front, 'profile_id': 'game_asset_native'})
        assert builtin_profiles()[0]==before
        assert app.PIXAL_CHARACTER_OPTIONS==standard_options and app.PIXAL_CHARACTER_PROFILE==standard_profile
        launch.assert_not_called()
    assert 'torch' not in sys.modules
    print('PASS: legacy recipes, Dense Master and standalone workflow settings across submissions and retries.')
