import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export async function openReductionRegionPreview(container, {
    url, isCurrent, disposeModel, limit, statusId, onPick,
}) {
    const gltf = await new GLTFLoader().loadAsync(url);
    if (!isCurrent()) {
        disposeModel(gltf.scene);
        return null;
    }

    let renderer;
    let controls;
    let observer;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, 1, .001, 1000);
    const zones = Array.from({ length: limit }, () => new THREE.Vector4());
    const zoneCount = { value: 0 };
    const selectedZone = { value: -1 };
    const strengths = new Float32Array(limit);
    const ray = new THREE.Raycaster();
    const crosshair = new THREE.Vector2();
    const reticle = document.createElement('span');
    reticle.className = 'gc-reticle';
    reticle.hidden = true;
    reticle.setAttribute('aria-hidden', 'true');
    let pickMode = false;

    scene.add(gltf.scene);
    gltf.scene.updateMatrixWorld(true);
    const mesh = gltf.scene.getObjectByProperty('isMesh', true);
    const bounds = new THREE.Box3().setFromObject(gltf.scene);
    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    const span = size.length();

    try {
        if (!mesh || !Number.isFinite(span) || span <= 0) throw new Error('The model has no visible surface.');
        mesh.geometry.computeBoundingBox();
        const localSpan = mesh.geometry.boundingBox.getSize(new THREE.Vector3()).length();
        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
        renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
        renderer.toneMapping = THREE.ACESFilmicToneMapping;
        renderer.toneMappingExposure = 1.2;
        const canvas = renderer.domElement;
        canvas.tabIndex = 0;
        canvas.setAttribute('role', 'group');
        canvas.setAttribute('aria-label', '3D model preview');
        canvas.setAttribute('aria-describedby', statusId);
        container.prepend(canvas, reticle);

        controls = new OrbitControls(camera, canvas);
        controls.enableDamping = false;
        controls.zoomToCursor = true;
        controls.minDistance = span * .015;
        controls.maxDistance = span * 12;
        scene.add(new THREE.HemisphereLight(0xf6fff1, 0x5b6857, 3));
        const key = new THREE.DirectionalLight(0xffffff, 2.4);
        key.target.position.copy(center);
        key.position.copy(center).add(new THREE.Vector3(1, 1.7, 2).multiplyScalar(span));
        scene.add(key, key.target);

        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        for (const material of new Set(materials)) {
            material.onBeforeCompile = shader => {
                shader.uniforms.protectedZones = { value: zones };
                shader.uniforms.protectedStrengths = { value: strengths };
                shader.uniforms.protectedZoneCount = zoneCount;
                shader.uniforms.activeProtectedZone = selectedZone;
                shader.vertexShader = 'varying vec3 vProtectionPosition;\n' + shader.vertexShader.replace(
                    '#include <begin_vertex>', '#include <begin_vertex>\nvProtectionPosition = position;',
                );
                // Coordinates and the falloff match the reduction's local-space sphere weights.
                shader.fragmentShader = `
                    varying vec3 vProtectionPosition;
                    uniform vec4 protectedZones[${limit}];
                    uniform float protectedStrengths[${limit}];
                    uniform int protectedZoneCount;
                    uniform int activeProtectedZone;
                ` + shader.fragmentShader.replace('#include <color_fragment>', `
                    #include <color_fragment>
                    float protection = 0.0;
                    float border = 0.0;
                    for (int i = 0; i < ${limit}; i++) {
                        if (i >= protectedZoneCount) break;
                        vec4 zone = protectedZones[i];
                        float radiusDistance = length(vProtectionPosition - zone.xyz) / zone.w;
                        float weight = pow(clamp(2.0 * (1.0 - radiusDistance), 0.0, 1.0), 1.5);
                        protection = max(protection, weight * protectedStrengths[i]);
                        if (i == activeProtectedZone) {
                            float width = max(fwidth(radiusDistance) * 1.4, 0.012);
                            border = 1.0 - smoothstep(0.0, width, abs(radiusDistance - 1.0));
                        }
                    }
                    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.39, 0.82, 0.13), protection * 0.78);
                    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.65, 0.93, 0.25), border * 0.9);
                `);
            };
            material.customProgramCacheKey = () => `reduction-zones-${limit}`;
        }

        let pressed = null;
        canvas.addEventListener('pointerdown', event => {
            if (!event.isPrimary) { pressed = null; return; }
            if (pickMode && event.button === 0) pressed = [event.clientX, event.clientY];
        });
        canvas.addEventListener('pointercancel', () => { pressed = null; });
        canvas.addEventListener('pointerup', event => {
            if (!pickMode || !event.isPrimary || !pressed) return;
            const [x, y] = pressed;
            pressed = null;
            if (Math.hypot(event.clientX - x, event.clientY - y) > 5) return;
            const rect = canvas.getBoundingClientRect();
            pick(new THREE.Vector2(2 * (event.clientX - rect.x) / rect.width - 1,
                1 - 2 * (event.clientY - rect.y) / rect.height));
        });
        canvas.addEventListener('keydown', event => {
            if (!pickMode) return;
            const step = event.shiftKey ? .12 : .03;
            const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowDown: [0, -step], ArrowUp: [0, step] }[event.key];
            if (!delta && !['Enter', ' '].includes(event.key)) return;
            event.preventDefault();
            if (delta) crosshair.add(new THREE.Vector2(...delta)).clampScalar(-.97, .97);
            reticle.hidden = false;
            reticle.style.left = `${50 * (1 + crosshair.x)}%`;
            reticle.style.top = `${50 * (1 - crosshair.y)}%`;
            if (!delta) pick(crosshair);
        });
        canvas.addEventListener('blur', () => { reticle.hidden = true; });
        controls.addEventListener('change', render);
        resize();
        fit();
        observer = new ResizeObserver(resize);
        observer.observe(container);
        return { localSpan, fit, zoom, setMode, setZones, dispose };
    } catch (error) {
        dispose();
        throw error;
    }

    function render() {
        if (renderer && container.clientWidth && container.clientHeight) renderer.render(scene, camera);
    }

    function resize() {
        const width = Math.max(1, container.clientWidth);
        const height = Math.max(1, container.clientHeight);
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        render();
    }

    function fit() {
        const view = new THREE.Vector3(.32, .17, 1).normalize();
        const right = camera.up.clone().cross(view).normalize();
        const up = view.clone().cross(right);
        const frustumHeight = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
        const frustumWidth = frustumHeight * camera.aspect;
        let distance = span * .55;
        for (const x of [-.5, .5]) {
            for (const y of [-.5, .5]) {
                for (const z of [-.5, .5]) {
                    const corner = new THREE.Vector3(x * size.x, y * size.y, z * size.z);
                    const fitDistance = Math.max(Math.abs(corner.dot(right)) / frustumWidth,
                        Math.abs(corner.dot(up)) / frustumHeight);
                    distance = Math.max(distance, corner.dot(view) + fitDistance * 1.2);
                }
            }
        }
        camera.position.copy(center).addScaledVector(view, distance);
        camera.near = span * .001;
        camera.far = Math.max(distance, controls.maxDistance) + span * 2;
        camera.updateProjectionMatrix();
        controls.target.copy(center);
        controls.update();
        render();
    }

    function zoom(factor) {
        const offset = camera.position.clone().sub(controls.target);
        offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance));
        camera.position.copy(controls.target).add(offset);
        controls.update();
        render();
    }

    function pick(point) {
        ray.setFromCamera(point, camera);
        const [hit] = ray.intersectObject(mesh, false);
        onPick(hit ? mesh.worldToLocal(hit.point).toArray() : null);
    }

    function setMode(mode) {
        pickMode = mode === 'pick';
        controls.enableRotate = !pickMode;
        controls.enablePan = !pickMode;
        controls.enableZoom = true;
        if (!pickMode) reticle.hidden = true;
    }

    function setZones(regions, active) {
        zoneCount.value = regions.length;
        selectedZone.value = active;
        for (const [index, region] of regions.entries()) {
            zones[index].set(...region.center, region.radius);
            strengths[index] = region.strength;
        }
        render();
    }

    function dispose() {
        observer?.disconnect();
        controls?.dispose();
        disposeModel(gltf.scene);
        renderer?.dispose();
        renderer?.forceContextLoss();
        renderer?.domElement.remove();
        renderer = null;
        reticle.remove();
    }
}
