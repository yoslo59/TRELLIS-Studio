# Windows x64 installer

`TRELLIS-Studio-<version>-Setup-x64.exe` is the per-user NSIS installer for Windows x64.
It copies the application and viewer and adds Start menu entries. It leaves GPU
drivers and WSL to Windows. The executable is unsigned. Do not
treat a SHA-256 checksum as an Authenticode signature.

## Install and open

1. Run Setup. The default folder is `%LOCALAPPDATA%\Programs\TRELLIS Studio`.
   First choose **TRELLIS Desktop (single user)** or **TRELLIS Server (multi-user)**.
   Both Server and Desktop include the Workshop and its standard animation files.
   Server setup includes initial administrator creation and an access choice
   covering local use, LAN HTTPS or automatic Internet HTTPS. See
   [TRELLIS Server](SERVER.md) for network settings.
   Review the NVIDIA GPU, driver and VRAM on **Check this PC**. Use **Browse**
   to choose `blender.exe` when needed. Select **TRELLIS.2 image-to-3D** for
   local generation. On a fresh installation with a supported GPU, **High
   Detail and Game Asset** is selected on the next page. Review and accept both
   the FLUX.2 Klein 9B and MeshLib terms to install the two native workflows with
   UltraTex.
   Clear the selection to install standard TRELLIS.2 only. PiXal3D is
   included in the native workflow bundle. The earlier PiXal3D component
   and UniRig are separate choices.
   The installer asks for applicable model licences and tests the GPU packages
   before the corresponding model downloads. If a component fails, use
   **Repair or resume components** in the Start menu. See
   `logs/component-setup.log` for the failing step.
   **TOPOS face refinement** adds portrait-based face reconstruction to **Prepare**.
   Selecting it includes the TRELLIS.2 GPU runtime and downloads 3.49 GB of geometry
   weights plus the shared face detector. Both editions use the same option.
   [Face refinement](FACE_REFINEMENT.md)

   **Hi3D detail refinement** is a separate selection for compatible NVIDIA GPUs.
   On a new installation it is unchecked. Setup displays the detected GPU, driver
   and VRAM guidance before downloading the model, and asks you to accept the
   Hi3D Open License. See [Hi3D](HI3D.md) for storage and compatibility details.

   **Multiview texture refinement** is optional in both editions and requires
   the TRELLIS.2 generation runtime. Setup checks for an eligible NVIDIA
   compute 8.6 or 8.9 GPU with at least 8 GB VRAM, and recommends 12 GB.
   Allow roughly 3.3 GB of additional model downloads and 4 GB of free space.
   Setup presents the model terms before downloading the pinned files.
   **Repair or resume components** can resume an interrupted installation.
   Read the [model terms](../THIRD_PARTY_NOTICES.md#multiview-texture-refinement)
   before offering the component to Server users.

   **UltraTex · 2K preview** is the separate optional six-view compatibility component. Setup
   checks CUDA eligibility and shows VRAM, memory and storage guidance. The
   FLUX.2 Klein 9B weights require acceptance of non-commercial, non-production
   terms. The native workflows include their own UltraTex runtime.
   See the [UltraTex guide](ULTRATEX.md) before enabling the six-view preview.

   The separate **Optional motion generation** page offers Kimodo for either
   edition and component selection. It is unchecked by default. Selecting it
   requires explicit acceptance of the NVIDIA Open Model License and Meta Llama 3
   Community License. Setup then downloads, builds, tests and configures the CPU
   runtime automatically. Allow approximately 9.3 GB of additional weights and at
   least 12 GiB free inside an initialized WSL2 Linux x64 distribution. Python
   3.10+, glibc 2.35+ and an AVX2/FMA/F16C CPU are required. Ubuntu 22.04 or newer
   is the reference; this component needs neither NVIDIA CUDA nor a system C++
   compiler. Setup does not enable WSL, create a Linux user or restart Windows.
2. Open **TRELLIS Studio** or **TRELLIS Server** from the Start menu. Setup can
   also create a desktop shortcut. The workshop downloads the private Python
   runtime and libraries over HTTPS during Server setup or the first Desktop
   launch in older installers. Current Setup prepares Workshop during installation.
   The shortcut opens a tray controller with Start, Stop, Open and Quit actions.
   Quitting the icon leaves services running. It runs on the CPU with the bundled web viewer.
3. Import a GLB with embedded assets. The workshop supports viewing, inspection,
   library management, and GLB export without Blender. Blender is needed for
   importing other formats, some uncommon GLB extensions, and Blender-dependent
   preparation and export formats. Select an existing Blender with **Browse** or
   set `TRELLIS_BLENDER` to its executable.

The Workshop installs pinned **PyMeshLab 2025.7.post1** for
CPU-only HD-to-game reduction (about 55 MB downloaded from PyPI, not bundled in
the executable). Setup verifies its native reduction filter with a small mesh.
**Baked game copies** additionally need Blender 5.2 LTS for UVs and PBR/normal
transfer. Setup discovers standard Blender installations and can prepare a
private portable copy with the native Game Asset component. A Workshop-only
installation works without Blender. Texture projection runs on the CPU.

Collection cards use two saved WebP renders. Studio creates them in an
installed Edge, Chrome or Chromium renderer on the host while compute jobs
are idle. Until a preview pair is ready, the card displays its reference image.

**HD Master Finishing** supports Studio 768 on a host with the TRELLIS.2
runtime and a compatible saved source. WTiVo 1024 requires Blender and a
compatible runtime. See the [WTiVo configuration guide](WTIVO.md).

The Studio header shows the installed version. When a newer stable release is
available, **Update** opens a link to its release notes. The backend checks the
[public GitHub Releases API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)
at most once per day after a successful check and sends no Studio user data.
Dismiss an update to hide its indicator in this browser.

To update an existing installation, stop Studio/Server and rerun Setup into the
same folder. Changed dependency pins trigger a Workshop refresh without moving
the collection or resetting accounts and HTTPS settings. The Desktop launcher
also checks the saved dependency fingerprint before starting. See the
[CPU finishing setup guide](../INSTALLATION.md#cpu-texture-projection-and-baked-game-copies)
for source-installation commands and portable Blender configuration.

On an existing TRELLIS.2 installation, generation setup adds about 709 MB for
the material refinement encoder used by **Retexture after Hi3D**.

For local generation, stop the Studio with **Stop TRELLIS Studio** in the Start
menu. Choose **Install TRELLIS generation** for the NVIDIA build without WSL, or
**Install UniRig** after setting up Ubuntu 22.04 in WSL. The
generation installer shows the DINOv3 terms before downloading the weights.
**Install Pixal3D** is available once standard generation is installed. To add
the optional **Pixal3D · Single image** method, run `"Install Pixal3D.cmd" --single-image`
from the installation folder in Command Prompt. This adds approximately 6.4 GiB
of checkpoint downloads to the Pixal3D installation. The
[requirements and troubleshooting guide](../INSTALLATION.md) covers the driver,
disk space, optional WSL setup and verified downloads.

**Install Kimodo motion generation** adds or resumes the optional CPU engine
later, including on a Workshop-only installation. The helper selects a compatible
existing WSL2 distribution and presents the model licences if they have not been
accepted for this invocation. Pinned downloads and a private portable toolchain
are verified before use; the runtime configuration is activated after a short
generation check. Existing custom configuration is preserved. If WSL is missing,
complete its first-run setup and rerun this shortcut. See
[generated motions](MOTION_GENERATION.md) for exact versions, storage locations
and practical limits. Saved motions remain reusable when generation is disabled.

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
Server configuration and Caddy certificate storage are also retained. If you enabled
the optional elevated Windows Firewall rule, remove it separately in Windows Firewall
when permanently retiring that installation; the per-user uninstaller does not elevate.
The optional Kimodo engine and verified cache in the Linux user's home directory
are also retained; they are not embedded in the Setup executable or a collection ZIP.

## GPU profiles

The installer selects packages from [the GPU manifest](../installer/gpu-runtime-profiles.json)
by CUDA compute capability. RTX 30 and 40 use the WSL CUDA 12.4 UniRig profile.
RTX 50 select a separate experimental CUDA 12.8 profile. The Blackwell profile uses
matching prebuilt FlashAttention, `cumm` and `spconv` packages, with
[reproducible builds for the sparse-convolution wheels](https://github.com/fafraob/spconv-wheels).
RTX 20 generation is experimental and UniRig is unavailable in this profile.
GTX 10 can use the Workshop and CPU animation features where their prerequisites
are met. VRAM below the 12 GB reference triggers an advisory, with TRELLIS.2 at
512 as a starting point. GPU and export tests on a target card remain the basis
for support claims.

The Hi3D option checks for CUDA capability 8.6 or 8.9 and at least 8,000 MiB of
detected VRAM, using the driver's existing native CUDA 12.8 requirement.
Setup recommends Detail Refinement (512) below 12,000 MiB. The 1024 pass can
need more memory on complex assets or when other applications are using the
GPU. The Setup can continue with the other supported components on GPUs
outside Hi3D's supported range.

## Standalone generation profiles

The **High Detail and Game Asset** option prepares the Studio-native geometry,
mesh finishing and UltraTex runtimes, downloads verified model weights and
configures the installation. It requires an eligible NVIDIA GPU with CUDA
capability 8.6 or 8.9 and at least 12,000 MiB of VRAM. Start with at least
75 GiB free for a fresh TRELLIS.2 installation with both native workflows.
Review the disk check on **Check this PC** before continuing. ComfyUI is
unnecessary for these workflows.
The Setup page links to the FLUX.2 model terms and the non-commercial MeshLib
license for the bundled geometry recipe.

To add the native workflows to an existing installation, rerun Setup into the
same folder and select **High Detail and Game Asset**. Setup reuses verified
files. To resume a download or retry a verification, open **Repair or resume
components** from the Start menu. Consult `logs/component-setup.log` for the
failed stage. See [Native profiles](NATIVE_PROFILES.md) for storage and advanced
configuration.

## Build and qualify

Build the public installer from a clean checkout of the merged `main` commit.
Install Python 3.11 or newer, Node.js and obtain
[NSIS 3.13](https://nsis.sourceforge.io/Download). Then run:

```powershell
.\installer\windows\Build-Setup.ps1 -Compiler 'C:\path\to\makensis.exe' -OutputDirectory 'C:\path\to\release'
```

The script compiles the tray using the Windows .NET Framework C# compiler,
verifies the pinned Caddy binary, and assembles the source allowlist and browser dependencies in
a separate staging directory, checks JavaScript syntax, and writes the
installer, its SHA-256, and the payload inventory under `Builds\Windows`.
No working library, model weights, caches, or personal configuration are copied.
Both source and payload inventories pass `installer/audit_public.py` before packaging.
The domain and certificates are created only on the user's own installation.
The [NSIS license](https://nsis.sourceforge.io/License) applies to the installer
engine; TRELLIS Studio retains its MIT license.

Use `-Preview` for a private test build with separate installation paths and
shortcuts. The default build keeps the public installer identity. Update
`package.json`, `package-lock.json` and the English release notes in the same PR.

Before distributing a binary, test installation from a fresh Windows account
or VM, no-GPU workshop startup, self-contained GLB import/export, interruption
and recovery, reinstall with a collection, and removal with data retention.
Validate generation and rig on supported hardware separately. Publish the
checksum and actual support matrix with the binary in GitHub Releases. The tag
must point to the merged commit used to build the executable. After publication,
download both public assets and verify that their bytes match the audited build.

For public Authenticode signing, [SignPath Foundation](https://signpath.org/terms.html)
is free for accepted open source projects. The application must already exist
in the form to be signed; review the scope of downloaded model and GPU
dependencies with the foundation. [Azure Artifact Signing](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)
starts at USD 9.99/month; its [Public Trust eligibility](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart#prerequisites)
for individuals is limited to the US and Canada. Signing does not guarantee
that SmartScreen will immediately recognise a newly distributed binary.
