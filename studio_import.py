"""Local model import; original files are retained beside a portable GLB."""
from pathlib import Path
import json, os, re, shutil, struct

FORMATS = ('.glb', '.fbx', '.obj', '.stl', '.ply')
MAX_BYTES = 500 * 1024 * 1024

def animation_recipes(data):
    clips=[]
    for clip in data.get('animations',[]):
        match=re.fullmatch(r'TS_(.+)_([0-9.]+)s',clip.get('name',''))
        if match and re.fullmatch(r'[a-z0-9_]{1,80}',match[1]):
            try:duration=float(match[2])
            except ValueError:continue
            if 2<=duration<=5:clips.append(dict(id=match[1],duration=duration))
    return clips

def find_blender():
    configured = os.environ.get('TRELLIS_BLENDER')
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    candidates = sorted(Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')).glob('Blender Foundation/Blender */blender.exe'), reverse=True)
    return candidates[0] if candidates else None

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
