"""Bake a tangent-space normal map for a finished variant of a decoded master."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import numpy as np

from glb_data import accessor, read
from normal_glb import embed_normal_map
from studio_import import find_blender


def bake_master_normals(destination, source, scale, translation, texture_size):
    blender = find_blender()
    if blender is None:
        raise RuntimeError("Install Blender to bake detail from the master, or retry without the normal map.")

    document, binary = read(destination)
    primitive, = document["meshes"][0]["primitives"]
    attributes = {key: accessor(document, binary, index)
                  for key, index in primitive["attributes"].items()}
    corners = accessor(document, binary, primitive["indices"]).reshape(-1, 3)
    voxel_m = float(np.min(source["voxel_size"])) * scale
    if not np.isfinite(voxel_m) or voxel_m <= 0:
        raise ValueError("The saved master has an invalid voxel size for normal baking.")
    script = Path(__file__).with_name("normal_bake_blender.py")
    with TemporaryDirectory(prefix=".normal-bake-", dir=destination.parent) as temporary:
        directory = Path(temporary)
        np.save(directory / "master-positions.npy", source["vertices"].detach().cpu().numpy(), allow_pickle=False)
        np.save(directory / "master-faces.npy", source["faces"].detach().cpu().numpy(), allow_pickle=False)
        np.savez(directory / "bake-input.npz", positions=attributes["POSITION"],
                 normals=attributes["NORMAL"], uv=attributes["TEXCOORD_0"], faces=corners,
                 scale=scale, translation=translation, voxel_m=voxel_m,
                 master_y_up=source.get("coordinate_system") == "y_up")
        command = [str(blender), "--background", "--factory-startup", "--disable-autoexec",
                   "--python-exit-code", "1", "--python", str(script), "--", str(directory), str(texture_size)]
        try:
            subprocess.run(command, check=True, timeout=600, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Blender could not bake the normal map. See the job log for details.") from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Normal baking timed out. Retry with a smaller texture size or triangle target.") from error
        report = embed_normal_map(destination, document, binary, primitive, attributes, corners,
                                  np.load(directory / "tangents.npy", allow_pickle=False),
                                  directory / "unfiltered.png")
    return dict(report, state="applied", source_triangles=len(source["faces"]))
