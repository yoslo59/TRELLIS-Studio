"""Choose a finishing budget by measuring sampled surface error on the master."""
import torch
import cumesh


def budgets(limit, factor=4):
    target = min(4_000, limit)
    while target < limit:
        yield target
        target = min(target * factor, limit)
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


def master_error(points, bvh):
    distances, _, _ = bvh.unsigned_distance(torch.cat(points))
    if not torch.isfinite(distances).all():
        raise ValueError('Unable to measure the surface error of this mesh.')
    quantiles = distances.new_tensor([.99, .999])
    return torch.stack([torch.quantile(group, quantiles)
                        for group in distances.split([len(group) for group in points])]).amax(dim=0)


@torch.no_grad()
def choose_master_mesh(vertices, faces, reference_bvh, ceiling, voxel_size, verbose):
    # Reduce the large decoded surface once. Test lighter meshes from the same capped base
    # and measure against the original surface, so reductions cannot accumulate unnoticed.
    capped = cumesh.CuMesh()
    capped.init(vertices, faces)
    capped.simplify(ceiling * 3)
    capped.remove_duplicate_faces()
    capped.repair_non_manifold_edges()
    capped.remove_small_connected_components(1e-5)
    capped.simplify(ceiling)
    capped.remove_duplicate_faces()
    capped.repair_non_manifold_edges()
    if not 8 < capped.num_faces <= ceiling:
        raise ValueError('No valid mesh was found within the Dense Master triangle ceiling.')
    base_vertices, base_faces = capped.read()
    limit = len(base_faces)
    reference_samples = surface_samples(vertices, faces)

    def measure(points, triangles):
        bvh = cumesh.cuBVH(points, triangles)
        return torch.maximum(master_error(reference_samples, bvh),
                             master_error(surface_samples(points, triangles), reference_bvh)) / voxel_size

    capped_error = measure(base_vertices, base_faces)
    # Allow small differences from the capped mesh when its unavoidable error already
    # exceeds the absolute tolerance (for instance around noisy or perforated details).
    tolerance = torch.maximum(vertices.new_tensor([.75, 1.5]),
                              capped_error + vertices.new_tensor([.25, .5]))
    trials = []
    for target in budgets(limit, factor=2):
        candidate = capped
        if target < limit:
            candidate = cumesh.CuMesh()
            candidate.init(base_vertices, base_faces)
            candidate.simplify(target)
            candidate.remove_duplicate_faces()
            candidate.repair_non_manifold_edges()
        points, triangles = candidate.read()
        if len(triangles) > 8:
            error = capped_error if candidate is capped else measure(points, triangles)
            p99, p999 = error.tolist()
            passed = bool((error <= tolerance).all())
            trials.append(dict(target=target, triangles=len(triangles), p99_voxels=round(p99, 5),
                               p999_voxels=round(p999, 5)))
            if verbose:
                print(f'Dense Master: {len(triangles):,} triangles, sampled p99/p99.9 error '
                      f'{p99:.3f}/{p999:.3f} voxels', flush=True)
            if passed or target == limit:
                candidate.unify_face_orientations()
                tolerance_p99, tolerance_p999 = tolerance.tolist()
                return candidate, dict(ceiling=ceiling, master_triangles=len(faces), selected_target=target,
                                       tolerance_voxels=round(tolerance_p99, 5),
                                       tail_tolerance_voxels=round(tolerance_p999, 5),
                                       capped_p99_voxels=round(float(capped_error[0]), 5),
                                       capped_p999_voxels=round(float(capped_error[1]), 5),
                                       target_met=passed, trials=trials)
        del candidate, points, triangles
    raise ValueError('No valid mesh was found within the Dense Master triangle ceiling.')
