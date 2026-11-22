# TRELLIS Studio 3.15.0

## Generation profiles

- **High Detail** uses two geometry passes with QuadDC and MeshLib and targets up to 2 million triangles. It defaults to **UltraTex 2K** when the standalone texture runtime is ready. **TRELLIS.2 PBR 4K** is also available.
- **Game Asset** targets 12,000 triangles with surface reconstruction, UV unwrapping, baked normals, and multiview UltraTex 2K. Both native profiles offer TRELLIS.2 and PiXal3D for the first geometry pass when their components are configured.
- **Create** and **Queue** now offer four built-in profiles and your saved recipes. **High Detail** opens by default when its geometry runtime is ready. **High Detail (Props)** is the fallback. **Use at startup** saves your choice in the browser.
- Open **Workflow graph** to edit connected nodes in **High Detail**, **Game Asset**, **Figure Master**, and **High Detail (Props)**. Edits are shared with **Advanced Settings**. **Save a copy** stores a custom Props or Figure Master recipe. Historical standard recipes remain available for saved models and API clients.

## Finishing and reliability

- **Texture Projection**, **Finishing board**, and **Game Copy from HD** support native outputs when the required model data is available.
- **Finishing board** can create an UltraTex material variant using a saved reference and the model's existing geometry and UVs.
- WTiVo reports the reconstruction grid and preserves its partial log when a timeout occurs. Standalone UltraTex uses a shorter compiler-cache path on Windows.
- Windows model imports retry status and settings updates when a reader briefly holds a file open.

## Windows Setup

- The x64 Setup includes the Desktop and Server applications and the native workflow adapters. The new standalone runtimes and weights are provisioned separately. Game Asset also uses Blender. The profile picker shows missing dependencies. See [Native profiles](NATIVE_PROFILES.md) and [Windows Setup](WINDOWS_SETUP.md).
- The UltraTex workflows use FLUX.2 Klein base 9B under its non-commercial, non-production terms. See the [UltraTex guide](ULTRATEX.md) for the model terms and runtime details.
- Verify the unsigned Setup against `SHA256SUMS.txt`. Close Studio or stop Server before updating an existing packaged installation.
