# Optional face refinement for Windows

**Prepare → Refine face** uses a portrait to reshape the face on a static character. Its original UV coordinates, textures and body geometry are retained. Open a completed model, check the proposed portrait crop, then select **Preview face**. Switch between **Original** and **Refined** while orbiting the face. Adjust **Intensity** and select **Create copy** to save a separate model.

## Install the geometry component on the Studio host

In Windows Setup, select **TOPOS face refinement** on the **Choose the components** page. This selects the TRELLIS.2 GPU runtime that TOPOS uses. The option is available in both Desktop and Server editions. On Server, install it on the machine that processes the jobs. To continue an interrupted download, open **Repair or resume components** from the Start menu.

For a source installation with a working Studio CUDA environment, run the following command from the Studio directory.

```powershell
.\.venv\Scripts\python.exe installer\install_topos.py
```

The installer verifies existing components and downloads missing files to their canonical locations. The three geometry weights use about 3.49 GB in `models/topos`. The sources and extra Python packages live under `.tools/topos`. The environment reuses Studio’s PyTorch installation and its shared YuNet face detector. The measured footprint on an existing CUDA installation was about 3.58 GB. Downloads can be resumed by running the command again.

To verify the installed files, dependency versions and CUDA availability offline, run the following command.

```powershell
.\.venv\Scripts\python.exe installer\install_topos.py --verify-only
```

## Sources and licenses

TOPOS provides the geometry code, topology and VAE/DiT weights under its MIT license. The component uses the DINOv2 ViT-L/14 image encoder with registers under Apache 2.0. The shared YuNet detector is distributed under the license included in OpenCV Zoo. The installer pins upstream revisions, archive digests and the SHA-256 of each model file. [TOPOS license](https://github.com/ymxbj/TOPOS/blob/581ad9e218cc7559072960ea44464014c808cef3/LICENSE) · [DINOv2 license](https://github.com/facebookresearch/dinov2/blob/7764ea0f912e53c92e82eb78a2a1631e92725fc8/LICENSE) · [YuNet license](https://github.com/opencv/opencv_zoo/blob/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet/LICENSE).
