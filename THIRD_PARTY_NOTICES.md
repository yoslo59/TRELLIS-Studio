# Third-party notices and credits

The original TRELLIS Studio code is MIT, Copyright 2026 yoslo59. Third-party components retain their own copyrights, notices and license terms. This file credits the tools used; it does not relicense them or grant blanket rights to every checkpoint or input image.

## Code and tools

| Project / repository | License | Role / attribution |
| --- | --- | --- |
| [Microsoft TRELLIS.2](https://github.com/microsoft/TRELLIS.2) | MIT | Generation engine; Microsoft Research. |
| [TRELLIS.2 Windows port](https://github.com/IgorAherne/TRELLIS.2-stableprojectorz) | MIT | Windows wheels, low-VRAM compatibility; Igor Aherne, building on Microsoft and community contributors. |
| [Validated Windows fork](https://github.com/andrakis/TRELLIS.2-stableprojectorz) | MIT | Pinned engine revision 1481f4995870460b6442be50b6265307b6d6d07f; includes compatibility and background-removal controls. |
| [UniRig](https://github.com/VAST-AI-Research/UniRig) | MIT | VAST AI Research / Tripo and Tsinghua University; skeleton and skin prediction. |
| [DINOv3](https://github.com/facebookresearch/dinov3) | Custom DINOv3 License | Meta image conditioning code/weights. Read the full separate terms; not MIT and not an unrestricted-use promise. |
| [Blender](https://github.com/blender/blender) | GNU GPL; see upstream license | Independent installed application and bpy runtime. Studio drives separate background processes; Blender is not shipped in this Git repository. |
| [PyTorch](https://github.com/pytorch/pytorch) | BSD-3-Clause and third-party notices | Native and WSL tensor/inference runtimes; binary distributions also contain separately licensed dependencies. |
| [TorchVision](https://github.com/pytorch/vision) | BSD-3-Clause | Image preprocessing. |
| [Transformers](https://github.com/huggingface/transformers) | Apache-2.0 | Transformer inference and DINOv3 integration. |
| [Accelerate](https://github.com/huggingface/accelerate) | Apache-2.0 | Model loading/runtime helpers. |
| [Hugging Face Hub](https://github.com/huggingface/huggingface_hub) | Apache-2.0 | Model metadata and asset ecosystem; checkpoint licenses remain separate. |
| [safetensors](https://github.com/huggingface/safetensors) | Apache-2.0 | Tensor serialization. |
| [CuMesh](https://github.com/JeffreyXiang/CuMesh) | MIT | CUDA mesh operations and simplification; Jeffrey Xiang. |
| [FlexGEMM](https://github.com/JeffreyXiang/FlexGEMM) | MIT | Sparse convolution backend; Jeffrey Xiang. |
| [O-Voxel](https://github.com/microsoft/TRELLIS.2/tree/main/o-voxel) | MIT under TRELLIS.2 | Mesh/attribute extraction. Studio retains a modified MIT postprocess layer and its own PyTorch UV rasteriser. |
| [utils3d](https://github.com/EasternJournalist/utils3d) | MIT | 3D math/geometry helpers; pinned revision 9a4eb15e4021b67b12c460c7057d642626897ec8. |
| [xformers](https://github.com/facebookresearch/xformers) | BSD-3-Clause | Memory-efficient sparse attention. |
| [Triton](https://github.com/triton-lang/triton) | MIT | GPU kernels. |
| [triton-windows](https://github.com/woct0rdho/triton-windows) | MIT | Windows Triton distribution. |
| [FlashAttention](https://github.com/Dao-AILab/flash-attention) | BSD-3-Clause | UniRig attention implementation; pinned prebuilt Linux wheel. |
| [spconv / cumm](https://github.com/traveller59/spconv) | Apache-2.0 | UniRig sparse convolution; cumm: https://github.com/FindDefinition/cumm. |
| [torch-scatter](https://github.com/rusty1s/pytorch_scatter) | MIT | UniRig tensor reductions; PyG binaries. |
| [torch-cluster](https://github.com/rusty1s/pytorch_cluster) | MIT | UniRig point/cluster operations; PyG binaries. |
| [PyTorch Lightning](https://github.com/Lightning-AI/pytorch-lightning) | Apache-2.0 | UniRig inference framework. |
| [Open3D](https://github.com/isl-org/Open3D) | MIT | UniRig geometry processing. |
| [Trimesh](https://github.com/mikedh/trimesh) | MIT | Mesh diagnostics, GLB and geometry processing. |
| [NumPy](https://github.com/numpy/numpy) | BSD-3-Clause | Numeric arrays. |
| [SciPy](https://github.com/scipy/scipy) | BSD-3-Clause | Numeric/geometry routines. |
| [Pillow](https://github.com/python-pillow/Pillow) | HPND / Pillow license | Image input/output. |
| [OpenCV](https://github.com/opencv/opencv) | Apache-2.0 | Image processing in current pinned releases. |
| [rembg](https://github.com/danielgatis/rembg) | MIT | CPU background removal with U2-Net. |
| [U2-Net](https://github.com/xuebinqin/U-2-Net) | Apache-2.0 | Background-removal network/checkpoint source; Xuebin Qin and collaborators. |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | MIT | CPU inference for U2-Net. |
| [Three.js](https://github.com/mrdoob/three.js) | MIT | Browser PBR renderer, loaders and export/viewer utilities. |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | Local API and schema generation. |
| [Starlette](https://github.com/Kludex/starlette) | BSD-3-Clause | ASGI requests, routing and responses. |
| [Uvicorn](https://github.com/Kludex/uvicorn) | BSD-3-Clause | Loopback HTTP server. |
| [Pydantic](https://github.com/pydantic/pydantic) | MIT | Typed contracts and validation. |
| [HTTPX](https://github.com/encode/httpx) | BSD-3-Clause | Local MCP-to-REST transport. |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MIT | STDIO and Streamable HTTP MCP protocol. |
| [Swagger UI](https://github.com/swagger-api/swagger-ui) | Apache-2.0 | Self-hosted interactive API reference. |
| [uv](https://github.com/astral-sh/uv) | MIT OR Apache-2.0 | Private Python/runtime provisioning and package installation. |
| [CPython](https://github.com/python/cpython) | Python Software Foundation License | Interpreter; managed standalone distributions carry their own included notices. |
| [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | See bundled distribution notices | Managed Python builds selected by uv. |
| [Node.js](https://github.com/nodejs/node) | MIT and bundled third-party notices | npm-based installation of browser dependencies. |
| [Eigen](https://gitlab.com/libeigen/eigen) | MPL-2.0 | Upstream native mesh-library dependency; licenses remain with upstream source/binaries. |
| [xatlas](https://github.com/jpcy/xatlas) | MIT | Upstream UV unwrapping dependency. |

## Models and animation assets

| Asset | Source and authors | License / installation |
| --- | --- | --- |
| TRELLIS.2-4B and shared decoder | [Microsoft model card](https://huggingface.co/microsoft/TRELLIS.2-4B), [shared assets](https://huggingface.co/microsoft/TRELLIS-image-large) | MIT according to upstream release; downloaded separately at pinned revisions. |
| UniRig skeleton and skin | [VAST-AI/UniRig](https://huggingface.co/VAST-AI/UniRig) | Model card declares MIT. The installer selects the Articulation-XL skeleton and skin checkpoints at revision 36842e2b5947e9e60f89275b83208c8e74071c63. |
| DINOv3 ViT-L/16 | [Meta](https://github.com/facebookresearch/dinov3), [official model](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m), redistribution provided by the Windows-port author | [DINOv3 License](licenses/DINOv3-LICENSE.md). The installer presents these separate terms before downloading the pinned redistribution, which includes the license. Usage conditions apply. |
| U2-Net | [U2-Net](https://github.com/xuebinqin/U-2-Net), distributed through [rembg releases](https://github.com/danielgatis/rembg/releases/tag/v0.0.0) | Apache-2.0 upstream license; downloaded separately. |
| Universal Animation Library 1 Standard | [Quaternius author page](https://quaternius.com/packs/universalanimationlibrary.html), [free source](https://opengameart.org/content/universal-animation-library) | CC0-1.0. Quaternius; free Standard edition only. |
| Universal Animation Library 2 Standard | [Quaternius author page](https://quaternius.com/packs/universalanimationlibrary2.html), [free source](https://opengameart.org/content/universal-animation-library-2) | CC0-1.0. Quaternius; free Standard edition only. |
| KayKit Character Animations 1.1 | [Kay Lousberg's Free 1.1 release](https://kaylousberg.itch.io/kaykit-character-animations/devlog/1139588/character-animations-update-11) | [CC0-1.0](licenses/KayKit-Character-Animations-CC0.txt). The bundled GLB keeps the original Rig_Medium mannequin, skin and four clips from the Tools and Simulation sets. |

Quaternius distributes these asset packs through the author website/OpenGameArt rather than a corresponding official Git repository; the links above are the actual sources. The screenshots use an isolated demonstration collection and the CC0 animation mannequin. The Tira pilot and all personal models are excluded.

## Distribution boundaries

Studio builds UniRig's OPT-shaped transformer configuration locally using the
Apache-2.0 Transformers implementation. UniRig's own checkpoint supplies its
learned parameters; Studio does not download or redistribute pretrained OPT
language-model weights or its tokenizer.

The repository includes Studio runtime source/adapters and local notices, not engine archives, model weights, Python environments, native wheels or personal model libraries. The installer downloads pinned upstream source archives and retains their original license files. The original Microsoft/Windows-port MIT notices accompany the modified O-Voxel files. The three mesh wheels are fetched with that archive; their included upstream/transitive notices remain applicable.

Native NVIDIA drivers, CUDA runtime components and other vendor binaries retain their vendor terms. They are not relicensed as open source by this repository. Blender/GPL and DINOv3/custom terms are independent from the MIT license on Studio. Commercial-use suitability must be assessed against the relevant component/model and the rights to each input.

The native background-removal selection is rembg/U2-Net. Studio uses its own pure-PyTorch UV baking implementation; the installer does not install the upstream optional research-render packages or RMBG-2.0 weights.

## Full package metadata

[docs/DEPENDENCIES.json](docs/DEPENDENCIES.json) records pinned Python package names, versions, upstream project URLs and published license metadata for the Windows, MCP and UniRig environments. Some binary-wheel versions come from PyTorch/PyG rather than PyPI; consult the upstream distributions for their notices. Package metadata is an inventory, not a replacement for complete license texts.

Additional runtime packages, such as image/HTTP/serialization utilities and transitive dependencies, retain the notices supplied by their authors. Web dependency licenses are also preserved in the npm packages installed through package-lock.json.

## Optional Pixal3D runtime

**Install Pixal3D.cmd** downloads pinned [ComfyUI](https://github.com/Comfy-Org/ComfyUI) source, preserving its GPL-3.0 license, and model packages from [Comfy-Org/Pixal3D](https://huggingface.co/Comfy-Org/Pixal3D) and [Comfy-Org/TRELLIS.2](https://huggingface.co/Comfy-Org/TRELLIS.2). [Pixal3D](https://github.com/TencentARC/Pixal3D) credits Tencent ARC and its collaborators and provides its own license and notices. The vision weights use DINOv3, with the separate terms linked above. The optional source, model packages and environment are downloaded locally. Pins and hashes are in `installer/pixal3d-manifest.json`.

The optional face pass uses the [OpenCV Zoo YuNet face detector](https://github.com/opencv/opencv_zoo/tree/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet), released under the [MIT license](https://github.com/opencv/opencv_zoo/blob/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet/LICENSE). The detector is downloaded separately and runs locally.

## Local license copies

The `licenses/` directory preserves the TRELLIS, UniRig, Three.js, U2-Net, Quaternius and DINOv3 notices used by this installation. The exact downloaded files, revisions and SHA-256 hashes are recorded in `installer/manifest.json`.
