# UltraTex · 2K preview

UltraTex is an optional local texture pass for a single reference image and a
compatible static GLB. It produces six color and six metallic/roughness views
and bakes them into the existing 2048 × 2048 UV atlas. It preserves positions,
indices and UVs. References and generated meshes are processed on the host.

This guide covers the optional six-view preview. UltraTex for the native
High Detail and Game Asset workflows is installed with those profiles in Setup.
See [Standalone generation profiles](NATIVE_PROFILES.md#windows-setup).

## Setup

Select **UltraTex · 2K preview** in Windows Setup and review the detected NVIDIA
GPU, driver and available VRAM. Setup requires a supported Windows CUDA
profile, at least 12,000 MiB of VRAM and about 16 GiB of additional free disk
space. The reference configuration is an RTX 4070 Ti with 12 GB VRAM and
32 GB system RAM. Texture view inference uses CUDA and the runtime can
offload weights to system RAM. The Setup executable contains the Studio
integration. The runtime and approximately 14 GB of weights are downloaded
separately.

Review the [FLUX.2 Klein 9B terms](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9B/blob/main/LICENSE.md)
before enabling the component. The bundled recipe is for local, non-commercial,
non-production evaluation. This includes private evaluation if using the
Server edition. See [third-party terms and attribution](../THIRD_PARTY_NOTICES.md#ultratex-preview).

## Use it

- In **Create → Output Quality**, choose **UltraTex** under **Texture method**
  with a single reference. Studio selects a 2K atlas for this pass.
- In **Prepare → Finishing board**, choose the UltraTex texture method for
  a compatible completed model with a square 2K atlas and its saved reference
  cutout. Compare the linked result with the original.
- Use **Repair or resume components** if Setup reports an interrupted download.
  Model files are stored under `models/ultratex`.
