# Historical ComfyUI workflow pilot

This page records the pre-release pilot. For the current Studio generation profiles and installation requirements, see [Standalone generation profiles](NATIVE_PROFILES.md).

Select a ComfyUI profile in **Create → Generation profile** and upload one still image. You can enter the output height in metres and set the model name and asset type. The graph sets the sampling, seeds, mesh processing and texture settings. The generated GLB enters the regular Studio collection.

| Profile | Geometry | Textures |
| --- | --- | --- |
| **Game Asset · ComfyUI** | PixelArtistry Character 05, Pixal3D 1536, QuadDC and WTiVo, with a 12,000 triangle target | Baked normals and UltraTex 2K with 12 colour views and six metallic/roughness views |
| **High Detail · ComfyUI** | TRELLIS.2 High Quality GGUF Q4_K_M, 1024 cascade and a second geometry pass, with QuadDC and MeshLib | TRELLIS.2 4K, 25 material steps |

The triangle counts are targets. Review faces and topology before rigging. The Character 05 graph uses FLUX.2 Klein **base** 9B. Its weights are subject to the [FLUX Non-Commercial License](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9B/blob/main/LICENSE.md).

## Local runtime

The profile picker reports which workflows are available. These profiles use an optional local ComfyUI 0.39.2 runtime with the matching Python environment, CUDA, nodes and weights. For Character 05, use the [published runtime and node requirements](https://github.com/pixelartistry/PixelArtistry-Animated-Characters/blob/77f1462dab427cb95a7b1585a65c8f286a2c3898/docs/COMPATIBILITY.md). High Detail uses the High Quality GGUF recipe with the xFormers backend validated in Studio's pilot.

Configure `config/comfy-runtime.json` with `schema` set to `1` and a `profiles` object keyed by `game_asset_comfy` and `high_detail_comfy`. Each configured profile expects paths to `python`, `comfy` (the directory containing `main.py`), `models` and `custom_nodes`. You can set `python_paths` for isolated dependencies and `environment` for local compiler or cache paths. The game profile also needs a `blender` executable path. Paths can be absolute or relative to `config`. Omit profiles whose runtimes are unavailable.

Studio starts the local runtime inside the generation job and closes it after completion. The workflow image is preserved in its uploaded format for retries. Studio sets the scale and ground placement with a scene transform, preserving the graph's geometry, UVs and textures.
