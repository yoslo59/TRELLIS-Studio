r"""Run the Unity reimport regression in a disposable project.

.venv\Scripts\python.exe tests\unity_reimport.py "C:\path\to\Unity.exe"
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image
import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from target_export import export_asset


def make_package(work, name, identifier, parent, *, width, colour, node_name):
    source = work / "sources" / name / identifier
    source.mkdir(parents=True)
    mesh = trimesh.creation.box(extents=(width, 1, 1))
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=np.zeros((len(mesh.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(
            name="Paint", baseColorTexture=Image.new("RGBA", (4, 4), colour)
        ),
    )
    scene = trimesh.Scene()
    scene.add_geometry(mesh, node_name=node_name, geom_name=node_name + "Mesh")
    (source / "model.glb").write_bytes(scene.export(file_type="glb"))
    (source / "LODs").mkdir()
    (source / "LODs" / "LOD1.glb").write_bytes(scene.export(file_type="glb"))
    (source / "lod-manifest.json").write_text(
        json.dumps(dict(levels=[dict(level=0), dict(level=1, file="LOD1.glb")],
                        screen_heights=[0.6, 0.2])), encoding="utf-8"
    )
    (source / "settings.json").write_text(
        json.dumps(dict(name=name, height_m=1, parent_id=parent)), encoding="utf-8"
    )
    (source / "metadata.json").write_text("{}", encoding="utf-8")
    (source / "physics.json").write_text('{"kind":"box"}', encoding="utf-8")
    destination = work / "packages" / name
    manifest = export_asset(source, destination, "unity")
    assert manifest["unity_asset_id"] == identifier
    assert manifest["unity_parent_id"] == parent
    return destination


def run(editor):
    with tempfile.TemporaryDirectory(prefix="trellis-unity-reimport-") as temporary:
        work = Path(temporary)
        installed = make_package(work, "Initial", "a" * 32, "", width=1, colour="red", node_name="Body")
        make_package(work, "Updated", "b" * 32, "a" * 32, width=2, colour="blue", node_name="Body")
        make_package(work, "Incompatible", "c" * 32, "a" * 32, width=3, colour="green", node_name="Renamed")
        # A 3.1.0 package has the legacy id field and no Unity-specific identity.
        for package in installed.rglob("studio-export.json"):
            manifest = json.loads(package.read_text(encoding="utf-8"))
            for key in ("unity_asset_id", "unity_parent_id"):
                manifest.pop(key)
            package.write_text(json.dumps(manifest), encoding="utf-8")
        project = work / "project"
        editor_dir = project / "Assets" / "Editor"
        editor_dir.mkdir(parents=True)
        shutil.copy2(ROOT / "unity" / "TrellisPackageImporter.cs", editor_dir)
        shutil.copy2(ROOT / "tests" / "unity_reimport_editor.cs", editor_dir)
        shutil.copytree(installed, project / "Assets" / "Installed")
        (project / "Packages").mkdir()
        (project / "Packages" / "manifest.json").write_text('{"dependencies":{}}', encoding="utf-8")
        log = work / "unity.log"
        result = subprocess.run(
            [str(editor), "-batchmode", "-nographics", "-quit", "-projectPath", str(project),
             "-logFile", str(log), "-executeMethod", "TrellisStudio.ReimportRegression.Run"],
            timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        report = log.read_text(encoding="utf-8", errors="replace")
        if result.returncode or "TRELLIS REIMPORT PASS" not in report:
            print(report[-15000:])
            raise SystemExit("Unity reimport regression failed.")
        print("TRELLIS REIMPORT PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unity", type=Path)
    run(parser.parse_args().unity.resolve(strict=True))
