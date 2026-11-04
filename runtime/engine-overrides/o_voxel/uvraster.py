# Pure-PyTorch UV-space rasteriser for texture baking.
#
# Drop-in replacement for the one thing o_voxel.postprocess.to_glb used nvdiffrast for:
# deciding which texel belongs to which triangle of the unwrapped mesh, and where in 3D
# that texel sits, so the attribute volume can be sampled there. nvdiffrast is licensed
# for research and evaluation only; this file is MIT.
#
# Conventions match nvdiffrast's output as measured on a single-triangle probe:
#   texel (row r, col c) is sampled at uv = ((c + 0.5) / size, (r + 0.5) / size),
#   row 0 is v = 0. The GLB exporter's own V flip and trimesh's export flip cancel out,
#   so nothing downstream changes.
#
# Charts from the unwrapper never overlap, so there is no depth test: a texel inside a
# triangle belongs to it. Shared edges may be claimed by either neighbour; both give the
# same surface point to within a texel, and the inpainting pass afterwards hides seams.

from typing import Tuple

import torch


def _bounded_chunks(widths, heights, budget):
    """Bound the shared raster grid, including mixed wide and tall boxes."""
    start, gw, gh = 0, 0, 0
    for end, (width, height) in enumerate(zip(widths, heights)):
        next_w, next_h = max(gw, width), max(gh, height)
        if end > start and (end - start + 1) * next_w * next_h > budget:
            yield start, end, gw, gh
            start, gw, gh = end, width, height
        else:
            gw, gh = next_w, next_h
    if len(widths) > start:
        yield start, len(widths), gw, gh


@torch.no_grad()
def rasterize_uv(
    uvs: torch.Tensor,
    faces: torch.Tensor,
    size: int,
    candidate_budget: int = 24_000_000,
    eps: float = 1e-6,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Args:
        uvs:   (V, 2) float, UV coordinates in [0, 1].
        faces: (F, 3) integer vertex indices.
        size:  texture width == height in texels.
        candidate_budget: how many (triangle, texel) candidates to evaluate per chunk.
            Bounds peak memory; 24 M candidates is a few hundred MB on the GPU.

    Returns:
        mask: (size, size) bool — texel is covered by some triangle.
        tri:  (size, size) long — index of the covering triangle, -1 where uncovered.
        bary: (size, size, 3) float — barycentric weights of the covering triangle's
              three vertices at the texel centre (sum to 1 where covered).
    """
    device = uvs.device
    uvs = uvs.to(torch.float32)
    faces = faces.to(torch.long)
    F = faces.shape[0]

    tri_out = torch.full((size, size), -1, dtype=torch.long, device=device)
    bary_out = torch.zeros((size, size, 3), dtype=torch.float32, device=device)
    if F == 0:
        return tri_out >= 0, tri_out, bary_out

    # Triangle corners in continuous pixel coordinates where texel i is centred at i.
    p = uvs[faces] * size - 0.5                       # (F, 3, 2)
    x, y = p[..., 0], p[..., 1]

    # Signed doubled area; degenerate triangles cover nothing.
    d = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    live = d.abs() > 1e-12

    # Per-triangle texel bounding boxes, clamped to the texture.
    xmin = x.min(1).values.ceil().clamp(0, size - 1).long()
    xmax = x.max(1).values.floor().clamp(0, size - 1).long()
    ymin = y.min(1).values.ceil().clamp(0, size - 1).long()
    ymax = y.max(1).values.floor().clamp(0, size - 1).long()
    bw = (xmax - xmin + 1).clamp(min=0)
    bh = (ymax - ymin + 1).clamp(min=0)
    live &= (bw > 0) & (bh > 0)

    idx_all = torch.nonzero(live).flatten()
    if idx_all.numel() == 0:
        return tri_out >= 0, tri_out, bary_out

    # Process triangles in order of bounding-box size so each chunk's shared candidate grid
    # (max bw × max bh of the chunk) is not dominated by one big flat triangle.
    area = (bw * bh)[idx_all]
    order = torch.argsort(area)
    idx_all = idx_all[order]
    area = area[order]

    # Sorted box areas do not bound max(width) * max(height): a thin horizontal
    # and a thin vertical triangle can create an enormous shared grid together.
    # Plan chunks with the actual shared dimensions, not the largest individual area.
    widths = bw[idx_all].cpu().tolist()
    heights = bh[idx_all].cpu().tolist()
    for start, end, gw, gh in _bounded_chunks(widths, heights, candidate_budget):
        ids = idx_all[start:end]                       # (n_c,)
        dx = torch.arange(gw, device=device)
        dy = torch.arange(gh, device=device)
        cx = xmin[ids][:, None, None] + dx[None, None, :]      # (n_c, 1, gw)
        cy = ymin[ids][:, None, None] + dy[None, :, None]      # (n_c, gh, 1)
        inside_box = (cx <= xmax[ids][:, None, None]) & (cy <= ymax[ids][:, None, None])
        cx = cx.expand(-1, gh, -1)
        cy = cy.expand(-1, -1, gw)

        # Barycentrics via edge functions, normalised by the signed area so either winding
        # yields positive weights inside.
        px = cx.to(torch.float32)
        py = cy.to(torch.float32)
        x0, x1, x2 = (x[ids, k][:, None, None] for k in range(3))
        y0, y1, y2 = (y[ids, k][:, None, None] for k in range(3))
        dd = d[ids][:, None, None]
        w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) / dd
        w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) / dd
        w2 = 1.0 - w0 - w1
        inside = inside_box & (w0 >= -eps) & (w1 >= -eps) & (w2 >= -eps)

        if inside.any():
            flat = (cy * size + cx)[inside]
            tri_ids = ids[:, None, None].expand(-1, gh, gw)[inside]
            w = torch.stack([w0, w1, w2], dim=-1)[inside]
            # A texel whose centre lies exactly on an edge shared by two triangles of the
            # chunk appears twice in `flat`. Scattering id and weights as two separate writes
            # would let CUDA resolve the duplicates differently for each, pairing one
            # triangle's id with the other's weights. Pick one candidate per texel first
            # (the last in sorted order) and write both fields from that selection.
            order = torch.argsort(flat, stable=True)
            sorted_flat = flat[order]
            last = torch.ones_like(sorted_flat, dtype=torch.bool)
            last[:-1] = sorted_flat[1:] != sorted_flat[:-1]
            keep = order[last]
            tri_out.view(-1)[flat[keep]] = tri_ids[keep]
            bary_out.view(-1, 3)[flat[keep]] = w[keep]

        del cx, cy, px, py, w0, w1, w2, inside, inside_box

    return tri_out >= 0, tri_out, bary_out
