"""Private preview admission, source revision binding, copy retries and draft cleanup."""
import hashlib
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import face_refine_engine as engine
from face_refine_api import TTL, prune
from server_lan import account, call, login, PASSWORD, serve
from studio_workspace import read, write


def run():
    with TemporaryDirectory(prefix='trellis-face-routes-') as directory:
        root = Path(directory)
        token = 'face-refine-local-fixture'
        with serve(root, token) as (base, _):
            admin = call(base, 'POST', '/server/api/setup', 201,
                         json={**account('admin'), 'token':token}).json()['user']

        with patch('collection_preview.CollectionPreviews.start'), serve(root) as (base, server), patch('face_refine_api.unsupported', return_value=''), \
                patch.object(engine, 'capability', return_value={'available':True,'reason':''}), \
                patch.object(engine, 'suggest_crop', return_value=[.1,.1,.5,.5]), \
                patch.object(engine, 'prepare_portrait', side_effect=lambda src, crop, dst: shutil.copy2(src,dst)):
            users = []
            for name in ('alice','bob'):
                profile = {k:v for k,v in account(name).items() if k!='password'}
                user = server.store.register(profile, PASSWORD)
                server.store.update_user(admin['id'], user['id'], dict(status='active'))
                users.append(user['id'])
            server.runtime.paused = True
            alice, _ = login(base, 'alice')
            bob, _ = login(base, 'bob')
            owner = root / 'users' / users[0]
            model = '2'*32
            folder = owner / 'outputs' / model
            folder.mkdir(parents=True)
            source = folder / 'model.glb'
            source.write_bytes(b'original')
            Image.new('RGB',(64,64),'#888').save(folder / 'input.png')
            write(folder/'settings.json', {'name':'Character','asset_type':'humanoid'})
            write(folder/'status.json', {'state':'done','progress':100})
            write(folder/'metadata.json', {'display_name':'Character','favorite':True})
            url = '/api/jobs/' + model + '/face-refine'
            call(base,'GET',url,401)
            call(base,'GET',url,404,bob)
            data = call(base,'GET',url,200,alice).json()
            assert data['available'] and data['crop']==[.1,.1,.5,.5]
            child_id = '3'*32
            child = owner/'outputs'/child_id
            shutil.copytree(folder,child)
            write(child/'settings.json',dict(name='Derived',source_kind='game',parent_id=model))
            (child/'input.png').unlink()
            options = '/api/jobs/'+child_id+'/face-refine'
            assert call(base,'GET',options,200,alice).json()['reference_url']==data['reference_url']
            original_settings = read(folder/'settings.json')
            write(folder/'settings.json',dict(original_settings,source_kind='import'))
            assert call(base,'GET',options,200,alice).json()['reference_url'] is None
            write(folder/'settings.json',original_settings)
            call(base,'POST',url+'/previews',400,alice,data={'crop':'[0.9,0,1,1]'})
            p = call(base,'POST',url+'/previews',202,alice,data={'crop':'[0,0,1,1]'}).json()['id']
            once = url+'/previews/'+p
            draft = owner/'state'/'face-previews'/p
            assert not (owner/'outputs'/p).exists() and (draft/'portrait.png').is_file()
            call(base,'GET',once,404,bob)
            call(base,'GET',once+'/patch',404,bob)
            call(base,'DELETE',once,404,bob)
            call(base,'POST',once+'/copy',404,bob,json={'strength':.6})
            assert call(base,'GET',once,200,alice).json()['state']=='queued'
            call(base,'GET',once+'/patch',409,alice)
            call(base,'POST',once+'/copy',409,alice,json={'strength':.6})
            private = server.runtime.workspaces[users[0]]
            assert private.job_folder(p) == draft and private.job_spec(draft)[0]=='face_refine_worker.py'
            assert any(row['id']==p and row['state']=='reserved' for row in server.store.jobs(users[0]))
            # Simulate completion of the numerical worker (separate geometry and GPU tests cover the calculation).
            write(draft/'status.json', dict(state='done',progress=100,stage='Ready',count=1,
                                            vertex_count=3,face_center=[0,1,0],face_span=.2))
            (draft/'patch.bin').write_bytes(b'FRP1-test')
            assert call(base,'GET',once+'/patch',200,alice).content == b'FRP1-test'
            call(base,'POST',once+'/copy',400,alice,json={'strength':1.1})
            source.write_bytes(b'changed')
            call(base,'POST',once+'/copy',409,alice,json={'strength':.6})
            source.write_bytes(b'original')
            saved = call(base,'POST',once+'/copy',202,alice,json={'strength':.6}).json()['id']
            # The copy worker pins its parent; a network retry still returns the initial copy.
            with patch.object(private.studio, 'active_job', side_effect=lambda identifier: identifier==model):
                call(base, 'GET', url, 409, alice)
                retry = call(base,'POST',once+'/copy',202,alice,json={'strength':.6}).json()
                assert retry['id']==saved
            call(base,'POST',once+'/copy',409,alice,json={'strength':.5})
            copy = owner/'outputs'/saved
            assert read(copy/'settings.json')['parent_id']==model and read(copy/'settings.json')['face_refinement']['strength']==.6
            assert read(copy/'metadata.json')['asset_type']=='humanoid'
            assert read(copy/'face-request.json')['sha256']==hashlib.sha256(b'original').hexdigest()
            assert private.job_spec(copy)[0]=='face_refine_worker.py'
            call(base,'DELETE',once,200,alice)
            assert not (draft/'patch.bin').exists() and (copy/'patch.bin').exists() and source.read_bytes()==b'original'
            call(base,'GET',once+'/patch',410,alice)
            past = time.time()-TTL-60
            os.utime(draft/'status.json',(past,past))
            prune(vars(private.studio),time.time())
            assert not draft.exists() and (copy/'patch.bin').exists()
            for tmp in ('face-copy.tmp.glb','face-temp1234.part'):
                (copy/tmp).write_bytes(b'partial')
            call(base,'POST','/api/jobs/'+saved+'/cancel',200,alice)
            assert read(copy/'status.json')['state']=='cancelled'
            assert all(not (copy/name).exists() for name in ('face-copy.tmp.glb','face-temp1234.part',
                                                            'face-request.json','patch.bin'))
            assert (copy/'input.png').is_file() and source.read_bytes()==b'original'
            print('PASS: scoped drafts, admission, digest, intensity, idempotent copy, discard and expiry')


if __name__=='__main__':
    run()
