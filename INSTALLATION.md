# Installation and recovery

This guide targets **Windows x64**. TRELLIS generation runs in native Windows Python;
UniRig runs in a separate **WSL2 Ubuntu 22.04** environment. Blender is used in private
background processes for conversion and preparation. The web interface is local.

## Prerequisites

| Item | Requirement |
| --- | --- |
| Windows | Windows 11 x64 is the reference system. WSL2 hardware virtualisation must be enabled for rigging. |
| GPU | NVIDIA CUDA GPU. The validated full setup is RTX 4070 Ti / 12 GB VRAM. 16 GB+ gives more headroom. CPU-only, AMD and Apple GPU generation are not supported by this installer. |
| Driver | Current NVIDIA Windows driver compatible with PyTorch's CUDA 12.8 runtime. Use the [official driver installer](https://www.nvidia.com/Download/index.aspx). A separate native CUDA Toolkit/compiler is not required by the supplied Windows wheels. |
| RAM | 32 GB recommended. Memory demand depends on resolution, mesh size and export/preparation settings. |
| Disk | 80 GiB free recommended for a full setup, including model/package caches and initial outputs. The full pinned model/animation download is about 20.6 GiB. Your collection needs additional storage. |
| Node.js | Current LTS x64 release, version 22 or newer, with npm. [Official installer](https://nodejs.org/en/download). |
| Blender | Install Blender 5.2 LTS x64, the validated version family. [Blender downloads](https://www.blender.org/download/). |
| WSL | WSL2 with a working Ubuntu-22.04 distribution and Linux user account for UniRig. Native-only setup is available with `-SkipRig`. |
| Browser | Current Edge or Chrome recommended. Turntable video export requires WebCodecs. |
| Network | HTTPS access to GitHub, Hugging Face, PyPI, PyTorch wheels, PyG wheels and OpenGameArt during installation. |

**Python 3.11 and uv are provisioned privately by the installer.** You do not need
Conda, a global Python installation or Git when installing from the source ZIP.
The source ZIP does not contain the engines, model weights or Python environments.
The Windows port and UniRig use different PyTorch/CUDA versions in separate environments.

UniRig's pinned Torch 2.5/CUDA 12.4/FlashAttention setup was validated on an Ada GPU.
Do not assume that a newer GPU architecture, especially Blackwell, is supported by
these pinned Linux wheels. Driver/runtime compatibility is checked, but additional
hardware still needs validation. `-SkipRig` leaves the Windows workflow available.

## Prepare Windows

Install Node.js and Blender using their official installers. If you use WinGet,
these commands open the corresponding package installation flow:

```powershell
winget install --exact --id OpenJS.NodeJS.LTS
winget install --exact --id BlenderFoundation.Blender
```

Close and reopen your terminal after changing PATH. Check `node --version` and
`npm --version`. If Blender is installed in an unusual location, set the executable
path for the current terminal before setup/startup:

```powershell
$env:TRELLIS_BLENDER = 'D:\Applications\Blender\blender.exe'
```

### Prepare WSL for rigging

From an **Administrator PowerShell**, install WSL if needed:

```powershell
wsl --install -d Ubuntu-22.04
```

Restart Windows if requested. Open Ubuntu once, create your Linux username and
password, then let Ubuntu complete first-run setup. Update its package index:

```powershell
wsl -d Ubuntu-22.04 -- sudo apt-get update
wsl -d Ubuntu-22.04 -- nvidia-smi
```

The second command should see your Windows NVIDIA GPU. WSL uses the Windows driver;
**do not install a separate Linux NVIDIA display driver inside WSL**. See Microsoft's
[WSL installation](https://learn.microsoft.com/windows/wsl/install) and
[GPU compute guide](https://learn.microsoft.com/windows/wsl/tutorials/gpu-compute).
The Studio installer does not reboot Windows or accept the Linux first-run prompts
for you. Once these prerequisites are ready, the remaining installation is scripted.

## Automatic installation

1. Download the repository ZIP or clone the repository.
2. Extract to a short, writable local path such as `C:\AI\TRELLIS-Studio`.
   Avoid network shares, cloud-synchronised folders and very long paths.
3. Double-click **Install TRELLIS Studio.cmd**.
4. Read `licenses/DINOv3-LICENSE.md`. Type `ACCEPT` only after you agree.
5. Leave the window open. Large first-time downloads and Python packages can take
   considerable time. On an error, correct the reported problem and run it again.
6. After verification, the launcher opens the application. Later launches only
   require **Open TRELLIS.cmd**.

Equivalent PowerShell command:

```powershell
.\Install-Studio.ps1
```

The `.cmd` entry point uses a process-local PowerShell execution-policy setting to
run the checked-out script; it does not change the machine's execution policy.

### What the installer does

1. Downloads **uv 0.13.7** from Astral's pinned release and checks SHA-256 before
   executing it. Installs a private CPython 3.11 under `.tools/python`.
2. Retrieves the pinned Windows TRELLIS port and UniRig source archives. Verifies
   hashes, validates archive paths and preserves upstream license files.
3. Applies the necessary Studio O-Voxel adapters: namespace integration, the
   pure-PyTorch UV rasteriser and the validated texture/mesh finishing changes.
4. Creates `.venv` for native inference/UI and `.mcp-venv` for the agent bridge.
   Installs pinned native PyTorch/CUDA/xformers, project dependencies and the three
   required Windows mesh wheels from the pinned source archive.
5. Runs `npm ci --omit=dev --ignore-scripts` for Three.js and Swagger UI.
6. Downloads **512 and 1024 TRELLIS checkpoints**, DINOv3 conditioning assets,
   U2-Net background-removal weights, UniRig skeleton/skin checkpoints and the two
   free Quaternius Standard animation packs. Downloads support HTTP resume and
   verify expected size and SHA-256 before becoming completed files.
7. Creates personal runtime configuration only when absent. Installs UniRig into
   the Linux user's `~/.local/share/trellis-unirig`, keeping its Python separate.
   Required Linux shared libraries are extracted into that private runtime.
8. Checks native CUDA/imports, Blender, the animation catalogue, MCP and UniRig.

UniRig's transformer architecture configuration is created locally. Inference
loads UniRig's own checkpoint; it does not download pretrained OPT language-model
weights, a text tokenizer or an extra Hugging Face configuration on first use.

The installer does not run the upstream all-purpose installer. Background removal
uses **rembg/U2-Net**, and the Studio UV baking path uses its **MIT PyTorch
rasteriser**. Optional research-only upstream render packages and RMBG-2.0 weights
are not installed by the Studio dependency selection.

A model/configuration already present with the expected hash is reused. A completed
file with a different hash is preserved and reported; it is not silently replaced.
Partial downloads use `.part` files. User models, trash, profiles and collection
state are not installer cleanup targets. Source directories from an unknown setup
are left alone with an error so that a manual migration can be reviewed.

### Options

```powershell
# Print the pinned download manifest without installing anything.
.\Install-Studio.ps1 -Plan

# Native generation/import/export + MCP; omit UniRig source/weights/environment.
.\Install-Studio.ps1 -SkipRig

# Install without opening a browser when finished.
.\Install-Studio.ps1 -NoLaunch

# Select an already configured WSL distribution.
.\Install-Studio.ps1 -WslDistribution Ubuntu-22.04

# Recheck an installed setup without changing dependencies.
.\Install-Studio.ps1 -VerifyOnly
# Use -SkipRig too when checking a native-only installation.
```

`-AcceptDinoV3License` is available for users who have already read and accepted
those exact terms, including scripted deployment under their own authority. It
must not be supplied by another agent as a substitute for your decision.

`config/runtime.json` stores the selected WSL distribution. Preserve or review it
when changing distributions. `TRELLIS_UNIRIG_ENV` can select a different Linux
runtime directory; set it within the selected WSL user's environment.

## Starting, stopping and connections

Double-click **Open TRELLIS.cmd** or **Ouvrir TRELLIS.cmd**. The launcher checks
UniRig and Blender, starts/reuses the local services and opens Studio:

- UI/API: `http://127.0.0.1:7860`
- MCP HTTP: `http://127.0.0.1:7861/mcp`
- Reference: `http://127.0.0.1:7860/developers`

An unrelated process using either port is preserved and reported. The launcher
waits for active Studio computation before replacing an outdated backend. Engine
weights load on demand; starting the interface does not keep both engines on the GPU.

Use the Studio stop control or **Stop TRELLIS.cmd** (or **Arreter TRELLIS.cmd**) to stop its API. The lightweight
HTTP MCP bridge is independent and can remain available for discovery; asset tools
need the API to be running. STDIO MCP connections are owned by their client process.
A bridge left running from a different version needs a manual restart; the launcher
does not forcibly kill an unrelated port owner. See [MCP setup](docs/MCP.md).

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Node/npm not found | Install Node LTS, reopen the terminal, check PATH. |
| Blender not found | Install Blender or set `TRELLIS_BLENDER` to its executable. |
| CUDA unavailable / driver too old | Check `nvidia-smi`, update the official Windows NVIDIA driver, restart if requested. |
| WSL distribution missing | Complete WSL installation and Ubuntu first-run setup, then rerun the installer. |
| UniRig check fails | Inspect `logs/unirig-demarrage-erreurs.log`, confirm WSL GPU access and the selected distribution. The Windows-only workflow can be installed with `-SkipRig`. |
| `apt-get download` cannot find a package | Run `sudo apt-get update` in the selected Ubuntu distribution and retry. |
| Incomplete download / network timeout | Re-run the same command. Keep the matching `.part` file to resume. |
| Checksum mismatch | Preserve existing user files. Remove only the specifically reported download/`.part` file after checking the error, then retry from the pinned manifest. |
| Existing source directory is unrecognised | Do not overwrite it. Use a new empty installation folder or review its provenance before migrating. |
| Out of GPU memory | Finish other GPU tasks, try 512 geometry and lower settings, then retry explicitly. Do not assume more VRAM is available from a lower target triangle count alone. |
| Out of system RAM / disk | Free space outside the user library or reduce batch/output complexity. Keep collection backups. |
| Model looks damaged / motion deforms poorly | Inspect geometry, skeleton and skin weights. Technical success does not guarantee an accepted asset. |
| Turntable export unavailable | Use a current Edge/Chrome with WebCodecs. PNG capture remains available independently. |
| Tools absent in an existing agent chat | Reload the MCP connection or open a new chat after registration. |

## Updating and uninstalling

Use Studio's collection backup before updating. Download/merge source updates while
preserving ignored personal directories, and rerun the installer for its pinned
runtime requirements. Do not move an installed virtual environment to a different
path and expect it to remain portable; recreate it in the new location instead.

To uninstall, stop the Studio services, export any collection you want to keep,
and remove the installation folder yourself. UniRig's Linux environment lives
separately under `~/.local/share/trellis-unirig`; remove it only if no other Studio
installation uses it. Never uninstall the user's WSL distribution or shared GPU
software as an automatic Studio cleanup step.

## Validation scope

The application has been exercised on the reference Windows/RTX workstation, with
isolated API, model/export and MCP checks. The installation manifest pins the exact
engine archives and downloaded asset hashes. Installer validation does not replace
trying a full generation and rig on your specific hardware. Report the stage and
redacted logs when requesting support; do not upload your personal library or tokens.
