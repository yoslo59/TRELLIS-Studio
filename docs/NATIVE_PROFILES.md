# Standalone generation profiles

Select **High Detail** or **Game Asset** in **Create → Generation profile**. Both profiles organise **Advanced Settings** into Reference, Geometry, Mesh finishing and Texturing. Each stage has its own settings and contextual help. **Reset workflow settings** restores the tested recipe. The batch and retry editors use the same groups.

Create opens with **High Detail** when its local geometry runtime is ready, with **High Detail (Props)** as the fallback. **Use at startup** saves a browser-specific choice.

Select **Open workflow graph** to display connected nodes in the central workspace. Drag the grid to pan, scroll to zoom, and expand a node to edit its parameters directly. The graph and the form share the same values. Escape or **Close workflow graph** returns to the 3D preview. **Figure Master** and **High Detail (Props)** have a shorter graph with the sections used by Studio's standard pipeline. Their nodes edit the same settings as Advanced Settings, and **Save a copy** stores the resulting recipe.

**High Detail** uses TRELLIS.2 4B GGUF Q4_K_M, 1024 cascade, a second shape pass, two QuadDC and MeshLib iterations, and a target of 2,000,000 triangles. **Geometry engine** selects the first mesh generator. With PiXal3D, Studio generates the first mesh at 1536 and uses the same QuadDC, refinement and material stages afterward. Seed values are saved per stage. Model scale and ground placement are applied as a scene transform to the completed GLB. When UltraTex is installed, new runs default to the same 2K multiview pass as Game Asset, applied to the finished High Detail mesh and its existing UVs. Under **Texturing → Texture generation**, **TRELLIS.2 PBR** uses a 4K atlas and 25 texture steps by default. Saved generations retain the method in their recorded recipe.

High Detail runs in the Studio worker with an optional portable GGUF component and local model weights. The component uses the CUDA backend. The settings mark unavailable backends and inputs ignored by the upstream recipe. **High Detail (Props)** uses the earlier Studio recipe for objects.

**Game Asset** takes an image through PiXal3D or TRELLIS.2 at 1536, QuadDC, WTiVo, LOD Tailor, PEC UV unwrapping and a 2K normal bake. UltraTex generates twelve colour views and six metal/roughness views by default. The target is 12,000 triangles. Advanced controls include reference framing, shape sampling, reconstruction, UVs, normal baking, and UltraTex settings for prompts, views, atlas and memory. Component names and options specific to the original graph host show the values required by the installed native runtime.

The profile picker shows the availability of each verified local component. Game Asset uses an isolated Python environment, optional native geometry and UltraTex packages, local weights and the Blender installation already detected by Studio. Existing standard generation methods have their own component requirements.

For completed native models, **Prepare → Texture Projection** uses the model's scene placement and its existing UVs. **Finishing board** can create an UltraTex 2K texture copy from the saved reference. **Game Copy from HD** can bake a separate lower-detail model. **HD Master Finishing** appears for assets with a reusable generation cache.

## Administrator-provisioned runtimes

The Windows installer includes the standalone adapters and the standard Studio components. Its **UltraTex · 2K preview** option is for the existing optional six-view adapter. The native workflows use a separate standalone runtime. Studio reports the availability of each engine and texture method in the profile picker. These runtimes use local source, weights and verification records.

### High Detail geometry

Run `python native_high_detail_portable.py PIPELINE_SOURCE GGUF_OPERATORS_SOURCE INSTALLATION` with local copies of the pinned source trees. The command verifies the source files it adapts. The required revisions are declared in the script. It produces a standalone Python package and preserves the upstream license files.

Create `config/native-high-detail.json` with `schema` set to `1` and define `package` for the generated package, `models` for the weights root and `u2net` for the directory containing `u2net.onnx`. The weights root contains `Trellis2` and the local DINOv3 files. Optional `python_paths` points to local dependency directories where needed. All paths can be relative to the configuration directory. Stock Windows Setup leaves portable components and weights to the local administrator.

### Game Asset and standalone UltraTex

- Geometry uses `config/native-pixal3d.json` for the isolated Python interpreter, portable shape architecture, BiRefNet source, MoGe source, dependencies and the weights root. The standalone UltraTex foreground step uses the configured Python, local BiRefNet source and its checkpoint.
- Game Asset finishing uses `runtime/native-game-geometry/runtime.json` and its adjacent `character-05-recipe.json`. This runtime points to the verified QuadDC and CuMesh dependencies, UV operators, and the mesh finishing toolkit. It also requires WTiVo and a Blender installation detected by Studio.
- Standalone UltraTex uses the isolated Python under `.tools/wtivo/runtime`, with pinned operator sources in `runtime/native-game-texture` and the six checkpoints under `models/native-game-texture`. The worker checks the sources and checkpoints against the runtime's source and model manifests before inference.
- Game Asset readiness also depends on local engine and workflow verification. Install compatible components and validate the complete workflow on the intended hardware before marking a configuration ready. The native adapters do not launch a ComfyUI server.
