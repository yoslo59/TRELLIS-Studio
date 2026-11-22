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
| [PyMeshLab](https://github.com/cnr-isti-vclab/PyMeshLab) / [MeshLab](https://github.com/cnr-isti-vclab/meshlab) | [GPL-3.0](https://github.com/cnr-isti-vclab/PyMeshLab/blob/main/LICENSE); retain bundled third-party notices | CPU QEM reduction for baked game copies; Alessandro Muntoni, Paolo Cignoni and the Visual Computing Lab at ISTI-CNR. Windows setup downloads pinned PyMeshLab 2025.7.post1 from PyPI into the private Studio environment. No PyMeshLab wheel or native binaries are embedded in the Setup payload. UV/PBR transfer uses the separately installed Blender application. |
| [AssetFurnace Pixel Match](https://github.com/AahishAbbani/assetfurnace/tree/66fd0d9d14ce004770322949a77493b33b136d08) | [Apache-2.0](licenses/AssetFurnace-Apache-2.0.txt) | Image-to-mesh texture projection and registration by the AssetFurnace contributors. Studio bundles the pinned CPU implementation and adapts its imports to the bundled package namespace. |
| [WTiVo](https://github.com/Mstafa-awad/WTiVo-WatertightVoxel-ComfyuiNode/tree/fb9e9ea6a7deec965d3a4bf43ba7a4a9ff3856f3) | [GPL-3.0-or-later and upstream component terms](https://github.com/Mstafa-awad/WTiVo-WatertightVoxel-ComfyuiNode/blob/fb9e9ea6a7deec965d3a4bf43ba7a4a9ff3856f3/LICENSING.md) | Optional externally installed remeshing runtime by Mstafa-awad, with CelloCut and FaithC attribution. Studio exchanges geometry arrays with the external process. Setup ships the Studio bridge. |
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
| [Caddy](https://github.com/caddyserver/caddy) | Apache-2.0 and upstream dependency notices | Optional Internet HTTPS proxy; pinned Windows binary verified against the installer manifest. The installer includes its upstream LICENSE and README in `bin/caddy`. |
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

## UltraTex preview

The optional Studio adapter runs an isolated worker with [UltraTex Modular](https://github.com/Mstafa-awad/UltraTex-ComfyUI-nodes) and [ComfyUI](https://github.com/Comfy-Org/ComfyUI), both under GPL-3.0, and [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) under Apache-2.0. Upstream code keeps its original terms and notices. The worker exchanges files with Studio and runs locally. The model terms are independent of the code licenses.

The standalone High Detail worker adapts tensor loading from [City96's GGUF operators at revision 6ea2651](https://github.com/city96/ComfyUI-GGUF/tree/6ea2651e7df66d7585f6ffee804b20e92fb38b8a) under [Apache-2.0](native_workflows/CITY96-APACHE-2.0.txt). The portable component preserves the upstream license files for both the GGUF operators and the TRELLIS.2 source. Model weights keep their respective terms.

The recipe uses a quantized FLUX.2 Klein base 9B checkpoint, the FLUX.2 VAE, Qwen3-8B and the [UltraTex adapters and decoder](https://modelscope.cn/models/Yibo-Zhang/UltraTex). The [FLUX Non-Commercial License](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9B/blob/main/LICENSE.md) restricts use to the non-commercial, non-production purposes it defines. The Setup executable contains the Studio integration. Checkpoints and upstream runtimes are installed separately.

Figure Master's optional alternate reconstruction uses [visualbruno's QuadDC integration for CuMesh](https://github.com/visualbruno/CuMesh/tree/d10e54c30ddd03d11472c1431693f985501c7966) under MIT, and separately licensed [MeshLib](https://github.com/MeshInspector/MeshLib/blob/v3.2.0.29/LICENSE). The public package uses the native CuMesh path unless an authorized, compatible alternate runtime is provided locally.

The optional `--single-image` setup adds the single-image Pixal3D checkpoint and
the MIT-licensed [MoGe-2 ViT-L weights](https://huggingface.co/Ruicheng/moge-2-vitl)
for camera estimation. Studio uses the MoGe implementation in the pinned ComfyUI source.

The optional face pass uses the [OpenCV Zoo YuNet face detector](https://github.com/opencv/opencv_zoo/tree/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet), released under the [MIT license](https://github.com/opencv/opencv_zoo/blob/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet/LICENSE). The detector is downloaded separately and runs locally.

## Optional generated-motion runtime

Studio can invoke an administrator-installed [kimodo.cpp](https://github.com/localai-org/kimodo.cpp/tree/75ed52d2d3ec767a9bac79d11060b65d99f61a89) CPU runtime (Apache-2.0), built with [GGML](https://github.com/ggml-org/ggml) (MIT). The configured motion model is [LocalAI's GGUF conversion of NVIDIA SOMA-RP v1.1](https://huggingface.co/LocalAI-io/Kimodo-SOMA-RP-v1.1-GGML), subject to the [NVIDIA Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/). This is distinct from the official v1.2 model.

The separate [Llama-3-Kimodo-GGML encoder](https://huggingface.co/LocalAI-io/Llama-3-Kimodo-GGML) derives from Meta Llama 3 and MIT-licensed McGill LLM2Vec adapters. Its distribution retains the Meta Llama 3 Community License, Acceptable Use Policy and NOTICE: **Built with Meta Llama 3**. These terms are independent from Studio's MIT licence and the motion-model licence. Windows Setup can download and configure this optional runtime after the user selects Kimodo and accepts the model terms; **Install Kimodo.cmd** offers the same opt-in installation later. The weights are not bundled in the repository or Setup executable. Read [the installation guide](docs/MOTION_GENERATION.md) and retain the upstream notices before use or redistribution.

The CPU installer downloads pinned [Zig](https://ziglang.org/) (MIT), [CMake](https://cmake.org/) (BSD-3-Clause) and [Ninja](https://ninja-build.org/) (Apache-2.0) build tools, with their upstream notices. Source, tool and model pins, sizes and SHA-256 hashes are recorded in `installer/kimodo-manifest.json`. These build tools are local installation dependencies and are not added to the system PATH.

Generated-motion previews reuse the separately credited Quaternius CC0 mannequin when installed, with a procedural preview fallback. No personal character mesh is bundled for this feature.

## Optional Hi3D refinement

**Powered by Hi3D 1.0.** Hi3D is licensed under the [Hi3D Open License Agreement Version 1.0](licenses/Hi3D-1.0-LICENSE.md). Copyright © 2026 OCEANO HK HOLDING LIMITED. Hi3D trademarks belong to OCEANO HK HOLDING LIMITED or its applicable affiliate(s).

### Multiview texture refinement

Windows Setup optionally downloads pinned [MV-Adapter source](https://github.com/huanngzh/MV-Adapter/tree/4277e0018232bac82bb2c103caf0893cedb711be) and the [image-and-geometry adapter](https://huggingface.co/huanngzh/mv-adapter/tree/6de4033df6b53366f3c009d22f5ec434bb55e59f) under Apache-2.0. The local download preserves the upstream source license.

The optional component also downloads the [archived Stable Diffusion 2.1 Base FP16 weights](https://huggingface.co/sd2-community/stable-diffusion-2-1-base/tree/4e63672c03103b6c636b8fb4119ba982469b2955). They are governed by the [CreativeML Open RAIL++-M License](licenses/Stable-Diffusion-2.1-OpenRAIL++-M.txt), including its use restrictions. Review the terms before installation and share the applicable restrictions with downstream users when making the component available on a server. The Setup executable contains the integration and model terms. It downloads the weights locally after acceptance.

Windows Setup requests license acceptance before obtaining the pinned Hi3D runtime and weights. See [Hi3D detail refinement](docs/HI3D.md) for upstream sources and requirements.

## Local license copies

The `licenses/` directory preserves the TRELLIS, UniRig, Three.js, U2-Net, Quaternius and DINOv3 notices used by this installation. The exact downloaded files, revisions and SHA-256 hashes are recorded in `installer/manifest.json`.
