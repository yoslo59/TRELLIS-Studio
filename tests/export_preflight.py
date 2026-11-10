"""Exercise budgets, topology and the checksum guard against a real portable export."""

import asyncio
import json
import os
import struct
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import trimesh
from fastapi import HTTPException
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def request(payload):
    body = json.dumps(payload).encode()

    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}

    return Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)


def fixture(folder):
    mesh = trimesh.Trimesh(
        vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, -1, 0]],
        faces=[[0, 1, 2], [0, 1, 3], [0, 1, 4]],
        process=False,
    )
    scene = trimesh.Scene()
    scene.add_geometry(mesh, geom_name='Open mesh', node_name='First instance')
    offset = np.eye(4)
    offset[0, 3] = 3
    scene.graph.update(frame_to='Second instance', matrix=offset, geometry='Open mesh')
    folder.mkdir(parents=True)
    (folder / 'model.glb').write_bytes(scene.export(file_type='glb'))
    (folder / 'LODs').mkdir()
    (folder / 'LODs' / 'LOD1.glb').write_bytes(mesh.export(file_type='glb'))
    (folder / 'lod-manifest.json').write_text(json.dumps(
        {'levels': [{'level': 0, 'file': 'LOD0.glb'}, {'level': 1, 'file': 'LOD1.glb', 'triangles': 99}]}
    ), encoding='utf-8')


def change_bytes_keep_stat(path):
    stat = path.stat()
    raw = bytearray(path.read_bytes())
    json_size = struct.unpack_from('<I', raw, 12)[0]
    data = json.loads(raw[20:20 + json_size])
    primitive = data['meshes'][0]['primitives'][0]
    accessor = data['accessors'][primitive['attributes']['POSITION']]
    view = data['bufferViews'][accessor['bufferView']]
    start = 28 + json_size + view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
    struct.pack_into('<f', raw, start, .125)
    path.write_bytes(raw)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


async def check(app):
    identifier = 'a' * 32
    folder = app.OUTPUT / identifier
    fixture(folder)
    app.write_json(folder / 'settings.json', {'name': 'Open fixture', 'asset_type': 'imported'})
    app.write_json(folder / 'metadata.json', {'review_state': 'retained'})
    app.write_json(folder / 'status.json', {'state': 'done', 'stage': 'Fixture', 'progress': 100})
    payload = {'ids': [identifier], 'target': 'glb', 'budget': {
        'usage': 'static', 'max_triangles': 4, 'max_lod_triangles': [4, 2],
        'expected_height_m': 1, 'pivot': 'ground',
    }}
    result = await app.export_preflight(request(payload))
    model = result['models'][0]
    rows = {row['label']: row for row in model['checks']}
    assert model['human_review'] == 'retained' and model['status'] == 'attention'
    assert rows['Triangles · LOD 0']['detail'] == '6 / 4'
    assert rows['Triangles · LOD 1']['detail'] == '3 / 4'
    assert rows['Triangles · LOD 2']['status'] == 'attention'
    assert rows['Width × height × depth']['status'] == rows['Pivot']['status'] == 'attention'
    measured = json.loads((folder / 'Diagnostics' / 'report.json').read_text(encoding='utf-8'))
    edges = measured['edge_overlay']['positions']
    assert len(edges['nonmanifold']) == 12 and max(edges['nonmanifold'][::3]) == 4
    assert len(edges['boundary']) == 72
    try:
        await app.export_preflight(request({**payload, 'budget': {'max_triangles': -1}}))
    except HTTPException as error:
        assert error.status_code == 400
    else:
        raise AssertionError('A negative triangle budget was accepted.')

    async def package(digest=None):
        body = {'ids': [identifier], 'target': 'glb'}
        if digest:
            body['preflight'] = {'budget': result['budget'], 'source_hashes': {identifier: digest}}
        task = (await app.create_export(request(body)))['id']
        app.EXPORT_PROCESS.wait(timeout=30)
        return app.export_status(task), app.EXPORT_ROOT / task / 'package.zip'

    # Same size and mtime still require a fresh diagnostic hash and a new export check.
    old_digest = model['source_glb_sha256']
    change_bytes_keep_stat(folder / 'model.glb')
    status, archive = await package(old_digest)
    assert status['state'] == 'error' and 'changed after' in status['error']
    assert not archive.exists()
    status, archive = await package()
    assert status['state'] == 'done'
    with zipfile.ZipFile(archive) as packaged:
        assert not any(name.endswith('/diagnostics.json') for name in packaged.namelist())

    fresh = (await app.export_preflight(request(payload)))['models'][0]['source_glb_sha256']
    assert fresh != old_digest
    status, archive = await package(fresh)
    assert status['state'] == 'done'
    with zipfile.ZipFile(archive) as packaged:
        model_file = next(name for name in packaged.namelist() if name.endswith('/studio-export.json') and '/LODs/' not in name)
        manifest = json.loads(packaged.read(model_file))
        assert manifest['source_glb_sha256'] == manifest['preflight']['source_glb_sha256'] == fresh
        assert manifest['preflight']['status'] == 'attention'
        assert manifest['preflight']['human_review'] == 'retained'
        assert packaged.read(model_file.replace('studio-export.json', 'model.glb')) == (folder / 'model.glb').read_bytes()
    print('PASS: instanced counts, measured LOD files, 3D edge locations, invalid budgets, stale digest, legacy export and verified ZIP')


if __name__ == '__main__':
    with TemporaryDirectory(prefix='trellis-preflight-test-') as temp:
        for key, directory in [('OUTPUT_DIR', 'outputs'), ('TRASH_DIR', 'trash'), ('EXPORT_DIR', 'exports'),
                               ('STATE_DIR', 'state'), ('PROFILES_FILE', 'profiles.json')]:
            os.environ['TRELLIS_' + key] = str(Path(temp) / directory)
        import app
        try:
            asyncio.run(check(app))
        finally:
            if app.EXPORT_PROCESS and app.EXPORT_PROCESS.poll() is None:
                app.stop_export()
            if app.EXPORT_LOG:
                app.EXPORT_LOG.close()
