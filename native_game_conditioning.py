"""Standalone DINOv3 and NAF vision features for Character 05.

The NAF image encoder follows ValeoAI's Apache-2.0 architecture. The workflow's
attention uses a zero-padded window, implemented here without NATTEN.
"""

from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.nn import functional as F

from native_game_asset import _require_hash


DINO_NAF_CHECKPOINT = '4ad2ec4e0879a5b5b04cd97325cc37da954a7b6edca5170b86510f17f2b2290f'
PATCH_SIZE = 16
GLOBAL_TOKENS = 5
NAF_HEADS = 4
NAF_KERNEL = 9


@dataclass(frozen=True)
class VisionFeatures:
    global_tokens: torch.Tensor
    patches: torch.Tensor


def conditioning_image(rgb, resolution, *, device):
    """Resize the composited RGB image with the graph's byte Lanczos filter."""
    rgb = np.asarray(rgb)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError('Expected an H × W × 3 RGB byte image.')
    resized = Image.fromarray(rgb).resize((resolution, resolution), Image.Resampling.LANCZOS)
    image = np.asarray(resized, dtype=np.float32) / 255.0
    return torch.from_numpy(image).permute(2, 0, 1).unsqueeze(0).to(device)


@torch.inference_mode()
def encode_dinov3(model, image):
    """Return CLS/register tokens and the patch grid with unaffine normalization."""
    if image.ndim != 4 or image.shape[1] != 3:
        raise ValueError('Expected a BCHW RGB tensor.')
    height, width = image.shape[-2:]
    if not height or not width or height % PATCH_SIZE or width % PATCH_SIZE:
        raise ValueError('Image dimensions must be positive multiples of 16.')
    mean = image.new_tensor((0.485, 0.456, 0.406)).view(1, 3, 1, 1)
    std = image.new_tensor((0.229, 0.224, 0.225)).view(1, 3, 1, 1)
    pixels = (image - mean) / std
    pixels = pixels.to(dtype=next(model.parameters()).dtype)
    hidden = model.embeddings(pixels)
    positions = model.rope_embeddings(pixels)
    hidden = model.model(hidden, position_embeddings=positions).last_hidden_state
    tokens = F.layer_norm(hidden, (hidden.shape[-1],))
    patches = tokens[:, GLOBAL_TOKENS:].transpose(1, 2).reshape(
        image.shape[0], -1, height // PATCH_SIZE, width // PATCH_SIZE,
    ).contiguous()
    return VisionFeatures(tokens[:, :GLOBAL_TOKENS], patches)


class NAFImageBlock(nn.Module):
    def __init__(self, channels, kernel):
        super().__init__()
        self.norm1 = nn.GroupNorm(8, channels)
        self.conv1 = nn.Conv2d(channels, channels, kernel, padding=kernel // 2, padding_mode='reflect')
        self.norm2 = nn.GroupNorm(8, channels)
        self.conv2 = nn.Conv2d(channels, channels, kernel, padding=kernel // 2, padding_mode='reflect')

    def forward(self, image):
        first = self.conv1(F.silu(self.norm1(image)))
        return self.conv2(F.silu(self.norm2(first)))


class NAFRotaryPosition(nn.Module):
    def __init__(self):
        super().__init__()
        self.register_buffer('periods', torch.empty(16))

    def forward(self, features):
        batch, channels, height, width = features.shape
        row = torch.arange(height, device=features.device, dtype=torch.float32).add(0.5).div(height)
        col = torch.arange(width, device=features.device, dtype=torch.float32).add(0.5).div(width)
        coords = torch.stack(torch.meshgrid(row, col, indexing='ij'), dim=-1).reshape(-1, 2) * 2 - 1
        phase = (2 * math.pi * coords[..., None] / self.periods.float()).flatten(1).tile(2)
        cos, sin = phase.cos().to(features.dtype), phase.sin().to(features.dtype)
        values = features.reshape(batch, NAF_HEADS, channels // NAF_HEADS, -1).transpose(-2, -1)
        first, second = values.chunk(2, dim=-1)
        rotated = values * cos + torch.cat((-second, first), dim=-1) * sin
        return rotated.transpose(-2, -1).reshape(batch, channels, height, width)


class NAFImageEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = self._stream(1)
        self.sem_encoder = self._stream(3)
        self.rope = NAFRotaryPosition()

    @staticmethod
    def _stream(kernel):
        channels = 128
        return nn.Sequential(
            nn.Conv2d(3, channels, kernel, padding=kernel // 2, padding_mode='reflect'),
            NAFImageBlock(channels, kernel),
            NAFImageBlock(channels, kernel),
        )

    def forward(self, image, size):
        limit = tuple(min(current, 4 * target) for current, target in zip(image.shape[-2:], size))
        if limit != image.shape[-2:]:
            image = F.interpolate(image, size=limit, mode='bilinear', align_corners=False)
        features = torch.cat((self.encoder(image), self.sem_encoder(image)), dim=1)
        return self.rope(F.adaptive_avg_pool2d(features, size))


def _head_grid(tensor):
    batch, channels, height, width = tensor.shape
    return tensor.reshape(batch, NAF_HEADS, channels // NAF_HEADS, height * width).transpose(-2, -1)


def _neighbor_indices(positions, width, low_height, low_width, row_scale, col_scale, offsets):
    """Indices in the LR grid for dilated nearest-expanded, zero-padded K/V."""
    rows = positions.div(width, rounding_mode='floor').div(row_scale, rounding_mode='floor')
    cols = positions.remainder(width).div(col_scale, rounding_mode='floor')
    rows = rows[:, None] + offsets[:, 0]
    cols = cols[:, None] + offsets[:, 1]
    present = (rows >= 0) & (rows < low_height) & (cols >= 0) & (cols < low_width)
    indices = rows.clamp(0, low_height - 1) * low_width + cols.clamp(0, low_width - 1)
    return indices, present[None, None, :, :, None]


def _zero_padded_attention(query, key, values, output_device, *, block_size):
    batch, query_channels, height, width = query.shape
    low_height, low_width = key.shape[-2:]
    query = _head_grid(query)
    key = _head_grid(key)
    values = _head_grid(values)
    value_channels = values.shape[-1]
    output = torch.empty((batch, NAF_HEADS, value_channels, height * width), device=output_device, dtype=query.dtype)
    axis = torch.arange(NAF_KERNEL, device=query.device) - NAF_KERNEL // 2
    offsets = torch.cartesian_prod(axis, axis)
    scale = (query_channels // NAF_HEADS) ** -0.5
    for start in range(0, height * width, block_size):
        stop = min(height * width, start + block_size)
        positions = torch.arange(start, stop, device=query.device)
        indices, present = _neighbor_indices(
            positions, width, low_height, low_width, height // low_height, width // low_width, offsets,
        )
        key_neighbors = key[:, :, indices, :].masked_fill_(~present, 0)
        scores = (query[:, :, start:stop, None, :] @ key_neighbors.transpose(-2, -1)) * scale
        weights = scores.softmax(dim=-1)
        del key_neighbors, scores
        # Preserve the zero-valued padded neighbors in softmax. They cannot be
        # masked out as they would be in standard neighborhood attention.
        for channel in range(0, value_channels, 64):
            end_channel = min(channel + 64, value_channels)
            neighbors = values[:, :, indices, channel:end_channel].masked_fill_(~present, 0)
            block = (weights @ neighbors).squeeze(-2).transpose(-2, -1)
            output[:, :, channel:end_channel, start:stop] = block
    return output.reshape(batch, -1, height, width)


class NAFFeatureUpsampler(nn.Module):
    """Image-guided NAF with Character 05's zero-padded attention boundaries."""

    def __init__(self):
        super().__init__()
        self.image_encoder = NAFImageEncoder()

    @torch.inference_mode()
    def forward(self, image, features, size, *, output_device=None, block_size=256):
        if image.ndim != 4 or image.shape[1] != 3 or features.ndim != 4 or image.shape[0] != features.shape[0]:
            raise ValueError('NAF needs matching BCHW reference and patch batches.')
        if not all(features.shape):
            raise ValueError('NAF needs a nonempty patch grid.')
        if len(size) != 2 or any(target < 1 or target % source for target, source in zip(size, features.shape[-2:])):
            raise ValueError('NAF target dimensions must be positive multiples of the patch grid.')
        if block_size < 1 or features.shape[1] % NAF_HEADS:
            raise ValueError('NAF needs a positive block size and four equally sized value heads.')
        parameter = next(self.parameters())
        image = image.to(device=parameter.device, dtype=parameter.dtype)
        values = features.to(device=parameter.device, dtype=parameter.dtype)
        query = self.image_encoder(image, size)
        key = F.adaptive_avg_pool2d(query, features.shape[-2:])
        output_device = parameter.device if output_device is None else output_device
        return _zero_padded_attention(query, key, values, output_device, block_size=block_size)


@torch.inference_mode()
def project_front_features(feature_map, fov_degrees, image_size, voxel_resolution, *, sparse_xyz=None):
    """Sample a DINO or NAF feature map from the workflow's front-view voxels.

    Dense sampling returns one row per voxel in XYZ order. Sparse sampling
    preserves the order of the integer voxel coordinates within a single job.
    """
    if feature_map.ndim != 4 or feature_map.shape[0] != 1:
        raise ValueError('Projection needs a single BCHW feature map.')
    if not 0 < fov_degrees < 180 or min(image_size, voxel_resolution) < 1:
        raise ValueError('Projection needs a valid field of view and positive resolutions.')
    if sparse_xyz is None:
        axis = torch.linspace(-1.0, 1.0, voxel_resolution, device=feature_map.device)
        grid = torch.stack(torch.meshgrid(axis, axis, axis, indexing='ij'), dim=-1).reshape(-1, 3)
    else:
        if sparse_xyz.ndim != 2 or sparse_xyz.shape[1] != 3:
            raise ValueError('Sparse voxel coordinates must have shape N × 3.')
        grid = sparse_xyz.to(device=feature_map.device, dtype=torch.float32)
        grid = grid / (voxel_resolution - 1) * 2 - 1 if voxel_resolution > 1 else grid * 0
    rotation = grid.new_tensor(((1, 0, 0), (0, 0, -1), (0, 1, 0)))
    world = grid @ rotation.T / 2.0
    fov = math.radians(fov_degrees)
    distance = 0.5 / math.tan(fov / 2)
    transform = grid.new_tensor(((1, 0, 0, 0), (0, 0, -1, -distance), (0, 1, 0, 0), (0, 0, 0, 1)))
    homogeneous = torch.cat((world, torch.ones_like(world[:, :1])), dim=-1)
    camera = homogeneous @ torch.linalg.inv(transform).T
    depth = -camera[:, 2] + 1e-8
    focal_pixels = (16.0 / torch.tan(grid.new_tensor(fov) / 2.0)) * image_size / 32.0
    pixels = torch.stack((focal_pixels * camera[:, 0] / depth, -focal_pixels * camera[:, 1] / depth), dim=-1)
    pixels += image_size / 2.0
    ndc = (pixels + 0.5) / image_size * 2.0 - 1.0
    samples = F.grid_sample(feature_map, ndc.view(1, -1, 1, 2).to(feature_map.dtype),
                            mode='bilinear', padding_mode='border', align_corners=False)
    return samples[0, :, :, 0].T


def load_dinov3_naf(checkpoint, *, device, naf_dtype=torch.float32, dino_dtype=None):
    """Load the pinned combined checkpoint with exact schemas for both models."""
    from safetensors import safe_open
    from transformers import DINOv3ViTConfig, DINOv3ViTModel

    checkpoint = Path(checkpoint)
    _require_hash(checkpoint, DINO_NAF_CHECKPOINT, 'Character 05 DINOv3/NAF checkpoint is missing or has an unexpected SHA-256.')
    config = DINOv3ViTConfig(
        hidden_size=1024, intermediate_size=4096, num_attention_heads=16, num_hidden_layers=24,
        patch_size=PATCH_SIZE, num_register_tokens=4, rope_theta=100.0, pos_embed_rescale=2.0,
        key_bias=False, query_bias=True, value_bias=True, proj_bias=True, mlp_bias=True,
        layer_norm_eps=1e-5, use_gated_mlp=False, attn_implementation='sdpa',
    )
    with torch.device('meta'):
        dino = DINOv3ViTModel(config)
        naf = NAFFeatureUpsampler()
    vision_weights, upsampler_weights = {}, {}
    with safe_open(checkpoint, framework='pt', device='cpu') as weights:
        for key in weights.keys():
            tensor = weights.get_tensor(key)
            if key.startswith('naf.'):
                upsampler_weights[key.removeprefix('naf.')] = tensor.to(device=device, dtype=naf_dtype)
            else:
                state_key = 'model.' + key if key.startswith('layer.') else key
                vision_weights[state_key] = tensor.to(device=device, dtype=dino_dtype)
    dino.load_state_dict(vision_weights, strict=True, assign=True)
    naf.load_state_dict(upsampler_weights, strict=True, assign=True)
    # The inverse frequencies are derived from the config and are not saved.
    rope = type(dino.rope_embeddings)(config).to(device)
    dino.rope_embeddings = rope
    return dino.eval(), naf.eval()
