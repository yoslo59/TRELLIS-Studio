# TRELLIS Studio 3.15.3

## Materials and exports

Open **Material** beside **Prepare** to adjust a model’s roughness and metallic response. Studio remembers the settings for each model in the current browser and applies them in Compare, LOD inspection and preparation previews. GLB, Blender, Unity and Unreal exports use the adjusted material by default. Select **Use original materials** when you want to export the source finish.

## Generation graphs and High Detail

- Follow the active workflow node during generation. The graph shows the path used by the selected recipe and records elapsed time for each reported stage. Reopen it from the generation log to see where a completed run spent its time.
- The profile selector shows an observed duration range once your collection has at least three completed original generations with matching settings.
- **High Detail with UltraTex** prepares the reference mask while geometry is being generated. For opaque meshes that need UVs, Studio unwraps the surface and skips the TRELLIS.2 material pass that UltraTex would replace. Both geometry passes, the UltraTex views and the chosen atlas resolution are preserved.
- UltraTex can write its temporary texture files inside long Windows installation and job paths.

## Optional face refinement

Use **Prepare → Refine face** on a textured character before rigging. Crop the original reference around the face or supply a closer portrait. Preview the result in 3D, adjust **Intensity** and select **Create copy**. TOPOS reshapes the face on the copy. The body, UVs and existing textures, including UltraTex maps, are preserved. Face refinement runs only when requested. [Face refinement guide](https://github.com/yoslo59/TRELLIS-Studio/blob/v3.15.3/docs/FACE_REFINEMENT.md)

## Windows Setup

The x64 Setup serves both Desktop and Server. Select **TOPOS face refinement** on the generation host to download the three pinned geometry and image-encoder weights (about 3.49 GB). Setup verifies their SHA-256 hashes. **Repair or resume components** can complete an interrupted installation. TOPOS uses Studio’s CUDA runtime and has been tested on an NVIDIA RTX 4070 Ti with 12 GB of VRAM.

The CPU Workshop remains available for viewing and library management. High Detail and Game Asset require a supported NVIDIA GPU with CUDA compute capability 8.6 or 8.9 and at least 12,000 MiB of VRAM. For an update, close Studio or stop Server and run Setup in the existing installation folder. Verify the unsigned executable against the published `SHA256SUMS.txt`. [Windows requirements and setup guide](https://github.com/yoslo59/TRELLIS-Studio/blob/v3.15.3/docs/WINDOWS_SETUP.md)
