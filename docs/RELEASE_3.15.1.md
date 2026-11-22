# TRELLIS Studio 3.15.1

## Windows Setup

- Full Generation Setup can provision High Detail and Game Asset with both geometry engines and the standalone UltraTex pipeline.
- Setup checks the selected NVIDIA GPU and presents the FLUX.2 and MeshLib terms. It verifies pinned downloads and tests the local runtimes before marking the component ready.
- Game Asset uses a Python installation owned by Studio. Its standard library stays available when development or QA folders are removed.
- High Detail and the base TRELLIS.2 engine read DINOv3 from the same installed checkpoint. Native Setup keeps weights at their runtime paths and installs Python packages without NTFS hard links.
- Setup uses a detected Blender installation or downloads the pinned portable build for Game Asset finishing.

See [Windows Setup](WINDOWS_SETUP.md) for component requirements and repair options.
