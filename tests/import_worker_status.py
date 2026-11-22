"""Windows import records survive transient sharing violations."""
import json
from pathlib import Path
from runpy import run_path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import studio_import


with TemporaryDirectory(prefix='trellis-import-test-') as directory:
    job = Path(directory)
    settings = job / 'settings.json'
    status = job / 'status.json'
    settings.write_text('{}', encoding='utf-8')
    (job / 'result.json').write_text(json.dumps(dict(height_m=2, texture_size=0, triangles=12)), encoding='utf-8')
    locked = {status, settings}
    replace = Path.replace

    def try_replace(source, target):
        if target in locked:
            locked.remove(target)
            raise PermissionError('The reader briefly holds the destination.')
        return replace(source, target)

    with (patch.object(Path, 'replace', try_replace),
          patch.object(studio_import, 'find_blender', return_value='blender'),
          patch.object(studio_import, 'validate_glb', return_value={}),
          patch.object(subprocess, 'Popen', return_value=Mock(wait=Mock(return_value=0))),
          patch.object(sys, 'argv', [str(ROOT / 'import_worker.py'), str(job)])):
        run_path(sys.argv[0], run_name='__main__')

    assert not locked, 'Expected a sharing violation on both records.'
    assert json.loads(status.read_text(encoding='utf-8'))['state'] == 'done'
    assert json.loads(settings.read_text(encoding='utf-8'))['triangles'] == 12
    assert not list(job.glob('*.tmp'))
