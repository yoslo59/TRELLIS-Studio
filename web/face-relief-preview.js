import {createPreparationPreview} from './preparation-preview.js?v=face-refine';

function readPatch(buffer, info) {
    const header = new DataView(buffer);
    if (buffer.byteLength < 8 || header.getUint32(0) !== 0x46525031) throw Error('Invalid face preview.');
    const count = header.getUint32(4, true);
    if (!count || count !== info.count || buffer.byteLength !== 8 + count * 20) {
        throw Error('The face preview is incomplete.');
    }
    return {
        indices: new Uint32Array(buffer, 8, count),
        depths: new Float32Array(buffer, 8 + count * 4, count),
        normals: new Float32Array(buffer, 8 + count * 8, count * 3),
    };
}

export async function createFaceReliefPreview(container, sourceURL, info, signal) {
    const response = await fetch(info.patch_url, {signal});
    if (!response.ok) throw Error('The face preview expired. Preview the face again.');
    const patch = readPatch(await response.arrayBuffer(), info);
    signal.throwIfAborted();
    const scene = await createPreparationPreview(container, sourceURL, signal);
    try {
        signal.throwIfAborted();
        const meshes = [];
        scene.root.traverse(object => { if (object.isMesh) meshes.push(object); });
        const mesh = meshes[0];
        const position = mesh?.geometry.getAttribute('position');
        const normal = mesh?.geometry.getAttribute('normal');
        if (meshes.length !== 1 || !normal || position?.count !== info.vertex_count ||
            patch.indices.some(index => index >= position.count)) {
            throw Error('The source geometry has changed. Open Refine face again.');
        }
        const {indices, depths, normals} = patch;
        const originalZ = new Float32Array(indices.length);
        const originalNormals = new Float32Array(indices.length * 3);
        let first = position.count, last = 0;
        for (let i = 0; i < indices.length; i++) {
            const index = indices[i];
            first = Math.min(first, index);
            last = Math.max(last, index);
            originalZ[i] = position.getZ(index);
            originalNormals.set([normal.getX(index), normal.getY(index), normal.getZ(index)], i * 3);
        }
        function setStrength(strength) {
            for (let i = 0; i < indices.length; i++) {
                const index = indices[i], n = i * 3;
                position.setZ(index, originalZ[i] + depths[i] * strength);
                const x = originalNormals[n] + normals[n] * strength;
                const y = originalNormals[n + 1] + normals[n + 1] * strength;
                const z = originalNormals[n + 2] + normals[n + 2] * strength;
                const scale = strength === 0 ? 1 : Math.hypot(x, y, z) || 1;
                normal.setXYZ(index, x / scale, y / scale, z / scale);
            }
            for (const attribute of [position, normal]) {
                const buffer = attribute.isInterleavedBufferAttribute ? attribute.data : attribute;
                const stride = attribute.isInterleavedBufferAttribute ? buffer.stride : attribute.itemSize;
                buffer.addUpdateRange(first * stride, (last - first + 1) * stride);
                buffer.needsUpdate = true;
            }
        }
        const frame = () => scene.frameFace(mesh, info.face_center, info.face_span);
        frame();
        return {setStrength, frame, dispose: () => scene.dispose()};
    } catch (error) {
        scene.dispose();
        throw error;
    }
}
