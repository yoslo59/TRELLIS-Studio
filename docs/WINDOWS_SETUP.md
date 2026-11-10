# Windows x64 installer preview

`TRELLIS-Studio-3.3.1-Setup-x64.exe` is a per-user NSIS installer for Windows x64.
It copies the application and viewer, adds Start menu entries, and never installs
GPU drivers, Blender or WSL. The executable in this preview is unsigned. Do not
treat a SHA-256 checksum as an Authenticode signature.

## Install and open

1. Run Setup. The default folder is `%LOCALAPPDATA%\Programs\TRELLIS Studio`.
   Choose **Workshop only** or **Full Studio**. Full Studio installs TRELLIS.2,
   UniRig and Pixal3D during Setup. Allow around 29 GiB for model downloads plus
   dependencies and roughly 100 GiB of free space for installation and caches.
   Prepare a supported NVIDIA driver, Blender and Ubuntu 22.04 under WSL2 first.
   The console displays progress and asks for explicit acceptance of the
   DINOv3 terms. If a download or component fails, see `logs/full-setup.log`
   in the installation folder and rerun Setup with **Full Studio** selected.
2. Open **TRELLIS Studio** from the Start menu. Setup can also create an optional
   shortcut with the Studio icon on your desktop. With Workshop only, the first
   launch downloads the private Python runtime and workshop libraries over HTTPS.
   Node.js, Blender, NVIDIA CUDA, WSL and model weights are not required for this step.
3. Import a GLB with embedded assets. The workshop supports viewing, inspection,
   library management, and GLB export without Blender. Blender is needed for
   importing other formats, some uncommon GLB extensions, and Blender-dependent
   preparation and export formats. Install Blender separately or set
   `TRELLIS_BLENDER` to its executable.

For local generation, stop the Studio with **Stop TRELLIS Studio** in the Start
menu. Choose **Install TRELLIS generation** for the NVIDIA build without WSL, or
**Install generation and UniRig** after setting up Ubuntu 22.04 in WSL. The
generation installer shows the DINOv3 terms before downloading the weights.
**Install Pixal3D** is available once standard generation is installed. The
[requirements and troubleshooting guide](../INSTALLATION.md) covers the driver,
disk space, optional WSL setup and verified downloads.

The packaged copy keeps a separate library from a source installation. The
default packaged ports are 7870 (Studio) and 7871 (MCP), separate from the
source installation's 7860/7861. Override them with `TRELLIS_PORT` and
`TRELLIS_MCP_PORT` if needed. Transfer a collection using the Studio ZIP backup/restore
workflow. The installer will not adopt or replace a source installation in place.

Close Studio and its component installer before running Setup again. If the
installed copy is open during uninstallation, the uninstaller offers to stop
it and its local MCP bridge. It waits for the user to finish any generation or
export before it will proceed; a separate MCP client may also need to be closed.
The uninstaller removes packaged program files and Start menu entries. Library,
profiles, runtime downloads and installed environments stay in the installation
directory, so reinstalling there can reuse them.

## Build and qualify

From a TRELLIS Studio checkout, install Node.js and obtain
[NSIS 3.13](https://nsis.sourceforge.io/Download). Then run:

```powershell
.\installer\windows\Build-Setup.ps1 -Compiler 'C:\path\to\makensis.exe'
```

The script assembles the source allowlist and pinned browser dependencies in
a separate staging directory, checks JavaScript syntax, and writes the
installer, its SHA-256, and the payload inventory under `Builds\Windows`.
No working library, model weights, caches, or personal configuration are copied.
The [NSIS license](https://nsis.sourceforge.io/License) applies to the installer
engine; TRELLIS Studio retains its MIT license.

Before distributing a binary, test installation from a fresh Windows account
or VM, no-GPU workshop startup, self-contained GLB import/export, interruption
and recovery, reinstall with a collection, and removal with data retention.
Validate generation and rig on supported hardware separately. Publish the
signature, checksum and actual support matrix with the binary in GitHub Releases.

For public Authenticode signing, [SignPath Foundation](https://signpath.org/terms.html)
is free for accepted open source projects. The application must already exist
in the form to be signed; review the scope of downloaded model and GPU
dependencies with the foundation. [Azure Artifact Signing](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)
starts at USD 9.99/month; its [Public Trust eligibility](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart#prerequisites)
for individuals is limited to the US and Canada. Signing does not guarantee
that SmartScreen will immediately recognise a newly distributed binary.
