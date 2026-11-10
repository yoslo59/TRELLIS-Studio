"""Choose a finishing budget by measuring sampled surface error on the master."""
import torch
import cumesh


def budgets(limit):
    target = min(4_000, limit)
    while target < limit:
        yield target
        target = min(target * 4, limit)
    yield limit


def surface_samples(vertices, faces):
    # Sample vertices (thin details) and triangle interiors (lost openings and bulges).
    # Keep the sampling deterministic and independent of the generation seed.
    gen = torch.Generator(device=vertices.device).manual_seed(8219)
    count = 32_768

    def indices(size):
        if size <= count:
            return torch.arange(size, device=vertices.device)
        return torch.randint(size, (count,), generator=gen, device=vertices.device)

    selected = vertices[faces[indices(len(faces))].long()]
    return vertices[indices(len(vertices))], selected.mean(dim=1)


def max_p99(points, bvh):
    distances, _, _ = bvh.unsigned_distance(torch.cat(points))
    p99 = max(float(torch.quantile(d, 0.99)) for d in distances.split([len(p) for p in points]))
    if not torch.isfinite(distances).all():
        raise ValueError('Unable to measure the surface error of this mesh.')
    return p99


@torch.no_grad()
def choose_mesh(vertices, faces, master_bvh, ceiling, voxel_size, verbose):
    tolerance_voxels = 0.75
    master_samples = surface_samples(vertices, faces)
    trials = []
    for budget in budgets(min(ceiling, len(faces))):
        mesh = cumesh.CuMesh()
        mesh.init(vertices, faces)
        mesh.remove_duplicate_faces()
        mesh.repair_non_manifold_edges()
        mesh.simplify(budget, verbose=False)
        mesh.remove_duplicate_faces()
        mesh.repair_non_manifold_edges()
        points, triangles = mesh.read()
        if len(triangles) <= 8:
            del mesh, points, triangles
            continue
        candidate_bvh = cumesh.cuBVH(points, triangles)
        error = max(max_p99(master_samples, candidate_bvh),
                    max_p99(surface_samples(points, triangles), master_bvh)) / voxel_size
        del candidate_bvh, points, triangles
        trials.append(dict(target=budget, triangles=mesh.num_faces, p99_voxels=round(error, 5)))
        if verbose:
            print(f'Adaptive finishing: {mesh.num_faces:,} triangles, sampled p99 error {error:.3f} voxels', flush=True)
        passed = error <= tolerance_voxels
        if passed or budget == min(ceiling, len(faces)):
            mesh.unify_face_orientations()
            return mesh, dict(ceiling=ceiling, master_triangles=len(faces), selected_target=budget,
                              tolerance_voxels=tolerance_voxels, target_met=passed, trials=trials)
        del mesh
    raise ValueError('No valid mesh was found within the adaptive triangle ceiling.')
