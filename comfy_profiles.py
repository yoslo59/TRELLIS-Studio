"""Run pinned ComfyUI generation profiles in a disposable native process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter, sleep
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
RECIPES = ROOT / 'comfy_workflows'
CONFIG_PATH = ROOT / 'config' / 'comfy-runtime.json'
COMFY_VERSION = '0.39.2'
LOAD_TIMEOUT = 120
U2NET_WEIGHT = (175997641, '8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491')
PROFILES = {
    'game_asset_comfy': {
        'recipe': 'game-asset-character-05.json',
        'image_node': '122',
        'output_node': '386',
        'output_prefix': 'PixelArtistry_char_final',
        'timeout': 3600,
        'stages': {
            '302': (13, 'Cropping the reference with BiRefNet'),
            '56': (19, 'Estimating reference geometry with MoGe'),
            '3': (27, 'Sampling the Pixal3D sparse structure'),
            '18': (34, 'Sampling the Pixal3D shape'),
            '23': (43, 'Refining the Pixal3D shape at 1536'),
            '325': (52, 'Reconstructing the mesh with QuadDC 1536'),
            '327': (60, 'Rebuilding the watertight surface with WTiVo'),
            '328': (70, 'Preparing the high-poly bake source'),
            '335': (76, 'Reducing the character to 12,000 triangles'),
            '337': (81, 'Unwrapping the low-poly mesh'),
            '341': (85, 'Baking the 2K normal map'),
            '358': (90, 'Generating 12 UltraTex color views'),
            '364': (94, 'Generating six UltraTex material views'),
            '377': (97, 'Baking UltraTex materials at 2K'),
            '386': (99, 'Packaging the finished character'),
        },
    },
    'high_detail_comfy': {
        'recipe': 'high-detail-gguf.json',
        'image_node': '13',
        'output_node': '26',
        'output_prefix': 'StudioHighDetail_Textured',
        'timeout': 5400,
        'stages': {
            '12': (13, 'Preparing the reference image'),
            '17': (25, 'Generating the dense TRELLIS.2 GGUF surface'),
            '41': (46, 'Reconstructing the dense surface with QuadDC'),
            '36': (56, 'Closing openings with MeshLib'),
            '20': (62, 'Refining the dense mesh with TRELLIS.2 GGUF'),
            '42': (75, 'Reconstructing the refined surface'),
            '37': (82, 'Finishing the refined geometry with MeshLib'),
            '25': (88, 'Generating 4K TRELLIS.2 materials'),
            '26': (98, 'Packaging the textured high-poly mesh'),
        },
    },
}
ENVIRONMENT_KEYS = frozenset({
    'CC', 'CUDA_PATH', 'U2NET_HOME', 'ATTN_BACKEND', 'SPARSE_ATTN_BACKEND',
    'TRITON_CACHE_DIR',
})


def _read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _runtime(profile_id, config_path):
    if profile_id not in PROFILES:
        raise ValueError('Unknown ComfyUI generation profile.')
    path = Path(config_path or CONFIG_PATH).resolve()
    if not path.is_file():
        raise ValueError('Configure the optional native ComfyUI generation runtime.')
    try:
        config = _read_json(path)
        if config['schema'] != 1:
            raise ValueError
        entry = config['profiles'][profile_id]
        if not isinstance(entry, dict):
            raise ValueError

        def runtime_path(value):
            return str((path.parent / value).resolve())

        runtime = {name: runtime_path(entry[name]) for name in ('python', 'comfy', 'models', 'custom_nodes')}
        runtime['python_paths'] = [runtime_path(value) for value in entry.get('python_paths', [])]
        runtime['environment'] = {
            name: value for name, value in entry.get('environment', {}).items()
            if name in ENVIRONMENT_KEYS and isinstance(value, str)
        }
        runtime['low_vram'] = entry.get('low_vram', profile_id == 'high_detail_comfy') is True
        if entry.get('blender'):
            runtime['blender'] = runtime_path(entry['blender'])
        if not Path(runtime['python']).is_file() or not (Path(runtime['comfy']) / 'main.py').is_file():
            raise ValueError('The configured ComfyUI Python or program was not found.')
        if not all(Path(folder).is_dir() for folder in (runtime['models'], runtime['custom_nodes'], *runtime['python_paths'])):
            raise ValueError('The configured ComfyUI model, node or dependency folder was not found.')
        if profile_id == 'game_asset_comfy' and not Path(runtime.get('blender', '')).is_file():
            raise ValueError('Configure Blender for the ComfyUI game asset profile.')
        if profile_id == 'high_detail_comfy':
            u2net_home = runtime['environment'].get('U2NET_HOME') or os.environ.get('U2NET_HOME')
            if not u2net_home:
                u2net_home = Path(os.environ.get('XDG_DATA_HOME', Path.home())) / '.u2net'
            u2net_home = Path(u2net_home).expanduser().resolve()
            background = u2net_home / 'u2net.onnx'
            if not background.is_file() or background.stat().st_size != U2NET_WEIGHT[0]:
                raise ValueError('The ComfyUI high detail profile needs its local U2NET background-removal weight.')
            runtime['environment']['U2NET_HOME'] = str(u2net_home)
        return runtime
    except (OSError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError('Configure the optional native ComfyUI runtime for this profile.') from error
    except ValueError as error:
        if not str(error):
            raise ValueError('The native ComfyUI runtime configuration has an unsupported format.') from None
        raise


def runtime_status(profile_id, *, config_path=None):
    """Report whether this optional profile has been configured locally."""
    try:
        _runtime(profile_id, config_path)
        if not (RECIPES / PROFILES[profile_id]['recipe']).is_file():
            raise ValueError('The pinned ComfyUI workflow recipe was not found.')
    except ValueError as error:
        return {'available': False, 'reason': str(error)}
    return {'available': True, 'reason': ''}


def _prepare_payload(profile_id, reference, runtime, recipe):
    profile = PROFILES[profile_id]
    payload = recipe['payload']
    prompt = payload['prompt']
    prompt[profile['image_node']]['inputs']['image'] = reference.name
    if profile_id == 'game_asset_comfy':
        blender = Path(runtime['blender']).as_posix()
        prompt['324']['inputs']['value'] = blender
        graph = payload.get('extra_data', {}).get('extra_pnginfo', {}).get('workflow')
        if graph:
            for node in graph['nodes']:
                if node['id'] == 122:
                    node['widgets_values'][0] = reference.name
                elif node['id'] == 324:
                    node['widgets_values'][0] = blender
    return payload


def _environment(profile_id, runtime, work):
    environment = os.environ.copy()
    for key in tuple(environment):
        if key in ('PYTHONHOME', 'PYTHONPATH') or key.startswith('UTX_'):
            environment.pop(key, None)
    if profile_id == 'game_asset_comfy':
        # The standalone Character 05 runtime chooses its own native CUDA and attention defaults.
        for key in ('PYTORCH_CUDA_ALLOC_CONF', 'TORCHDYNAMO_DISABLE', 'ATTN_BACKEND', 'SPARSE_ATTN_BACKEND'):
            environment.pop(key, None)
    environment.update({
        'TMP': str(work / 'scratch'),
        'TEMP': str(work / 'scratch'),
        'HF_HUB_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1',
        'DO_NOT_TRACK': '1',
        'TRANSFORMERS_OFFLINE': '1',
        'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONDONTWRITEBYTECODE': '1',
        'TRITON_CACHE_DIR': str(work / 'triton'),
        'TORCHINDUCTOR_CACHE_DIR': str(work / 'inductor'),
        'FLEX_GEMM_AUTOTUNE_CACHE_PATH': str(work / 'flex-gemm-cache.json'),
    })
    environment.update(runtime['environment'])
    return environment


def _stop_process(process):
    if process.poll() is None:
        from ultratex_runtime import stop_worker

        stop_worker(process)


def _read_progress(path, offset, progress, last_percent):
    if not path.is_file():
        return offset, last_percent
    with path.open('rb') as stream:
        stream.seek(offset)
        for line in stream:
            if not line.endswith(b'\n'):
                break
            offset += len(line)
            percentage, label = json.loads(line)
            if percentage > last_percent:
                progress(label, percentage)
                last_percent = percentage
    return offset, last_percent


def _validate_glb(path):
    with path.open('rb') as stream:
        header = stream.read(12)
    if len(header) != 12:
        raise ValueError('The native ComfyUI output is an incomplete GLB.')
    magic, version, length = struct.unpack('<4sII', header)
    if magic != b'glTF' or version != 2 or length != path.stat().st_size:
        raise ValueError('The native ComfyUI output is an invalid GLB.')


def run_profile(profile_id, image_path, output_path, job_dir, progress=None, *,
                config_path=None, cancelled=None):
    """Run one pinned profile and atomically publish its verified GLB."""
    runtime = _runtime(profile_id, config_path)
    if profile_id == 'high_detail_comfy':
        background = Path(runtime['environment']['U2NET_HOME']) / 'u2net.onnx'
        if _file_hash(background) != U2NET_WEIGHT[1]:
            raise ValueError('The local U2NET background-removal weight does not match the pinned model.')
    profile = PROFILES[profile_id]
    progress = progress or (lambda _label, _percent: None)
    cancelled = cancelled or (lambda: False)
    source, destination, job = (Path(value).resolve() for value in (image_path, output_path, job_dir))
    if not source.is_file() or source.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
        raise ValueError('The ComfyUI profile requires a PNG, JPEG or WebP input image.')
    if not destination.is_relative_to(job) or destination.suffix.lower() != '.glb' or destination == source:
        raise ValueError('The ComfyUI GLB must be saved within the current generation folder.')
    recipe_path = RECIPES / profile['recipe']
    recipe = _read_json(recipe_path)
    if cancelled():
        raise InterruptedError('ComfyUI generation cancelled.')

    with TemporaryDirectory(prefix='comfy-', dir=job, ignore_cleanup_errors=True) as temporary:
        work = Path(temporary)
        for name in ('input', 'output', 'temp', 'scratch', 'user', 'custom_nodes'):
            (work / name).mkdir()
        reference = work / 'input' / ('reference' + source.suffix.lower())
        shutil.copyfile(source, reference)
        task = {
            'profile_id': profile_id,
            'runtime': runtime,
            'payload': _prepare_payload(profile_id, reference, runtime, recipe),
        }
        task_path = work / 'task.json'
        task_path.write_text(json.dumps(task), encoding='utf-8')
        extra_paths = {'studio': {'custom_nodes': runtime['custom_nodes']}}
        (work / 'extra-paths.yaml').write_text(json.dumps(extra_paths), encoding='utf-8')
        log_path = job / 'comfy-profile.log'
        progress('Starting the isolated native ComfyUI runtime', 5)
        deadline = monotonic() + profile['timeout']
        offset, percent = 0, 5
        launch = [runtime['python'], '-I', '-B', '-u', '-X', 'utf8', str(Path(__file__).resolve()),
                  '--child', str(task_path)]
        with log_path.open('wb') as log, subprocess.Popen(
            launch, cwd=work, env=_environment(profile_id, runtime, work), stdout=log,
            stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        ) as process:
            try:
                ready_deadline = monotonic() + LOAD_TIMEOUT
                while process.poll() is None:
                    if cancelled():
                        raise InterruptedError('ComfyUI generation cancelled.')
                    if monotonic() >= deadline:
                        raise TimeoutError('The pinned ComfyUI workflow timed out.')
                    if monotonic() >= ready_deadline and not (work / 'ready').exists():
                        raise TimeoutError('The native ComfyUI runtime did not initialize.')
                    offset, percent = _read_progress(work / 'progress.jsonl', offset, progress, percent)
                    try:
                        process.wait(timeout=.5)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                _stop_process(process)
        _read_progress(work / 'progress.jsonl', offset, progress, percent)
        if cancelled():
            raise InterruptedError('ComfyUI generation cancelled.')
        result_path = work / 'result.json'
        result = _read_json(result_path) if result_path.is_file() else {}
        if process.returncode != 0 or not result.get('success'):
            reason = result.get('error') or 'The native ComfyUI workflow did not finish. See the job log.'
            raise RuntimeError(reason)
        output = (work / 'output' / result['glb']).resolve()
        if not output.is_relative_to(work / 'output') or not output.is_file():
            raise RuntimeError('The pinned ComfyUI workflow did not produce its expected GLB.')
        _validate_glb(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = work / 'result.glb'
        shutil.copyfile(output, staging)
        if cancelled():
            raise InterruptedError('ComfyUI generation cancelled.')
        os.replace(staging, destination)
        metadata = {
            'profile': profile_id,
            'workflow_sha256': recipe['source_sha256'],
            'recipe_sha256': _file_hash(recipe_path),
            'comfy_version': result['comfy_version'],
            'elapsed_seconds': result['elapsed_seconds'],
            'notes': recipe.get('notes', []),
        }
    return metadata


class _NativeWorkflowError(RuntimeError):
    pass


def _workflow_failure(error):
    node = error.get('node_type', 'unknown')
    detail = (str(error.get('exception_type', '')) + ' ' + str(error.get('exception_message', ''))).lower()
    if 'outofmemory' in detail or 'out of memory' in detail:
        return f'The GPU ran out of memory at {node}. Close other GPU tasks before retrying.'
    if 'failed to find c compiler' in detail or 'failed to find cuda' in detail:
        return f'The CUDA build tools are unavailable at {node}. Check the local ComfyUI compiler paths.'
    if 'localentrynotfound' in detail or 'filenotfound' in detail:
        return f'A local model or dependency is missing at {node}. Check the pinned runtime and weights.'
    return f'The native ComfyUI workflow stopped at {node}. See the job log.'


def _child(task_path):
    work = task_path.parent
    task = _read_json(task_path)
    profile = PROFILES[task['profile_id']]
    runtime = task['runtime']
    payload = task['payload']
    comfy_directory = Path(runtime['comfy'])
    sys.path[:0] = [*runtime['python_paths'], str(comfy_directory)]
    sys.argv = [
        str(comfy_directory / 'main.py'), '--base-directory', str(work),
        '--models-directory', runtime['models'], '--input-directory', str(work / 'input'),
        '--output-directory', str(work / 'output'), '--temp-directory', str(work / 'temp'),
        '--user-directory', str(work / 'user'), '--extra-model-paths-config', str(work / 'extra-paths.yaml'),
        '--disable-auto-launch', '--offline',
    ]
    if runtime['low_vram']:
        sys.argv.extend(['--lowvram', '--reserve-vram', '1.0'])
    import comfyui_version

    if comfyui_version.__version__ != COMFY_VERSION:
        raise _NativeWorkflowError(f'This ComfyUI profile requires native runtime version {COMFY_VERSION}.')
    # Keep the pinned launcher allocator and Windows device selection while omitting its HTTP listener.
    import comfy.options
    comfy.options.enable_args_parsing()
    import cuda_malloc

    if os.name == 'nt':
        os.environ.setdefault('CUDA_VISIBLE_DEVICES', '0')
    main = runpy.run_path(str(comfy_directory / 'main.py'), run_name='studio_comfy_local',
                          init_globals={'cuda_malloc': cuda_malloc})
    manager = main['comfy'].model_management
    if manager.get_torch_device().type != 'cuda':
        raise _NativeWorkflowError('The ComfyUI generation profile requires a CUDA GPU.')
    loop, server, _unused_listener = main['start_comfyui']()
    try:
        def announce(event, data, sid=None):
            if event == 'executing' and (stage := profile['stages'].get(data.get('node'))):
                with (work / 'progress.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(stage) + '\n')

        server.send_sync = announce
        prompt = payload['prompt']
        prompt_id = str(uuid4())
        valid, error, outputs, node_errors = loop.run_until_complete(
            main['execution'].validate_prompt(prompt_id, prompt, None))
        if not valid or node_errors or profile['output_node'] not in outputs:
            print(json.dumps({'error': error, 'node_errors': node_errors}, default=str))
            raise _NativeWorkflowError('The pinned ComfyUI workflow needs its matching local nodes and weights. See the job log.')
        extra = {**payload.get('extra_data', {}), 'client_id': 'trellis-studio-local'}
        start = perf_counter()
        (work / 'ready').touch()
        server.prompt_queue.put((0, prompt_id, prompt, extra, outputs, {}))
        while not (history := server.prompt_queue.get_history(prompt_id=prompt_id).get(prompt_id)):
            sleep(.25)
        elapsed = round(perf_counter() - start, 2)
        if history['status']['status_str'] != 'success':
            errors = [details for event, details in history['status']['messages'] if event == 'execution_error']
            if errors:
                print(json.dumps(errors, default=str))
                raise _NativeWorkflowError(_workflow_failure(errors[-1]))
            raise _NativeWorkflowError('The native ComfyUI workflow stopped. See the job log.')
        output_root = work / 'output'
        files = list(output_root.rglob(profile['output_prefix'] + '*.glb'))
        if len(files) != 1:
            raise _NativeWorkflowError('The native ComfyUI workflow did not produce the pinned final GLB.')
        return {
            'success': True, 'glb': files[0].relative_to(output_root).as_posix(),
            'comfy_version': comfyui_version.__version__, 'elapsed_seconds': elapsed,
        }
    finally:
        server.asset_manager.shutdown()
        loop.close()


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', type=Path, required=True)
    task = parser.parse_args().child
    try:
        result = _child(task)
    except Exception as error:
        public_message = (str(error) if isinstance(error, _NativeWorkflowError)
                          else 'The native ComfyUI runtime failed. See the job log.')
        (task.parent / 'result.json').write_text(
            json.dumps({'success': False, 'error': public_message}), encoding='utf-8')
        raise
    (task.parent / 'result.json').write_text(json.dumps(result), encoding='utf-8')


if __name__ == '__main__':
    _main()
