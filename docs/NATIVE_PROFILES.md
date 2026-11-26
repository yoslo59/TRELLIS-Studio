# Standalone generation profiles

Select **High Detail** or **Game Asset** in **Create → Generation profile**. Both profiles organise **Advanced Settings** into Reference, Geometry, Mesh finishing and Texturing. Each stage has its own settings and contextual help. **Reset workflow settings** restores the tested recipe. The batch and retry editors use the same groups.

Create opens with **High Detail** when its local geometry runtime is ready, with **High Detail (Props)** as the fallback. **Use at startup** saves a browser-specific choice.

Select **Open workflow graph** to display connected nodes in the central workspace. Drag the grid to pan, scroll to zoom, and expand a node to edit its parameters directly. The graph and the form share the same values. Escape or **Close workflow graph** returns to the 3D preview. **Figure Master** and **High Detail (Props)** have a shorter graph with the sections used by Studio's standard pipeline. Their nodes edit the same settings as Advanced Settings, and **Save a copy** stores the resulting recipe.

During generation, open **View generation graph** on the selected job or open its graph from the queue. The graph shows the job’s saved settings, the stage reported by the worker and overall progress. Phase buttons and **Follow stage** help navigate long workflows.

Use **Save a copy** to name your own **Game Asset** or **High Detail** recipe. It stores the geometry engine and workflow settings. The reference image, model name, height and seeds belong to each generation. Select the saved recipe to restore its settings, or choose **Edit this profile** to update it. Existing jobs retain the recipe they used.

**High Detail** uses TRELLIS.2 4B GGUF Q4_K_M, 1024 cascade, a second shape pass, two QuadDC and MeshLib iterations, and a target of 2,000,000 triangles. **Geometry engine** selects the first mesh generator. With PiXal3D, Studio generates the first mesh at 1536 and uses the same QuadDC, refinement and material stages afterward. Seed values are saved per stage. Model scale and ground placement are applied as a scene transform to the completed GLB. When UltraTex is installed, new runs default to the same 2K multiview pass as Game Asset, applied to the finished High Detail mesh and its existing UVs. Under **Texturing → Texture generation**, **TRELLIS.2 PBR** uses a 4K atlas and 25 texture steps by default. Saved generations retain the method in their recorded recipe.

With **UltraTex**, High Detail prepares its foreground mask on the CPU while the geometry pipeline runs. For opaque meshes that need UVs, Studio unwraps the finished surface and sends its UVs and normals directly to UltraTex. UltraTex generates the final colour, roughness and metallic maps at the selected atlas size.

High Detail runs in the Studio worker with an optional portable GGUF component and local model weights. The component uses the CUDA backend. The settings mark unavailable backends and inputs ignored by the upstream recipe. **High Detail (Props)** uses the earlier Studio recipe for objects.

**Game Asset** takes an image through PiXal3D or TRELLIS.2 at 1536, QuadDC, WTiVo, LOD Tailor, PEC UV unwrapping and a 2K normal bake. UltraTex generates twelve colour views and six metal/roughness views by default. The target is 12,000 triangles. Advanced controls include reference framing, shape sampling, reconstruction, UVs, normal baking, and UltraTex settings for prompts, views, atlas and memory. Component names and options specific to the original graph host show the values required by the installed native runtime.

During WTiVo, Studio shows the current operation, elapsed time and the 40-minute limit for that step. The full generation also includes shape generation, reduction, baking and texturing.

The profile picker shows the availability of each verified local component. Game Asset uses an isolated Python environment, optional native geometry and UltraTex packages, local weights and the Blender installation already detected by Studio. Existing standard generation methods have their own component requirements.

For completed native models, **Prepare → Texture Projection** uses the model's scene placement and its existing UVs. **Finishing board** can create an UltraTex 2K texture copy from the saved reference. **Game Copy from HD** can bake a separate lower-detail model. **HD Master Finishing** appears for assets with a reusable generation cache.

## Windows Setup

Select **TRELLIS.2 image-to-3D**, then **High Detail and Game Asset**. After you accept the FLUX.2 Klein 9B and MeshLib terms, Setup prepares PiXal3D and TRELLIS.2 geometry, mesh finishing and the native UltraTex runtime. The Studio worker runs these workflows locally. The **UltraTex · 2K preview** entry in Setup is a separate six-view compatibility component. Use **Repair or resume components** to finish an interrupted native installation. See the [Windows Setup guide](WINDOWS_SETUP.md#standalone-generation-profiles).

## Runtime configuration

Studio reports the availability of each locally verified engine and texture method in the profile picker. The following files describe the native runtimes for diagnostics or manual installation.

### High Detail geometry

Run `python native_high_detail_portable.py PIPELINE_SOURCE GGUF_OPERATORS_SOURCE INSTALLATION` with local copies of the pinned source trees. The command verifies the source files it adapts. The required revisions are declared in the script. It produces a standalone Python package and preserves the upstream license files.

The `config/native-high-detail.json` file uses schema `1` and defines `package` for the generated package, `models` for the weights root and `u2net` for the directory containing `u2net.onnx`. The weights root contains `Trellis2` and the local DINOv3 files. Optional `python_paths` points to local dependency directories where needed. All paths can be relative to the configuration directory.

### Game Asset and standalone UltraTex

- Geometry uses `config/native-pixal3d.json` for the isolated Python interpreter, portable shape architecture, BiRefNet source, MoGe source, dependencies and the weights root. The standalone UltraTex foreground step uses the configured Python, local BiRefNet source and its checkpoint.
- Game Asset finishing uses `runtime/native-game-geometry/runtime.json` and its adjacent `character-05-recipe.json`. This runtime points to the verified QuadDC and CuMesh dependencies, UV operators, and the mesh finishing toolkit. It also requires WTiVo and a Blender installation detected by Studio.
- Standalone UltraTex uses the isolated Python under `.tools/wtivo/runtime` and the pinned operators in `runtime/native-game-texture`. It reads compatible Game Asset Python packages from their installed paths. Setup adds any UltraTex packages that are missing from those paths. The worker checks its sources and checkpoints before inference.
- The native and compatibility components can use the same PiXal3D or UltraTex checkpoint when their manifests pin the same path, size and SHA-256. Both readers use the installed weight directly. New shared downloads use `models/native-pixal3d` or `models/native-game-texture`, and existing compatible weights can stay at their original locations.
- Game Asset readiness also depends on local engine and workflow verification. Install compatible components and validate the complete workflow on the intended hardware before marking a configuration ready. The native adapters do not launch a ComfyUI server.
