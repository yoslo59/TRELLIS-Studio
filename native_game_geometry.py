"""Run Character 05 mesh finishing in its isolated native environment."""

from argparse import ArgumentParser
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'runtime/native-game-geometry/runtime.json'


def _runtime():
    try:
        data = json.loads(RUNTIME.read_text(encoding='utf-8-sig'))
        if data['schema_version'] != 1:
            raise ValueError
        paths = {name: (RUNTIME.parent / data[name]).resolve(strict=True)
                 for name in ('operator_source', 'toolkit', 'dependencies', 'cumesh')}
        paths['python'] = (ROOT / data['studio_python']).resolve(strict=True)
        if not (RUNTIME.parent / 'character-05-recipe.json').is_file():
            raise ValueError
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise ValueError('Install the standalone Character 05 geometry component.') from error
    return paths


def runtime_status():
    try:
        _runtime()
        from studio_import import find_blender
        if not find_blender():
            raise ValueError('Install Blender for the Character 05 normal bake.')
    except ValueError as error:
        return {'available': False, 'reason': str(error)}
    return {'available': True, 'reason': ''}


def finish_geometry(raw_mesh, work, progress=None, *, options=None):
    """Run QuadDC, WTiVo, LOD, UV and normal bake with the selected recipe."""
    work = Path(work).resolve()
    paths = _runtime()
    command = [str(paths['python']), '-I', '-B', '-u', str(Path(__file__).resolve()),
               str(Path(raw_mesh).resolve()), str(work)]
    with TemporaryDirectory(prefix='.finish-', dir=work.parent) as directory:
        settings = Path(directory) / 'settings.json'
        nodes = ('325', '327', '328', '335', '337', '338', '341', '342')
        settings.write_text(json.dumps({node: options[node] for node in nodes if options and node in options}),
                            encoding='utf-8')
        command.extend(('--options', str(settings)))
        with subprocess.Popen(command, cwd=work.parent, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace',
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            try:
                failure = 'Character 05 finishing failed. See the generation log.'
                for line in process.stdout:
                    if line.startswith('FINISH_PROGRESS ') and progress is not None:
                        progress(line.removeprefix('FINISH_PROGRESS ').strip())
                    elif line.startswith('FINISH_ERROR '):
                        failure = line.removeprefix('FINISH_ERROR ').strip()
                    else:
                        print(line, end='', flush=True)
                if process.wait() or not (work / 'summary.json').is_file():
                    raise RuntimeError(failure)
            finally:
                if process.poll() is None:
                    process.terminate()
    receipt = json.loads((work / 'summary.json').read_text(encoding='utf-8'))
    return work / 'uv-bake/normal-ready.glb', receipt


def _main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('raw_mesh', type=Path)
    parser.add_argument('work', type=Path)
    parser.add_argument('--options', type=Path)
    args = parser.parse_args()
    raw_mesh, work = args.raw_mesh, args.work
    options = json.loads(args.options.read_text(encoding='utf-8')) if args.options else None
    runtime = _runtime()
    sys.path[:0] = [str(runtime['dependencies']), str(ROOT)]
    import numpy as np
    from native_game_finish import finish_character_mesh
    from studio_import import find_blender
    from wtivo_runtime import WTiVoTimeout

    class BlockGraphRuntime:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.partition('.')[0] in {'comfy', 'comfy_api', 'comfy_extras', 'folder_paths'}:
                raise ImportError('The standalone mesh worker cannot load the ComfyUI application.')

    sys.meta_path.insert(0, BlockGraphRuntime())
    blender = find_blender()
    if not blender:
        raise RuntimeError('Install Blender for the Character 05 normal bake.')
    try:
        with np.load(raw_mesh, allow_pickle=False) as arrays:
            model, result = finish_character_mesh(
                ROOT, runtime['toolkit'], runtime['cumesh'], blender, runtime['operator_source'], work,
                arrays['vertices'], arrays['faces'], options=options,
                progress=lambda label: print('FINISH_PROGRESS ' + label, flush=True),
            )
    except WTiVoTimeout as error:
        print('FINISH_ERROR ' + str(error), flush=True)
        raise SystemExit(1) from None
    if model != work / 'uv-bake/normal-ready.glb':
        raise RuntimeError('Unexpected standalone surface output path.')
    (work / 'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'Surface GLB with {result["triangles"]} triangles.', flush=True)


if __name__ == '__main__':
    _main()
