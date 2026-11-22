"""CPU-testable camera, schedule and noise stages of Character 05 UltraTex.

Calibration and schedule based on UltraTex-ComfyUI-nodes 4dea3ff0c61e0ec0aa82808ffe2299f14089db92 (GPL-3.0).
"""

from dataclasses import dataclass
import math

import numpy as np
import torch


FOCAL = 6400.0 / 4096.0
ATLAS_ORDER = ((0, 1, 3), (2, 4, 5))
# TexVerse calibration in the Blender coordinate frame.
POSES = np.asarray((
    ((1, 0, 0, 0), (0, 0, -1, -1.67), (0, 1, 0, 0)),
    ((0, 0, -1, -1.67), (-1, 0, 0, 0), (0, 1, 0, 0)),
    ((-1, 0, 0, 0), (0, 0, 1, 1.67), (0, 1, 0, 0)),
    ((0, 0, 1, 1.67), (1, 0, 0, 0), (0, 1, 0, 0)),
    ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 1.67)),
    ((-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, -1, -1.67)),
), dtype=np.float32)
GLTF_TO_BLENDER = np.asarray(((1, 0, 0), (0, 0, -1), (0, 1, 0)), dtype=np.float32)


@dataclass(frozen=True)
class ViewPass:
    task: str
    yaw: float
    seed: int
    view_resolution: int
    steps: int
    guidance: float
    denoise: float
    sparse_retention: float
    use_reprojected_init: bool
    tiled_decode: bool
    lora: str
    lora_strength: float
    decoder: str


def character05_passes(prompt):
    """Make one pass for the original cameras and optionally one for the diagonal rig."""
    passes = []
    for node, task in (('358', 'albedo'), ('364', 'mr')):
        entry = prompt[node]
        config = entry['inputs']
        if entry['class_type'] != 'UTXGenerateTextureViewsV23' or config['task'] != task:
            raise ValueError('Unexpected Character 05 texture branch.')
        view_count = config['view_count']
        if view_count not in (6, 10, 12):
            raise ValueError('UltraTex supports 6, 10 or 12 views.')
        for index in range(1 if view_count == 6 else 2):
            guided = index == 1 and task == 'albedo' and config['extra_angle_mode'] == 'guided'
            passes.append(ViewPass(
                task=task,
                yaw=45.0 * index,
                seed=(int(config['seed']) + index) % (2**63),
                view_resolution=int(config['view_resolution']),
                steps=int(config['extra_steps'] if guided else config['steps']),
                guidance=float(config['guidance']),
                denoise=float(config['extra_denoise'] if guided else config['denoise']),
                sparse_retention=float(config['sparse_retention']),
                use_reprojected_init=guided,
                tiled_decode=bool(config['tiled_decode']),
                lora=config['lora_name'],
                lora_strength=float(config['lora_strength']),
                decoder=config['decoder_name'],
            ))
    return tuple(passes)


def fitted_cameras(vertices, yaw, *, target_fill=0.94, fit=True):
    """Return the six world-to-camera matrices in the UltraTex calibration."""
    radians = np.radians(yaw)
    c, s = np.cos(radians), np.sin(radians)
    yaw_rotation = np.asarray(((c, 0, s), (0, 1, 0), (-s, 0, c)), np.float32)
    frame = np.eye(4, dtype=np.float32)
    frame[:3, :3] = GLTF_TO_BLENDER @ yaw_rotation
    poses = np.broadcast_to(np.eye(4, dtype=np.float32), (6, 4, 4)).copy()
    poses[:, :3] = POSES
    matrices = np.linalg.inv(poses) @ frame
    if not fit:
        return matrices, 0.0
    threshold = target_fill / 2.0
    shift = -0.40
    for matrix in matrices:
        for start in range(0, len(vertices), 65536):
            points = vertices[start:start + 65536] @ matrix[:3, :3].T + matrix[:3, 3]
            required = (FOCAL / threshold) * np.abs(points[:, :2]).max(axis=1)
            shift = max(shift, float((np.maximum(required, 0.1) + points[:, 2]).max()))
    matrices[:, 2, 3] -= shift
    return matrices, shift


def diffusion_times(view_pass):
    """Exact 1024-view time warp used by UltraTex sparse flow sampling."""
    if view_pass.steps < 1 or not 0 <= view_pass.denoise <= 1:
        raise ValueError('UltraTex needs positive steps and denoise between 0 and 1.')
    if view_pass.denoise < 1 and not view_pass.use_reprojected_init:
        raise ValueError('UltraTex denoise under 1 needs an encoded initialization.')
    if view_pass.denoise == 0:
        return [0.0]
    resolution = view_pass.view_resolution
    length = ((resolution * 3) // 8) * ((resolution * 2) // 8) // 256
    shift = math.exp((1.15 - 0.5) / (4096 - 256) * (length - 256) + 0.5)
    steps = torch.linspace(1.0, 0.0, view_pass.steps + 1, device='cpu', dtype=torch.float32)
    warped = (shift / (shift + (1 / steps - 1))).tolist()
    if view_pass.denoise < 1:
        return [view_pass.denoise, *(time for time in warped if 0 <= time < view_pass.denoise)]
    return warped


def foreground_noise(view_pass, foreground_indices):
    """Draw CPU noise for the full six-view grid before the foreground gather."""
    latent_edge = view_pass.view_resolution // 16
    grid_length = (2 * latent_edge) * (3 * latent_edge)
    generator = torch.Generator(device='cpu').manual_seed(view_pass.seed)
    noise = torch.randn((1, grid_length, 128), generator=generator, dtype=torch.float32, device='cpu')
    return noise[:, foreground_indices]
