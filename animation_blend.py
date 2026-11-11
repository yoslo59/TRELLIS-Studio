"""Save a Blender companion for an edited GLB without re-exporting the GLB."""
from pathlib import Path
import sys

import bpy

stage = Path(sys.argv[sys.argv.index('--') + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(stage / 'model.glb'))
bpy.ops.file.pack_all()
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=str(stage / 'model.blend'))
