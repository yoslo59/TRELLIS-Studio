"""Local model import; original files are retained beside a portable GLB."""
from pathlib import Path
import json, os, re, shutil, struct

FORMATS = ('.glb', '.fbx', '.obj', '.stl', '.ply')
MAX_BYTES = 500 * 1024 * 1024

def animation_recipe(name):
    if not isinstance(name, str):
        return None
    match = re.fullmatch(r'TS_([a-z0-9_]{1,80})_(\d+(?:\.\d+)?)s', name)
    if match:
        duration = float(match[2])
        if 2 <= duration <= 5:
            return dict(id=match[1], duration=duration)
    return None


def animation_recipes(data):
    return [recipe for clip in data.get('animations', []) if (recipe := animation_recipe(clip.get('name')))]

def find_blender():
    configured = os.environ.get('TRELLIS_BLENDER')
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    runtime = Path(__file__).resolve().parent / 'config' / 'runtime.json'
    try:
        settings = json.loads(runtime.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        settings = {}
    configured = settings.get('blender_path') if isinstance(settings, dict) else None
    if isinstance(configured, str) and configured:
        path = Path(configured)
        return path if path.is_absolute() and path.is_file() else None
    on_path = shutil.which('blender.exe')
    if on_path:
        return Path(on_path)
    program_files = os.environ.get('ProgramW6432') or os.environ.get('PROGRAMFILES', 'C:/Program Files')
    candidates = sorted(Path(program_files).glob('Blender Foundation/Blender */blender.exe'), reverse=True)
    if candidates:
        return candidates[0]
    bundled = runtime.parent.parent / '.tools/blender-5.2.2/blender.exe'
    return bundled if bundled.is_file() else None

def validate_glb(path):
    """Reject incomplete containers and external references before opening them."""
    size = path.stat().st_size
    with path.open('rb') as stream:
        header = stream.read(20)
        if len(header) != 20: raise ValueError('Incomplete GLB file.')
        magic, version, length, json_size, kind = struct.unpack('<4sIIII', header)
        if magic != b'glTF' or version != 2 or length != size or kind != 0x4e4f534a or json_size > min(size-20, 32*1024*1024):
            raise ValueError('Invalid GLB 2.0 container.')
        try: data = json.loads(stream.read(json_size))
        except (ValueError, UnicodeError): raise ValueError('Unreadable GLB description.')
    if not isinstance(data, dict) or not data.get('meshes'): raise ValueError('The GLB contains no mesh.')
    for item in data.get('buffers', []) + data.get('images', []):
        uri = item.get('uri', '')
        if uri and not uri.startswith('data:'):
            raise ValueError('The GLB references an external file. Export a GLB with embedded resources.')
    return data

def copy_version(source, destination):
    """A new version of an imported model preserves its source, rig and clips."""
    for filename in ('model.glb', 'model.blend', 'model.stl', 'print-report.json', 'input.png', 'result.json', 'physics.json', 'lod-manifest.json', 'motion-report.json', 'presentation.json'):
        if (source/filename).is_file(): shutil.copy2(source/filename, destination/filename)
    for dirname in ('LODs', 'Collisions', 'Presentation'):
        if (source/dirname).is_dir(): shutil.copytree(source/dirname, destination/dirname)
    for path in source.glob('original.*'):
        if path.suffix in FORMATS: shutil.copy2(path, destination/path.name)


def rewrite_animation_names(source, destination, names):
    """Change clip names only; preserve all binary chunks byte for byte."""
    data = validate_glb(source)
    for clip in data.get('animations', []):
        if clip.get('name') in names:
            clip['name'] = names[clip['name']]
    descriptor = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    descriptor += b' ' * (-len(descriptor) % 4)
    raw = source.read_bytes()
    old_length = struct.unpack_from('<I', raw, 12)[0]
    tail = raw[20 + old_length:]
    destination.write_bytes(struct.pack('<4sIIII', b'glTF', 2, 20 + len(descriptor) + len(tail),
                                      len(descriptor), 0x4e4f534a) + descriptor + tail)


def blender_animation_source(source, destination):
    """Use collision-free short names for imported clips Blender would truncate.

    Blender reserves bytes in action names. Keep original names outside Blender
    and restore them on the exported GLB, including older generated motions.
    """
    data = validate_glb(source)
    occupied = {clip.get('name') for clip in data.get('animations', [])}
    aliases = {}
    for index, clip in enumerate(data.get('animations', [])):
        name = clip.get('name')
        if not isinstance(name, str) or len(name.encode('utf-8')) <= 58:
            continue
        if animation_recipe(name):
            continue  # Authored recipe names must remain recognizable during replacement.
        alias = '__TS_preserved_' + str(index)
        while alias in occupied:
            alias += '_'
        occupied.add(alias)
        aliases[alias] = name
    rewrite_animation_names(source, destination, {name: alias for alias, name in aliases.items()})
    return aliases
