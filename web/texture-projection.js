import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import {
    fitLinkedAffine,
    fitOverlayRect,
    mapPoint,
    polygonBounds,
    scaleOverlay,
    validatePolygon,
    simplifyLasso,
} from './texture-projection-geometry.js?v=3.9.0-resize';
import { button, dialog, el, field, input, select } from './workbench-ui.js?v=3.4.0';
import { matchTextureProjectionCamera, readReferenceSilhouette } from './texture-projection-auto-align.js?v=3.7.0';
import { createGameCopyRegions } from './game-copy-regions.js?v=native-options';

const MATCH_RENDER_SIZE = 128;
const placementScaleLimits = [0.25, 3];
const overlayHandleOffsets = [
    [0, 0, 'nwse-resize'], [0.5, 0, 'ns-resize'], [1, 0, 'nesw-resize'],
    [0, 0.5, 'ew-resize'], [1, 0.5, 'ew-resize'],
    [0, 1, 'nesw-resize'], [0.5, 1, 'ns-resize'], [1, 1, 'nwse-resize'],
];

const cameraViews = {
    front: [0, 0], back: [180, 0], left: [-90, 0], right: [90, 0],
    top: [0, 90], bottom: [0, -90],
};
const generationViewFiles = {
    front: 'prepared.png', left: 'prepared-left.png', back: 'prepared-back.png', right: 'prepared-right.png',
};
const defaultCamera = () => ({ view: 'front', projection: 'orthographic', yaw: 0, pitch: 0, roll: 0, fov: 35 });
const titleCase = (value) => value[0].toUpperCase() + value.slice(1);
const wrapAngle = (value) => ((value + 180) % 360 + 360) % 360 - 180;
const bound = (value, min, max) => Math.max(min, Math.min(max, value));
const clamp = (value) => Math.max(0, Math.min(1, value));
const nameOf = (job) => job?.metadata?.display_name || job?.settings?.name || 'Selected model';
const validRect = (rect) =>
    rect.every(Number.isFinite) && rect[2] - rect[0] > 0.002 && rect[3] - rect[1] > 0.002;
const pointsReady = (points) =>
    points.length === 4 &&
    [0, 1, 2, 3].every((index) => points[index]?.length === 2 && points[index].every(Number.isFinite));

function disposeModel(root) {
    const geometries = new Set(),
        materials = new Set(),
        textures = new Set();
    root?.traverse((object) => {
        if (object.geometry) geometries.add(object.geometry);
        for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
            if (!material) continue;
            materials.add(material);
            for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
        }
    });
    for (const texture of textures) {
        texture.dispose();
        texture.image?.close?.();
    }
    for (const material of materials) material.dispose();
    for (const geometry of geometries) geometry.dispose();
}

function loadImage(url) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () =>
            reject(new Error('This reference image could not be opened. Choose another image.'));
        image.src = url;
    });
}

function referenceSilhouetteFor(image) {
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 256 / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext('2d', {willReadFrequently: true});
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return readReferenceSilhouette(context.getImageData(0, 0, canvas.width, canvas.height));
}

async function loadProjectionPreview(url, isCurrent) {
    let gltf, renderer, matchTarget, matchPixels, topDownPixels, keepPreview = false;
    try {
        gltf = await new GLTFLoader().loadAsync(url);
        if (!isCurrent()) return null;
        let skinned = false;
        gltf.scene.traverse((object) => {
            if (object.isSkinnedMesh) skinned = true;
            for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
                if (material) material.side = THREE.DoubleSide;
            }
        });
        if (skinned) throw new Error('Texture projection currently requires a static model.');
        gltf.scene.updateMatrixWorld(true);
        const box = new THREE.Box3().setFromObject(gltf.scene, true);
        if (box.isEmpty()) throw new Error('The model has no visible surface to project onto.');
        const size = box.getSize(new THREE.Vector3());
        const center = box.getCenter(new THREE.Vector3());
        const radius = Math.max(0.001, size.length() / 2);
        const corners = [-1, 1].flatMap((x) => [-1, 1].flatMap((y) => [-1, 1].map((z) =>
            new THREE.Vector3(x * size.x / 2, y * size.y / 2, z * size.z / 2),
        )));
        const scene = new THREE.Scene();
        scene.add(gltf.scene, new THREE.HemisphereLight(0xffffff, 0x566251, 2.8));
        const light = new THREE.DirectionalLight(0xffffff, 2.3);
        light.target.position.copy(center);
        scene.add(light, light.target);
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
        renderer.setSize(1024, 1024, false);
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        renderer.toneMapping = THREE.ACESFilmicToneMapping;
        const orthographic = new THREE.OrthographicCamera();
        const perspective = new THREE.PerspectiveCamera();
        keepPreview = true;
        return { render, sample, dispose };

        function sample(pose) {
            if (renderer.getContext().isContextLost()) throw new Error('The preview renderer is unavailable.');
            if (!matchTarget) {
                matchTarget = new THREE.WebGLRenderTarget(MATCH_RENDER_SIZE, MATCH_RENDER_SIZE, {
                    colorSpace: THREE.SRGBColorSpace,
                });
                matchPixels = new Uint8Array(MATCH_RENDER_SIZE ** 2 * 4);
                topDownPixels = new Uint8Array(matchPixels.length);
            }
            const previousTarget = renderer.getRenderTarget();
            try {
                renderer.setRenderTarget(matchTarget);
                render(pose);
                renderer.readRenderTargetPixels(matchTarget, 0, 0, MATCH_RENDER_SIZE, MATCH_RENDER_SIZE, matchPixels);
            } finally {
                renderer.setRenderTarget(previousTarget);
            }
            const stride = MATCH_RENDER_SIZE * 4;
            for (let row = 0; row < MATCH_RENDER_SIZE; row++) {
                const offset = (MATCH_RENDER_SIZE - row - 1) * stride;
                topDownPixels.set(matchPixels.subarray(offset, offset + stride), row * stride);
            }
            return { data: topDownPixels, width: MATCH_RENDER_SIZE, height: MATCH_RENDER_SIZE };
        }

        function render(pose) {
            const yaw = THREE.MathUtils.degToRad(pose.yaw);
            const pitch = THREE.MathUtils.degToRad(pose.pitch);
            const roll = THREE.MathUtils.degToRad(pose.roll);
            const outward = new THREE.Vector3(
                Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch),
            );
            const right = new THREE.Vector3(Math.cos(yaw), 0, -Math.sin(yaw));
            const up = new THREE.Vector3().crossVectors(outward, right);
            right.applyAxisAngle(outward, roll);
            up.applyAxisAngle(outward, roll);
            const coordinates = corners.map((p) => [p.dot(right), p.dot(up), p.dot(outward)]);
            const perspectiveMode = pose.projection === 'perspective';
            const camera = perspectiveMode ? perspective : orthographic;
            const recipe = { view: pose.view };
            if (pose.view === 'custom') recipe.basis = [right.toArray(), up.toArray(), outward.toArray()];
            let distance;
            if (perspectiveMode) {
                const half = Math.tan(THREE.MathUtils.degToRad(pose.fov / 2));
                distance = Math.max(...coordinates.map(([x, y, z]) =>
                    z + Math.max(Math.abs(x), Math.abs(y)) * 1.09 / half), radius * 1.05);
                camera.fov = pose.fov;
                recipe.projection = 'perspective';
                recipe.frame = [-half, -half, half, half];
            } else {
                const half = Math.max(...coordinates.flatMap(([x, y]) => [Math.abs(x), Math.abs(y)]), 0.0005) * 1.08;
                distance = radius * 3;
                const x = center.dot(right), y = center.dot(up);
                recipe.frame = [x - half, y - half, x + half, y + half];
                camera.left = camera.bottom = -half;
                camera.right = camera.top = half;
            }
            camera.position.copy(center).addScaledVector(outward, distance);
            camera.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(right, up, outward));
            camera.near = Math.max(radius / 100000, (distance - radius) / 2);
            camera.far = distance + radius * 2;
            camera.updateProjectionMatrix();
            if (perspectiveMode) recipe.origin = camera.position.toArray();
            light.position.copy(center).addScaledVector(right, radius)
                .addScaledVector(up, radius * 1.5).addScaledVector(outward, radius * 2);
            renderer.render(scene, camera);
            return { recipe, image: renderer.domElement };
        }
    } finally {
        if (!keepPreview) dispose();
    }

    function dispose() {
        matchTarget?.dispose();
        disposeModel(gltf?.scene);
        renderer?.dispose();
        renderer?.forceContextLoss();
        renderer?.domElement.remove();
    }
}

function toolbarGroup(label, id) {
    const group = el('div', 'tp-tool-group');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', label);
    if (id) group.id = id;
    return group;
}

function toolbarButton(group, id, label, icon, action, title = label) {
    const control = button(label, icon, 'secondary tp-tool', action);
    if (!icon) control.firstChild.remove();
    control.id = id;
    control.title = title;
    group.append(control);
    return control;
}

function dockButton(id, icon, label, action) {
    const control = button('', icon, 'tool-button', action);
    if (id) control.id = id;
    control.title = label;
    control.setAttribute('aria-label', label);
    return control;
}

export function createTextureProjection({ api, startJob }) {
    const ui = dialog(
        'textureProjection',
        'Texture Projection',
        '',
        '',
        'texture-projection-dialog',
    );
    const summary = el('p', 'tp-model-name');
    ui.element.querySelector('header > div').append(summary);
    const methodChoices = el('div', 'tp-method-choices');
    methodChoices.hidden = true;
    methodChoices.setAttribute('role', 'group');
    methodChoices.setAttribute('aria-label', 'Projection method');
    const manualMethod = button('', 'image', 'secondary tp-method-choice');
    const generationMethod = button('', 'camera', 'secondary tp-method-choice');
    manualMethod.id = 'projectionManualMethod';
    generationMethod.id = 'projectionGenerationMethod';
    for (const [control, label, caption] of [
        [manualMethod, 'Align references', 'Auto Align or manual controls'],
        [generationMethod, 'Use generation views', 'Pixel Match · Saved cameras'],
    ]) {
        const copy = el('span', 'tp-method-copy');
        const detail = el('small', '', caption);
        detail.id = control.id + 'Detail';
        copy.append(el('strong', '', label), detail);
        control.lastChild.replaceWith(copy);
        control.setAttribute('aria-label', label);
        control.setAttribute('aria-describedby', detail.id);
    }
    manualMethod.setAttribute('aria-controls', 'projectionManualEditor');
    generationMethod.setAttribute('aria-controls', 'projectionGenerationViews');
    manualMethod.onclick = () => selectMethod('manual');
    generationMethod.onclick = () => selectMethod(savedProjectionMethod());
    methodChoices.append(manualMethod, generationMethod);
    const generationUnavailable = el('p', 'tp-method-unavailable');
    generationUnavailable.id = 'projectionGenerationUnavailable';
    generationUnavailable.hidden = true;
    const generationPanel = el('section', 'tp-generation-views');
    generationPanel.id = 'projectionGenerationViews';
    generationPanel.hidden = true;
    generationPanel.setAttribute('aria-labelledby', 'projectionGenerationViewsTitle');
    const generationTitle = el('h3', '', 'Pixel Match');
    generationTitle.id = 'projectionGenerationViewsTitle';
    const generationDescription = el('p', '', '');
    const generationInputs = el('div', 'tp-generation-inputs');
    const generationCurrent = el('figure', 'tp-generation-current');
    const sourceSnapshot = el('canvas', 'tp-generation-image');
    sourceSnapshot.width = sourceSnapshot.height = 256;
    sourceSnapshot.setAttribute('aria-hidden', 'true');
    const generationCaption = el('figcaption', '', 'Current model');
    generationCurrent.append(sourceSnapshot, generationCaption);
    const savedRoles = el('ul', 'tp-generation-roles');
    savedRoles.setAttribute('aria-label', 'Saved views');
    generationInputs.append(generationCurrent, savedRoles);
    const generationStatus = el('p', '', '');
    generationStatus.setAttribute('role', 'status');
    const generationFootnote = el('p', '', '');
    const retryGenerationFit = button('Retry alignment', 'rotate', 'secondary tp-generation-retry',
        () => fitGenerationReference());
    retryGenerationFit.hidden = true;
    generationPanel.append(
        generationTitle,
        generationDescription,
        generationInputs,
        generationStatus,
        retryGenerationFit,
        generationFootnote,
    );
    const manualEditor = el('div', 'tp-manual-editor');
    manualEditor.id = 'projectionManualEditor';
    const viewStrip = el('div', 'tp-view-strip');
    viewStrip.hidden = true;
    const viewChoices = el('div', 'tp-view-choices');
    viewChoices.setAttribute('role', 'group');
    viewChoices.setAttribute('aria-label', 'Projection views');
    const viewButtonsByIndex = ['One', 'Two'].map((suffix, index) => {
        const label = `View ${index + 1}`;
        const control = el('button', 'secondary tp-view-option', label);
        control.id = `projectionView${suffix}`;
        control.type = 'button';
        control.onclick = () => switchView(index);
        const status = el('span', 'tp-view-readiness');
        control.append(status);
        viewChoices.append(control);
        return { control, label, status };
    });
    const addViewButton = button('Add second view', 'plus', 'secondary tp-view-action', () => switchView(1));
    addViewButton.id = 'projectionAddView';
    const removeViewButton = button('Remove view 2', 'close', 'secondary tp-view-action', removeSecondView);
    removeViewButton.id = 'projectionRemoveView';
    const viewHelp = el('span', 'tp-view-help', 'Align each view separately. Both are blended in the texture copy.');
    viewStrip.append(viewChoices, addViewButton, removeViewButton, viewHelp);
    const reference = select([['', 'Choose a reference']], '');
    reference.id = 'projectionReference';
    const upload = input('file', '', { accept: 'image/png,image/jpeg,image/webp' });
    upload.id = 'projectionUpload';
    upload.hidden = true;
    const uploadButton = button('Upload image', 'import', 'secondary', () => upload.click());
    const uploadName = el('small', 'tp-upload-name');
    const uploadControls = el('div', 'tp-upload-control');
    uploadControls.append(upload, uploadButton, uploadName);
    const referenceFields = el('div', 'tp-reference-fields');
    reference.setAttribute('aria-label', 'Saved reference');
    referenceFields.append(
        el('span', 'tp-reference-label', 'Reference'),
        reference,
        uploadControls,
    );
    uploadButton.title = 'PNG, JPEG or WebP up to 25 MB';
    let mode = 'selection', shape = 'ellipse';
    const toolGroup = el('div', 'tp-dock-group');
    toolGroup.id = 'projectionTool';
    toolGroup.setAttribute('role', 'group');
    toolGroup.setAttribute('aria-label', 'Projection tools');
    const toolButtons = Object.fromEntries([
        ['selection', 'select', 'Select texture area'],
        ['placement', 'fit', 'Align reference overlay'],
        ['guides', 'edit', 'Match guide points'],
        ['orbit', 'rotate', 'Orbit model camera'],
        ['pan', 'compass', 'Pan zoomed views'],
    ].map(([value, icon, label]) => [value,
        dockButton(`projectionTool${titleCase(value)}`, icon, label, () => selectTool(value)),
    ]));
    const maskGroup = toolbarGroup('Selection shapes', 'projectionMask');
    const masks = [
        ['rectangle', 'Rectangle', 'select', 'Drag a rectangular selection'],
        ['ellipse', 'Ellipse', 'sphere', 'Drag an elliptical selection'],
        ['lasso', 'Lasso', 'edit', 'Draw a freehand selection'],
        ['polygon', 'Polygon', 'wire', 'Place points around the selection'],
    ];
    const maskButtons = Object.fromEntries(masks.map(([value, label, icon, title]) => [value,
        toolbarButton(maskGroup, `projectionMask${titleCase(value)}`, label, icon, () => {
            selectShape(value);
            closeToolbarPanel(true);
        }, title),
    ]));
    const shapeOptions = dockButton('projectionShapeOptions', 'chevron-right', 'Selection shape', () =>
        toggleToolbarPanel(shapePanel, shapeOptions));
    shapeOptions.classList.add('tp-shape-caret');
    const shapePanel = el('section', 'tp-popover tp-shape-popover');
    shapePanel.id = 'projectionShapeOptionsPanel';
    shapePanel.append(el('strong', 'tp-popover-title', 'Selection shape'), maskGroup);
    const undoButton = dockButton('projectionUndo', 'back', 'Undo', undo);
    const resetSelection = dockButton('projectionReset', 'restore', 'Reset selection', () => {
        remember();
        sourceRect = [...contentBounds];
        sourcePolygon = null;
        draft = [];
        shape = 'ellipse';
        changed();
    });
    const toolbar = el('div', 'tp-toolbar tool-pill');
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', 'Texture projection');
    const cameraPanel = el('section', 'tp-popover tp-camera-popover');
    cameraPanel.id = 'projectionCameraOptionsPanel';
    const presetGroup = toolbarGroup('Model camera views');
    const viewButtons = Object.fromEntries([...Object.keys(cameraViews), 'custom'].map((view) => [view,
        toolbarButton(presetGroup, `projectionView${titleCase(view)}`, titleCase(view), '', () => selectView(view)),
    ]));
    const lensGroup = toolbarGroup('Camera projection');
    const lensButtons = Object.fromEntries(['orthographic', 'perspective'].map((projection) => [projection,
        toolbarButton(lensGroup, `projectionCamera${titleCase(projection)}`, titleCase(projection), '',
            () => setCamera({ ...cameraPose, projection }, false)),
    ]));
    const cameraOptions = dockButton('projectionCameraOptions', 'camera', 'Projection camera', () =>
        toggleToolbarPanel(cameraPanel, cameraOptions));
    const cameraTools = el('div', 'tp-camera-tools');
    cameraTools.append(presetGroup, lensGroup);
    const cameraAdjustments = el('div', 'tp-camera-adjustments');
    cameraAdjustments.id = 'projectionCameraAdjustments';
    const cameraInputs = {};
    for (const [key, label, min, max] of [
        ['yaw', 'Yaw (°)', -180, 180], ['pitch', 'Pitch (°)', -90, 90], ['roll', 'Roll (°)', -180, 180],
        ['fov', 'Field of view (°)', 12, 90],
    ]) {
        const control = input('number', 0, { min, max, step: 0.1 });
        control.id = `projectionCamera${titleCase(key)}`;
        control.onchange = () => {
            if (control.value.trim() === '' || !Number.isFinite(Number(control.value))) {
                control.value = Number(cameraPose[key].toFixed(1));
                return;
            }
            const value = bound(Number(control.value), min, max);
            if (key === 'fov') setCamera({ ...cameraPose, fov: value }, false);
            else setCamera({ ...cameraPose, view: 'custom', [key]: value }, false);
        };
        cameraInputs[key] = control;
        const cameraField = field(label, control);
        cameraField.hidden = key === 'fov';
        cameraAdjustments.append(cameraField);
    }
    const autoAlignPanel = el('section', 'tp-auto-align');
    autoAlignPanel.setAttribute('role', 'group');
    autoAlignPanel.setAttribute('aria-label', 'Automatic camera alignment');
    const autoAlignButton = button('Auto Align', 'sparkles', 'secondary tp-tool', alignAutomatically);
    autoAlignButton.id = 'projectionAutoAlign';
    const cancelAlignmentButton = button('Cancel', 'close', 'secondary tp-tool', () => {
        cancelAutoAlign('Alignment canceled.');
        changed();
        autoAlignButton.focus({ preventScroll: true });
    });
    cancelAlignmentButton.id = 'projectionCancelAutoAlign';
    cancelAlignmentButton.setAttribute('aria-label', 'Cancel automatic alignment');
    const autoAlignActions = el('div', 'tp-auto-align-actions');
    autoAlignActions.append(autoAlignButton, cancelAlignmentButton);
    const autoAlignStatus = el('p', 'tp-auto-align-status', 'Match the camera to a cutout reference.');
    autoAlignStatus.id = 'projectionAutoAlignStatus';
    autoAlignStatus.setAttribute('role', 'status');
    autoAlignButton.setAttribute('aria-describedby', autoAlignStatus.id);
    const autoAlignProgress = el('progress');
    autoAlignProgress.id = 'projectionAutoAlignProgress';
    autoAlignProgress.setAttribute('aria-label', 'Matching camera views');
    autoAlignPanel.append(autoAlignActions, autoAlignStatus, autoAlignProgress);
    cameraPanel.append(el('strong', 'tp-popover-title', 'Projection camera'), autoAlignPanel, cameraTools, cameraAdjustments);
    const overlayButton = dockButton('projectionOverlayToggle', 'layers', 'Toggle reference overlay', () => {
        overlay.checked = !overlay.checked;
        changed();
    });
    const overlayOptions = dockButton('projectionOverlayOptions', 'sliders', 'Projection settings', () =>
        toggleToolbarPanel(adjustments, overlayOptions));
    const controlStrip = el('div', 'tp-control-strip');
    const divider = () => {
        const rule = el('i', 'tool-divider');
        rule.setAttribute('aria-hidden', 'true');
        return rule;
    };
    toolGroup.append(toolButtons.selection, shapeOptions, divider(), toolButtons.orbit, toolButtons.placement,
        toolButtons.guides, toolButtons.pan);
    toolbar.append(toolGroup, divider(), cameraOptions, overlayButton, overlayOptions, divider(), undoButton, resetSelection);
    for (const [popup, opener] of [[shapePanel, shapeOptions], [cameraPanel, cameraOptions]]) setupToolbarPanel(popup, opener);
    controlStrip.append(toolbar, shapePanel, cameraPanel);
    const linkPreviewZoom = input('checkbox', '');
    linkPreviewZoom.id = 'projectionLinkPreviewZoom';
    linkPreviewZoom.onchange = () => {
        if (linkPreviewZoom.checked) setPreviewZoom('target');
    };
    const linkZoomLabel = el('label', 'check tp-link-zoom');
    linkZoomLabel.append(linkPreviewZoom, el('span', '', 'Link view zoom'));
    controlStrip.append(linkZoomLabel);
    const instruction = el('p', 'tp-instruction');
    instruction.setAttribute('role', 'status');
    const columns = el('div', 'tp-columns'),
        sides = {};
    for (const [key, title] of [
        ['source', 'Reference image'],
        ['target', 'Model preview'],
    ]) {
        const card = el('section', 'tp-card'),
            viewport = el('div', 'tp-viewport'),
            canvas = el('canvas', 'tp-canvas');
        const heading = el('h3', '', title);
        const placeholder = el('p', 'tp-preview-placeholder');
        placeholder.setAttribute('role', 'status');
        canvas.width = canvas.height = 1024;
        canvas.tabIndex = 0;
        canvas.id = `projection${key === 'source' ? 'Source' : 'Target'}Canvas`;
        canvas.setAttribute(
            'aria-label',
            key === 'source'
                ? 'Reference image. Draw a selection or place a guide point.'
                : 'Model preview. Adjust the camera, move the overlay, or place a matching guide point.',
        );
        viewport.append(canvas, placeholder);
        const zoom = input('range', 1, { min: 1, max: 6, step: 0.25 });
        zoom.id = `projection${key === 'source' ? 'Source' : 'Target'}Zoom`;
        const zoomValue = el('span', 'tp-zoom-value', '100%'),
            panButton = dockButton('', 'compass', `Pan ${key === 'source' ? 'reference' : 'model'} preview`);
        panButton.setAttribute('aria-pressed', 'false');
        const fitButton = dockButton('', 'fit', `Fit ${key === 'source' ? 'reference' : 'model'} preview`, () => {
            zoom.value = 1;
            setPreviewZoom(key);
            viewport.scrollLeft = viewport.scrollTop = 0;
        });
        const controls = el('div', 'tp-view-controls');
        controls.append(field('Zoom', zoom), zoomValue, panButton, fitButton);
        const notice = el('p', 'tp-preview-note');
        const header = el('div', 'tp-pane-header');
        header.append(heading, controls);
        card.append(header, viewport, notice);
        columns.append(card);
        sides[key] = {
            image: null,
            points: [],
            canvas,
            viewport,
            zoom,
            zoomValue,
            panButton,
            heading,
            notice,
            placeholder,
            error: '',
            loading: false,
            panMode: false,
        };
        zoom.oninput = () => setPreviewZoom(key);
        panButton.onclick = () => {
            sides[key].panMode = !sides[key].panMode;
            changed();
        };
        canvas.onpointerdown = (event) => pointerDown(key, event);
        canvas.onpointermove = (event) => pointerMove(key, event);
        canvas.onpointerup = (event) => pointerUp(key, event);
        canvas.onpointercancel = () => cancelGesture();
        canvas.ondblclick = (event) => {
            if (key === 'source' && isDrawingMode() && shape === 'polygon') {
                event.preventDefault();
                closePolygon();
            }
        };
        canvas.onkeydown = (event) => {
            if (key === 'target' && mode === 'orbit' && event.key.startsWith('Arrow')) {
                event.preventDefault();
                const step = event.shiftKey ? 15 : 3;
                const yaw = (event.key === 'ArrowRight' ? step : 0) - (event.key === 'ArrowLeft' ? step : 0);
                const pitch = (event.key === 'ArrowUp' ? step : 0) - (event.key === 'ArrowDown' ? step : 0);
                rotateCamera(yaw, pitch);
            }
            if (event.key === 'Enter' && key === 'source') {
                event.preventDefault();
                closePolygon();
            }
            if (event.key === 'Escape' && draft.length) {
                event.preventDefault();
                event.stopPropagation();
                draft = [];
                changed();
            }
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
                event.preventDefault();
                undo();
            }
        };
    }
    const closePolygonButton = button('Close polygon', 'check', 'secondary', () => closePolygon());
    closePolygonButton.id = 'projectionClosePolygon';
    const undoPointButton = button('Undo point', 'back', 'secondary', () => {
        draft.pop();
        changed();
    });
    undoPointButton.id = 'projectionUndoPoint';
    const polygonControls = el('div', 'tp-polygon-controls');
    polygonControls.append(
        closePolygonButton,
        undoPointButton,
        el('span', 'hint', 'Click points around the edge, then close the polygon. Enter also closes it.'),
    );
    const placementPanel = el('div', 'tp-placement');
    const sizeControls = ['Width', 'Height'].map((axis, index) => {
        const control = input('range', 1, { min: placementScaleLimits[0], max: placementScaleLimits[1], step: 0.01 });
        control.id = index ? 'projectionPlacementHeight' : 'projectionPlacementScale';
        control.setAttribute('aria-label', `Overlay ${axis.toLowerCase()}`);
        const value = el('output', 'tp-zoom-value', '100%');
        value.setAttribute('for', control.id);
        const wrapper = field(`Overlay ${axis.toLowerCase()}`, control);
        wrapper.classList.add('tp-size-control');
        wrapper.append(value);
        return {control, value, wrapper};
    });
    const lockProportions = input('checkbox', '');
    lockProportions.id = 'projectionLockProportions';
    lockProportions.checked = true;
    const proportionsLabel = el('label', 'check tp-lock-proportions');
    proportionsLabel.append(lockProportions, el('span', '', 'Lock proportions'));
    const nudge = el('div', 'tp-nudges');
    for (const [label, x, y] of [
        ['Left', -0.005, 0],
        ['Up', 0, -0.005],
        ['Down', 0, 0.005],
        ['Right', 0.005, 0],
    ]) {
        const b = button(label, '', 'secondary', () => {
            remember();
            placement[2] += x;
            placement[5] += y;
            changed();
        });
        nudge.append(b);
    }
    const resetPlacement = button('Reset alignment', 'rotate', 'secondary', () => {
        remember();
        useGuides.checked = false;
        sides.source.points = [];
        sides.target.points = [];
        activePair = 0;
        initialisePlacement();
        mode = 'placement';
        changed();
    });
    placementPanel.append(
        ...sizeControls.map(({wrapper}) => wrapper),
        proportionsLabel,
        nudge,
        resetPlacement,
    );
    const useGuides = input('checkbox', '');
    useGuides.id = 'projectionAnchors';
    const guideToggle = el('label', 'check');
    guideToggle.append(useGuides, el('span', '', 'Use guide alignment'));
    const guidePanel = el('section', 'tp-guides'),
        pairList = el('div', 'tp-pairs');
    const pairButtons = [0, 1, 2, 3].map((index) => {
        const b = button(`Point ${index + 1}`, '', 'secondary', () => {
            activePair = index;
            mode = 'guides';
            changed();
        });
        pairList.append(b);
        return b;
    });
    const pairTitle = el('strong', 'tp-active-pair');
    const pairCoordinates = el('div', 'tp-pair-coordinates'),
        pointInputs = {};
    for (const key of ['source', 'target'])
        for (const axis of [0, 1]) {
            const control = input('number', '', { min: 0, max: 100, step: 0.1 });
            control.id = `projection${key}${axis}Guide`;
            pointInputs[`${key}${axis}`] = control;
            control.onchange = () => {
                const x = pointInputs[`${key}0`].value,
                    y = pointInputs[`${key}1`].value;
                if (x === '' || y === '') return;
                remember();
                sides[key].points[activePair] = [clamp(Number(x) / 100), clamp(Number(y) / 100)];
                changed();
            };
            pairCoordinates.append(
                field(`${key === 'source' ? 'Image' : 'Model'} ${axis ? 'Y' : 'X'} %`, control),
            );
        }
    const clearPair = button('Clear this pair', 'close', 'secondary', () => {
        remember();
        sides.source.points[activePair] = null;
        sides.target.points[activePair] = null;
        changed();
    });
    const resetGuides = button('Reset all guides', 'rotate', 'secondary', () => {
        remember();
        sides.source.points = [];
        sides.target.points = [];
        activePair = 0;
        changed();
    });
    const pairActions = el('div', 'tp-pair-actions');
    pairActions.append(clearPair, resetGuides);
    const guideTop = el('div', 'tp-guide-top');
    guideTop.append(pairList, guideToggle, pairTitle);
    guidePanel.append(pairCoordinates, pairActions);
    const overlay = input('checkbox', '');
    overlay.checked = true;
    overlay.id = 'projectionOverlay';
    const overlayLabel = el('label', 'check');
    overlayLabel.append(overlay, el('span', '', 'Show reference overlay'));
    const opacity = input('range', 1, { min: 0.1, max: 1, step: 0.05 });
    opacity.id = 'projectionOpacity';
    const feather = input('range', 0.06, { min: 0, max: 0.25, step: 0.01 });
    feather.id = 'projectionFeather';
    const overlayOpacity = input('range', 0.5, { min: 0.1, max: 0.9, step: 0.05 });
    overlayOpacity.id = 'projectionOverlayOpacity';
    const overlayControls = el('div', 'tp-overlay-controls');
    overlayControls.append(overlayLabel, field('Overlay opacity', overlayOpacity));
    const adjustments = el('section', 'tp-popover tp-adjustments');
    adjustments.id = 'projectionOverlayOptionsPanel';
    const adjustmentFields = el('div', 'tp-adjustment-fields');
    const rangeOutputs = [[overlayOpacity, overlayControls.lastChild], [opacity, field('Texture strength', opacity)],
        [feather, field('Soft edge', feather)]].map(([control, wrapper]) => {
        const value = el('output', 'tp-range-value');
        value.htmlFor = control.id;
        wrapper.firstChild.append(value);
        return { control, wrapper, value };
    });
    adjustmentFields.append(overlayControls, rangeOutputs[1].wrapper, rangeOutputs[2].wrapper);
    adjustments.append(el('strong', 'tp-popover-title', 'Projection settings'), adjustmentFields);
    setupToolbarPanel(adjustments, overlayOptions);
    controlStrip.append(adjustments);
    const cropDetails = el('details', 'tp-crop-details');
    cropDetails.append(el('summary', '', 'Selection coordinates'));
    const cropFields = el('div', 'tp-coordinates'),
        rectInputs = [];
    for (const [i, label] of ['Left %', 'Top %', 'Right %', 'Bottom %'].entries()) {
        const control = input('number', 0, { min: 0, max: 100, step: 0.1 });
        rectInputs.push(control);
        control.onchange = () => {
            remember();
            sourceRect[i] = clamp(Number(control.value) / 100);
            sourcePolygon = null;
            if (['polygon', 'lasso'].includes(shape)) shape = 'rectangle';
            changed();
        };
        cropFields.append(field(label, control));
    }
    cropDetails.append(cropFields);
    adjustments.append(cropDetails);
    const alignmentStatus = el('p', 'tp-alignment-status');
    alignmentStatus.setAttribute('role', 'status');
    manualEditor.append(
        viewStrip,
        referenceFields,
        controlStrip,
        instruction,
        polygonControls,
        placementPanel,
        guideTop,
        columns,
        guidePanel,
        alignmentStatus,
    );
    ui.body.append(methodChoices, generationUnavailable, generationPanel, manualEditor);
    const submit = button('Create texture copy', 'image', 'primary');
    submit.id = 'confirmTextureProjection';
    const submitLabel = submit.lastChild;
    const viewStatus = el('span', 'tp-projection-status');
    const actions = el('div', 'tp-action-pill tool-pill');
    actions.append(button('Cancel', 'close', 'secondary', () => ui.close()), submit);
    ui.foot.append(viewStatus, actions);

    let base = null,
        board = null,
        preview = null,
        cameraRecipe = null,
        contentBounds = [0, 0, 1, 1];
    let cameraPose = defaultCamera(),
        cameraRequest = 0,
        cameraFit = false;
    let panelOpen = null;
    let autoAlignRun = null;
    let sourceRect = [0, 0, 1, 1],
        sourcePolygon = null,
        placement = [1, 0, 0, 0, 1, 0],
        initialPlacement = [...placement];
    let selectedReference = null,
        opening = 0,
        imageVersion = 0,
        uploadUrl = null,
        pending = false,
        uploading = false;
    let activePair = 0,
        draft = [],
        hover = null,
        gesture = null,
        history = [],
        scaleStart = null;
    let inactiveView = null;
    let activeViewIndex = 0;
    let projectionMethod = 'manual';
    let manualViewports = [];
    let manualNotice = '';
    let generationReferenceRun = null;
    let generationReferenceRecipe = null;

    function methodSwitchAvailable() {
        return !pending && !uploading && !autoAlignRun && !gesture && !cameraRequest
            && !Object.values(sides).some((side) => side.loading);
    }
    function selectMethod(method) {
        if (!method || method === projectionMethod || !savedProjectionMethod() || !methodSwitchAvailable()) return;
        closeToolbarPanel();
        if (method !== 'manual') {
            manualViewports = Object.values(sides).map(({ viewport }) => [viewport.scrollLeft, viewport.scrollTop]);
            manualNotice = ui.notice.textContent;
            if (method === 'generation_views') drawGenerationSnapshot(sides.target.image, 'Current model');
        } else {
            generationReferenceRun?.abort();
            generationReferenceRun = null;
            renderCamera(false);
        }
        projectionMethod = method;
        ui.notice.textContent = method === 'manual' ? manualNotice : '';
        changed();
        if (method === 'generation_reference' && !generationReferenceRecipe) void fitGenerationReference();
        if (method === 'manual') {
            Object.keys(sides).forEach((key, index) => {
                applyZoom(key);
                const [left, top] = manualViewports[index];
                sides[key].viewport.scrollLeft = left;
                sides[key].viewport.scrollTop = top;
            });
        }
    }
    function savedProjectionMethod() {
        if (board?.generation_views?.supported) return 'generation_views';
        if (board?.generation_reference?.supported) return 'generation_reference';
        return null;
    }
    function drawGenerationSnapshot(image, caption) {
        generationCurrent.hidden = !image;
        if (!image) return;
        generationCaption.textContent = caption;
        const snapshot = sourceSnapshot.getContext('2d');
        snapshot.clearRect(0, 0, sourceSnapshot.width, sourceSnapshot.height);
        snapshot.drawImage(image, 0, 0, sourceSnapshot.width, sourceSnapshot.height);
    }
    async function fitGenerationReference() {
        const descriptor = board?.references?.find(({id}) => id === board?.generation_reference?.reference);
        if (!descriptor || !preview || generationReferenceRun || projectionMethod !== 'generation_reference') return;
        const run = new AbortController();
        generationReferenceRun = run;
        generationReferenceRecipe = null;
        generationCurrent.hidden = true;
        generationStatus.textContent = 'Matching the saved reference…';
        changed();
        try {
            const image = await loadImage(descriptor.url);
            run.signal.throwIfAborted();
            const match = await matchTextureProjectionCamera(
                referenceSilhouetteFor(image), preview.sample, cameraPose, {signal: run.signal},
            );
            if (generationReferenceRun !== run || !ui.element.open) return;
            if (match.reason === 'time-limit') throw new Error('The model took too long to match. Try again or use Align references.');
            if (match.reason === 'low-overlap') throw new Error('No view matches this image closely. Use Align references to place it.');
            const fitted = preview.render(match.pose);
            const source = alphaBounds(image, 384);
            const target = alphaBounds(fitted.image, 384);
            if (!validRect(source) || !validRect(target)) throw new Error('The saved image or model view was empty.');
            drawGenerationSnapshot(fitted.image, 'Fitted model view');
            generationReferenceRecipe = {
                method: 'generation_reference', linked_selection: true, camera: fitted.recipe,
                source_rect: source, target_rect: target, mask_shape: 'rectangle', opacity: 1, feather: 0.04,
            };
            generationStatus.textContent = match.ambiguous
                ? 'Several angles fit the image. Check the fitted view before creating the copy.'
                : 'Check the fitted view before creating the texture copy.';
        } catch (error) {
            if (generationReferenceRun === run) generationStatus.textContent = error.message;
        } finally {
            if (generationReferenceRun === run) {
                generationReferenceRun = null;
                changed();
            }
        }
    }
    function setupToolbarPanel(panel, opener) {
        panel.hidden = true;
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-label', opener.getAttribute('aria-label'));
        opener.setAttribute('aria-controls', panel.id);
        opener.setAttribute('aria-haspopup', 'dialog');
        opener.setAttribute('aria-expanded', 'false');
    }
    function toggleToolbarPanel(panel, opener) {
        const wasOpen = panelOpen?.panel === panel;
        closeToolbarPanel(wasOpen);
        if (wasOpen) return;
        panelOpen = { panel, opener };
        panel.hidden = false;
        opener.setAttribute('aria-expanded', 'true');
        panel.querySelector('button[aria-pressed="true"], button:not(:disabled), input:not(:disabled)')
            ?.focus({ preventScroll: true });
    }
    function closeToolbarPanel(returnFocus = false) {
        if (!panelOpen) return;
        panelOpen.panel.hidden = true;
        panelOpen.opener.setAttribute('aria-expanded', 'false');
        if (returnFocus) panelOpen.opener.focus({ preventScroll: true });
        panelOpen = null;
    }
    ui.element.addEventListener('pointerdown', (event) => {
        if (panelOpen && !panelOpen.panel.contains(event.target) && !panelOpen.opener.contains(event.target)) {
            closeToolbarPanel();
        }
    });
    ui.element.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !panelOpen) return;
        event.preventDefault();
        event.stopPropagation();
        closeToolbarPanel(true);
    }, true);
    toolbar.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        const enabled = [...toolbar.querySelectorAll('button:not(:disabled)')];
        const index = enabled.indexOf(event.target);
        if (index < 0) return;
        event.preventDefault();
        const direction = event.key === 'ArrowRight' ? 1 : -1;
        enabled[(index + direction + enabled.length) % enabled.length]?.focus();
    });
    const snapshot = () => ({
        sourceRect: [...sourceRect],
        sourcePolygon: sourcePolygon?.map((p) => [...p]),
        placement: [...placement],
        initialPlacement: [...initialPlacement],
        cameraPose: { ...cameraPose },
        mode,
        sourcePoints: sides.source.points.map((p) => p && [...p]),
        targetPoints: sides.target.points.map((p) => p && [...p]),
        shape: shape,
        useGuides: useGuides.checked,
        activePair,
    });
    function restoreSnapshot(saved) {
        ({ sourceRect, sourcePolygon, placement, initialPlacement, cameraPose, mode, activePair, shape } = saved);
        sides.source.points = saved.sourcePoints;
        sides.target.points = saved.targetPoints;
        useGuides.checked = saved.useGuides;
    }
    function viewSwitchAvailable() {
        return !!preview && !pending && !uploading && !sides.source.loading && !gesture && !cameraRequest;
    }
    function saveView() {
        const { bounds, issue } = inspectView();
        return {
            alignment: snapshot(),
            contentBounds,
            history,
            draft,
            selectedReference,
            referenceChoice: reference.value,
            sourceImage: sides.source.image,
            sourceError: sides.source.error,
            uploadedName: uploadName.textContent,
            uploadUrl,
            sourceControls: [overlay.checked, overlayOpacity.value, opacity.value, feather.value],
            lockProportions: lockProportions.checked,
            linkPreviewZoom: linkPreviewZoom.checked,
            viewports: Object.values(sides).map(({ zoom, viewport, panMode }) => ({
                zoom: zoom.value, left: viewport.scrollLeft, top: viewport.scrollTop, panMode,
            })),
            autoAlignStatus: { text: autoAlignStatus.textContent, state: autoAlignPanel.dataset.state },
            recipe: issue ? null : projectionRecipe(bounds),
            readiness: readyLabel(issue),
        };
    }
    function activateView(saved) {
        closeToolbarPanel();
        ui.notice.textContent = '';
        resetView(activeViewIndex === 1 && board?.views?.includes('back') ? 'back' : 'front');
        if (saved) {
            restoreSnapshot(saved.alignment);
            ({ contentBounds, history, draft, selectedReference, uploadUrl } = saved);
            reference.value = saved.referenceChoice;
            uploadName.textContent = saved.uploadedName;
            sides.source.image = saved.sourceImage;
            sides.source.error = saved.sourceError;
            if (saved.sourceImage) {
                const { naturalWidth, naturalHeight } = saved.sourceImage;
                sides.source.canvas.height = Math.round(1024 * naturalHeight / naturalWidth);
            }
            [overlay.checked, overlayOpacity.value, opacity.value, feather.value] = saved.sourceControls;
            lockProportions.checked = saved.lockProportions;
            linkPreviewZoom.checked = saved.linkPreviewZoom;
            setAutoAlignStatus(saved.autoAlignStatus.text, saved.autoAlignStatus.state);
        }
        renderCamera(false);
        const viewports = Object.entries(sides);
        viewports.forEach(([, side], index) => {
            const state = saved?.viewports[index];
            if (!state) return;
            side.zoom.value = state.zoom;
            side.panMode = state.panMode;
        });
        changed();
        viewports.forEach(([key, side], index) => {
            const state = saved?.viewports[index];
            applyZoom(key);
            if (state) {
                side.viewport.scrollLeft = state.left;
                side.viewport.scrollTop = state.top;
            }
        });
        viewButtonsByIndex[activeViewIndex].control.focus({ preventScroll: true });
    }
    function switchView(index) {
        if (index === activeViewIndex || board?.max_views !== 2 || !viewSwitchAvailable()) return;
        if (autoAlignRun) cancelAutoAlign('Alignment canceled.');
        const saved = inactiveView;
        inactiveView = saveView();
        activeViewIndex = index;
        activateView(saved);
    }
    function removeSecondView() {
        if (activeViewIndex !== 1 || !viewSwitchAvailable()) return;
        if (autoAlignRun) cancelAutoAlign();
        if (uploadUrl) URL.revokeObjectURL(uploadUrl);
        const first = inactiveView;
        inactiveView = null;
        activeViewIndex = 0;
        activateView(first);
    }
    function remember() {
        history.push(snapshot());
        if (history.length > 40) history.shift();
    }
    function undo() {
        if (autoAlignRun) return;
        if (autoAlignPanel.dataset.state === 'success') ui.notice.textContent = '';
        setAutoAlignStatus();
        if (draft.length) {
            draft.pop();
            changed();
            return;
        }
        const previous = history.pop();
        if (!previous || pending) return;
        const cameraChanged = !sameCamera(previous.cameraPose, cameraPose);
        restoreSnapshot(previous);
        if (cameraChanged) {
            cameraFit = false;
            queueCamera();
        }
        else changed();
    }
    function sameCamera(a, b) {
        return Object.keys(a).every((key) => a[key] === b[key]);
    }
    function supportsCustom() {
        return board?.views?.includes('custom') === true;
    }
    function supportsPerspective() {
        return board?.camera_projections?.includes('perspective') === true;
    }
    function setAutoAlignStatus(message = 'Match the camera to a cutout reference.', state = 'idle') {
        autoAlignStatus.textContent = message;
        autoAlignPanel.dataset.state = state;
    }
    function cancelAutoAlign(message) {
        autoAlignRun?.abort();
        autoAlignRun = null;
        setAutoAlignStatus(message);
    }
    async function alignAutomatically() {
        if (autoAlignButton.disabled || !preview || !sides.source.image) return;
        let referenceSilhouette;
        try {
            referenceSilhouette = referenceSilhouetteFor(sides.source.image);
        } catch (error) {
            setAutoAlignStatus(error.message, 'error');
            return;
        }
        const run = new AbortController();
        autoAlignRun = run;
        autoAlignProgress.removeAttribute('value');
        setAutoAlignStatus('Matching camera views…', 'running');
        changed();
        cancelAlignmentButton.focus({ preventScroll: true });
        try {
            const result = await matchTextureProjectionCamera(referenceSilhouette, preview.sample, cameraPose, {
                signal: run.signal,
                onProgress({ tested, total }) {
                    autoAlignProgress.max = total;
                    autoAlignProgress.value = tested;
                },
            });
            if (autoAlignRun !== run || !ui.element.open) return;
            autoAlignRun = null;
            if (result.applied) {
                setCamera(result.pose, true);
                setAutoAlignStatus('Camera matched. Review the overlay.', 'success');
                closeToolbarPanel(true);
                ui.notice.textContent = 'Camera matched. Review the overlay before creating a texture copy.';
            } else if (result.ambiguous) {
                setAutoAlignStatus('Several angles fit this reference. Use Orbit to choose a view.', 'error');
            } else if (result.reason === 'close') {
                setAutoAlignStatus('The current angle already fits the reference.', 'success');
            } else if (result.reason === 'time-limit') {
                setAutoAlignStatus('The model took too long to match. Set the angle with Orbit.', 'error');
            } else {
                setAutoAlignStatus('No view matches this cutout closely. Check the reference or use Orbit.', 'error');
            }
        } catch (error) {
            if (autoAlignRun !== run) return;
            autoAlignRun = null;
            setAutoAlignStatus(error.message, 'error');
        } finally {
            if (ui.element.open && autoAlignRun === null) {
                changed();
                if (!cameraPanel.hidden && document.activeElement === cancelAlignmentButton) {
                    autoAlignButton.focus({ preventScroll: true });
                }
            }
        }
    }
    function selectTool(value) {
        const enableGuides = value === 'guides' && !useGuides.checked;
        if (enableGuides) remember();
        if (mode !== value) {
            mode = value;
            sides.source.panMode = sides.target.panMode = false;
        }
        if (enableGuides) useGuides.checked = true;
        changed();
    }
    function selectShape(value) {
        if (shape !== value) {
            remember();
            shape = value;
            sourcePolygon = null;
            draft = [];
        }
        selectTool('selection');
    }
    function selectView(view) {
        if (view === 'custom') {
            selectTool('orbit');
            setCamera({ ...cameraPose, view: 'custom' });
            return;
        }
        if (mode === 'orbit') selectTool('selection');
        const [yaw, pitch] = cameraViews[view];
        setCamera({ ...cameraPose, view, yaw, pitch, roll: 0 }, true);
    }
    function setCamera(next, fit = false, save = true) {
        if (!preview || pending || sameCamera(cameraPose, next)) return;
        if (next.view === 'custom' && !supportsCustom()) return;
        if (next.projection === 'perspective' && !supportsPerspective()) return;
        if (autoAlignRun) cancelAutoAlign('Alignment canceled.');
        else if (autoAlignPanel.dataset.state === 'success') {
            setAutoAlignStatus();
            ui.notice.textContent = '';
        }
        if (save) remember();
        if (useGuides.checked || sides.target.points.some(Boolean)) {
            const transform = activeTransform();
            if (transform && useGuides.checked && validRect(sourceRect)) {
                // Free placement bakes through source and target rectangles after guides are cleared.
                placement = fitOverlayRect(sourceRect, targetRect(transform));
            }
            useGuides.checked = false;
            if (mode === 'guides') mode = 'placement';
            sides.target.points = [];
            activePair = 0;
        }
        cameraPose = next;
        queueCamera(fit);
    }
    function rotateCamera(yaw, pitch, start = cameraPose, save = true) {
        if (!yaw && !pitch) return;
        setCamera({ ...start, view: 'custom', yaw: wrapAngle(start.yaw + yaw),
            pitch: bound(start.pitch + pitch, -90, 90) }, false, save);
    }
    function queueCamera(fit = false) {
        cameraFit ||= fit;
        cameraRecipe = null;
        submit.disabled = true;
        if (cameraRequest) return;
        cameraRequest = requestAnimationFrame(() => {
            cameraRequest = 0;
            if (!preview || !ui.element.open) return;
            const firstRender = !sides.target.image;
            renderCamera(cameraFit || firstRender);
            if (firstRender) applyZoom('target');
            cameraFit = false;
            changed();
        });
    }
    function renderCamera(fit) {
        try {
            const rendered = preview.render(cameraPose);
            sides.target.image = rendered.image;
            cameraRecipe = rendered.recipe;
            if (fit) initialisePlacement();
        } catch (error) {
            ui.notice.textContent = error.message;
        }
    }
    function selectionOutline() {
        if (sourcePolygon?.length) return sourcePolygon;
        const [x0, y0, x1, y1] = sourceRect;
        if (shape !== 'ellipse')
            return [
                [x0, y0],
                [x1, y0],
                [x1, y1],
                [x0, y1],
            ];
        return Array.from({ length: 72 }, (_, i) => {
            const a = (i * Math.PI) / 36;
            return [
                (x0 + x1) / 2 + ((x1 - x0) / 2) * Math.cos(a),
                (y0 + y1) / 2 + ((y1 - y0) / 2) * Math.sin(a),
            ];
        });
    }
    function activeTransform() {
        return useGuides.checked
            ? pointsReady(sides.source.points) && pointsReady(sides.target.points)
                ? fitLinkedAffine(sides.source.points, sides.target.points)
                : null
            : placement;
    }
    function targetRect(transform) {
        return polygonBounds(selectionOutline().map((point) => mapPoint(transform, point)));
    }
    const relativeSize = (matrix) => [matrix[0] / initialPlacement[0], matrix[4] / initialPlacement[4]];
    const pointInRect = ([x0, y0, x1, y1], x, y) => [x0 + (x1 - x0) * x, y0 + (y1 - y0) * y];
    function resizeOverlay(matrix, anchor, factors, locked) {
        const [min, max] = placementScaleLimits;
        const size = relativeSize(matrix);
        if (locked) {
            const wanted = Math.abs(factors[0] - 1) >= Math.abs(factors[1] - 1) ? factors[0] : factors[1];
            const factor = bound(wanted, min / Math.min(...size), max / Math.max(...size));
            return scaleOverlay(matrix, anchor, [factor, factor]);
        }
        return scaleOverlay(matrix, anchor, factors.map((factor, axis) => bound(factor, min / size[axis], max / size[axis])));
    }
    function overlayHandles() {
        if (mode !== 'placement' || useGuides.checked || !sides.source.image || !sides.target.image || !validRect(sourceRect)) return [];
        const rect = targetRect(placement);
        return overlayHandleOffsets.map(([x, y, cursor]) => ({point: pointInRect(rect, x, y), x, y, cursor}));
    }
    function overlayHandleAt(point, canvas) {
        let nearest = 13;
        let match = null;
        for (const handle of overlayHandles()) {
            const distance = Math.hypot((point[0] - handle.point[0]) * canvas.clientWidth,
                (point[1] - handle.point[1]) * canvas.clientHeight);
            if (distance < nearest) {
                nearest = distance;
                match = handle;
            }
        }
        return match;
    }
    function initialisePlacement() {
        if (!sides.source.image || !sides.target.image) return;
        const b = contentBounds,
            t = alphaBounds(sides.target.image, 384),
            aspect = sides.source.image.naturalWidth / sides.source.image.naturalHeight;
        const scale = Math.min((t[2] - t[0]) / ((b[2] - b[0]) * aspect), (t[3] - t[1]) / (b[3] - b[1]));
        placement = [
            scale * aspect,
            0,
            (t[0] + t[2]) / 2 - (scale * aspect * (b[0] + b[2])) / 2,
            0,
            scale,
            (t[1] + t[3]) / 2 - (scale * (b[1] + b[3])) / 2,
        ];
        initialPlacement = [...placement];
    }
    function alphaBounds(image, size = 1024) {
        const width = image.naturalWidth || image.width;
        const height = image.naturalHeight || image.height;
        const canvas = document.createElement('canvas'),
            scale = Math.min(1, size / Math.max(width, height));
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
        try {
            const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
            let x0 = canvas.width,
                y0 = canvas.height,
                x1 = 0,
                y1 = 0,
                transparent = false;
            for (let y = 0; y < canvas.height; y++)
                for (let x = 0; x < canvas.width; x++) {
                    const alpha = pixels[(y * canvas.width + x) * 4 + 3];
                    if (alpha < 250) transparent = true;
                    if (alpha > 16) {
                        x0 = Math.min(x0, x);
                        y0 = Math.min(y0, y);
                        x1 = Math.max(x1, x + 1);
                        y1 = Math.max(y1, y + 1);
                    }
                }
            return transparent && x1 > x0 && y1 > y0
                ? [x0 / canvas.width, y0 / canvas.height, x1 / canvas.width, y1 / canvas.height]
                : [0, 0, 1, 1];
        } catch {
            return [0, 0, 1, 1];
        } finally {
            canvas.width = canvas.height = 1;
        }
    }
    function path(ctx, points, w, h, close = true) {
        ctx.beginPath();
        if (!points.length) return;
        ctx.moveTo(points[0][0] * w, points[0][1] * h);
        for (const p of points.slice(1)) ctx.lineTo(p[0] * w, p[1] * h);
        if (close) ctx.closePath();
    }
    function draw(key) {
        const side = sides[key],
            canvas = side.canvas,
            ctx = canvas.getContext('2d'),
            w = canvas.width,
            h = canvas.height;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = '#171e1a';
        ctx.fillRect(0, 0, w, h);
        if (!side.image) return;
        ctx.drawImage(side.image, 0, 0, w, h);
        const transform = activeTransform(),
            outline = selectionOutline(),
            shown = key === 'source' ? outline : transform ? outline.map((p) => mapPoint(transform, p)) : [];
        if (key === 'target' && transform && overlay.checked && sides.source.image && validRect(sourceRect)) {
            ctx.save();
            path(ctx, shown, w, h);
            ctx.clip();
            ctx.globalAlpha = Number(overlayOpacity.value);
            ctx.setTransform(
                transform[0] * w,
                transform[3] * h,
                transform[1] * w,
                transform[4] * h,
                transform[2] * w,
                transform[5] * h,
            );
            ctx.drawImage(sides.source.image, 0, 0, 1, 1);
            ctx.restore();
        }
        const ratio = w / (canvas.clientWidth || w);
        ctx.lineWidth = 2 * ratio;
        ctx.strokeStyle = '#d5ff98';
        ctx.setLineDash([7 * ratio, 4 * ratio]);
        path(ctx, shown, w, h);
        ctx.stroke();
        ctx.setLineDash([]);
        if (key === 'target') {
            ctx.fillStyle = '#d5ff98';
            ctx.strokeStyle = '#172015';
            const size = 9 * ratio;
            for (const {point: [x, y]} of overlayHandles()) {
                ctx.fillRect(x * w - size / 2, y * h - size / 2, size, size);
                ctx.strokeRect(x * w - size / 2, y * h - size / 2, size, size);
            }
        }
        if (key === 'source' && draft.length) {
            ctx.strokeStyle = '#ffca81';
            path(ctx, [...draft, ...(hover && shape === 'polygon' ? [hover] : [])], w, h, false);
            ctx.stroke();
            for (const p of draft) {
                ctx.beginPath();
                ctx.arc(p[0] * w, p[1] * h, 3 * ratio, 0, Math.PI * 2);
                ctx.fillStyle = '#ffca81';
                ctx.fill();
            }
        }
        if (useGuides.checked)
            for (const [index, p] of side.points.entries())
                if (p) {
                    ctx.fillStyle = index === activePair ? '#ffca81' : '#c4f58a';
                    ctx.strokeStyle = '#172015';
                    ctx.lineWidth = 2 * ratio;
                    ctx.beginPath();
                    ctx.arc(p[0] * w, p[1] * h, 9 * ratio, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.stroke();
                    ctx.fillStyle = '#172015';
                    ctx.font = `bold ${11 * ratio}px sans-serif`;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillText(String(index + 1), p[0] * w, p[1] * h);
                    ctx.font = `${11 * ratio}px sans-serif`;
                }
    }
    function inspectView() {
        const ready = !!(sides.source.image && sides.target.image),
            transform = ready ? activeTransform() : null;
        const polygonValid =
            !['polygon', 'lasso'].includes(shape) || (sourcePolygon && validatePolygon(sourcePolygon));
        const bounds = transform && targetRect(transform),
            fits =
                bounds &&
                bounds.every(Number.isFinite) &&
                bounds[0] >= -1e-8 &&
                bounds[1] >= -1e-8 &&
                bounds[2] <= 1 + 1e-8 &&
                bounds[3] <= 1 + 1e-8;
        const issue = !selectedReference || !ready || !cameraRecipe ? 'Choose a reference and prepare its model view.'
            : draft.length ? 'Finish the current outline before creating the copy.'
            : !polygonValid ? 'Draw a closed outline with at least three points and no crossing edges.'
            : !validRect(sourceRect) ? 'Select an area with a non-zero width and height.'
            : !transform ? (pointsReady(sides.source.points) && pointsReady(sides.target.points)
                ? 'The guide pairs do not form a consistent alignment. Check the matching features in both views.'
                : 'Place all four matching pairs. The selection can be anywhere relative to the guides.')
            : !fits ? 'The linked selection extends beyond the model view. Move it inward, reduce its scale, or correct the guide pairs.'
            : '';
        return { ready, bounds, issue };
    }
    function readyLabel(issue) {
        return !selectedReference ? 'Needs image' : issue ? 'Needs alignment' : 'Ready';
    }
    function changed() {
        const generationAvailable = savedProjectionMethod() !== null;
        const useGeneration = generationAvailable && projectionMethod !== 'manual';
        const useGenerationReference = projectionMethod === 'generation_reference';
        const showMethods = board?.supported && (board?.generation_views != null || board?.generation_reference != null);
        const showUnavailable = showMethods && !generationAvailable;
        ui.element.classList.toggle('tp-saved-view-mode', useGeneration);
        methodChoices.hidden = !showMethods;
        generationUnavailable.hidden = !showUnavailable;
        generationMethod.setAttribute('aria-describedby', 'projectionGenerationMethodDetail'
            + (showUnavailable ? ' projectionGenerationUnavailable' : ''));
        manualEditor.hidden = useGeneration;
        generationPanel.hidden = !useGeneration;
        manualMethod.setAttribute('aria-pressed', String(!useGeneration));
        generationMethod.setAttribute('aria-pressed', String(useGeneration));
        manualMethod.disabled = !methodSwitchAvailable();
        generationMethod.disabled = manualMethod.disabled || !generationAvailable;
        if (useGeneration) {
            submit.disabled = pending || (useGenerationReference && !generationReferenceRecipe);
            generationStatus.hidden = !useGenerationReference;
            retryGenerationFit.hidden = !useGenerationReference || !!generationReferenceRun || !!generationReferenceRecipe;
            submitLabel.nodeValue = 'Create texture copy';
            const count = savedRoles.childElementCount;
            viewStatus.textContent = useGenerationReference ? 'One saved image' :
                count + (count === 1 ? ' saved view' : ' saved views');
            return;
        }
        const { ready, issue } = inspectView();
        const multiple = inactiveView !== null;
        submit.disabled = pending || uploading || !!autoAlignRun || !board?.supported || ['orbit', 'resize'].includes(gesture?.kind)
            || !!issue || (multiple && !inactiveView.recipe);
        viewStrip.hidden = board?.max_views !== 2 || !board.supported;
        const switching = viewSwitchAvailable();
        viewButtonsByIndex.forEach(({ control, label, status }, index) => {
            const isActive = index === activeViewIndex;
            const readiness = isActive ? readyLabel(issue) : inactiveView?.readiness || '';
            control.hidden = index === 1 && !multiple;
            control.disabled = !switching && !isActive;
            control.setAttribute('aria-pressed', String(isActive));
            control.setAttribute('aria-label', `${label}${readiness ? `, ${readiness}` : ''}`);
            control.dataset.ready = String(readiness === 'Ready');
            status.textContent = readiness;
        });
        addViewButton.hidden = multiple;
        removeViewButton.hidden = activeViewIndex !== 1;
        viewHelp.hidden = !multiple;
        addViewButton.disabled = removeViewButton.disabled = !switching;
        submitLabel.nodeValue = multiple ? 'Blend views and create copy' : 'Create texture copy';
        reference.disabled = uploadButton.disabled = pending || uploading || !board?.supported;
        for (const [value, control] of Object.entries(toolButtons)) {
            control.setAttribute('aria-pressed', String(value === mode));
            control.disabled = pending || (value === 'orbit' ? !preview || !supportsCustom() : !ready);
        }
        for (const [value, control] of Object.entries(maskButtons)) {
            control.setAttribute('aria-pressed', String(value === shape));
            control.disabled = pending || !sides.source.image;
        }
        shapeOptions.disabled = pending || !sides.source.image;
        const selectedMask = masks.find(([value]) => value === shape);
        toolButtons.selection.querySelector('use').setAttribute('href', `/web/icons.svg#${selectedMask[2]}`);
        toolButtons.selection.title = `Select texture area (${selectedMask[1]})`;
        overlayButton.disabled = pending || !ready;
        overlayOptions.disabled = pending || !ready;
        overlayButton.setAttribute('aria-pressed', String(overlay.checked));
        for (const [view, control] of Object.entries(viewButtons)) {
            control.setAttribute('aria-pressed', String(view === cameraPose.view));
            control.disabled = pending || !preview || (view !== 'front' && !board?.views?.includes(view));
        }
        for (const [projection, control] of Object.entries(lensButtons)) {
            control.setAttribute('aria-pressed', String(projection === cameraPose.projection));
            control.disabled = pending || !preview || (projection === 'perspective' && !supportsPerspective());
        }
        cameraOptions.disabled = pending || !preview;
        autoAlignPanel.hidden = !supportsCustom() || !supportsPerspective();
        autoAlignButton.disabled = pending || uploading || !ready || !cameraRecipe || !!autoAlignRun
            || !!gesture || autoAlignPanel.hidden;
        cancelAlignmentButton.hidden = autoAlignProgress.hidden = !autoAlignRun;
        for (const [key, control] of Object.entries(cameraInputs)) {
            if (document.activeElement !== control) control.value = Number(cameraPose[key].toFixed(1));
            control.disabled = cameraOptions.disabled || (key !== 'fov' && !supportsCustom());
        }
        cameraInputs.fov.closest('label').hidden = cameraPose.projection !== 'perspective';
        resetSelection.disabled = pending || !sides.source.image;
        overlayOpacity.disabled = !overlay.checked || pending;
        for (const { control, value } of rangeOutputs) value.value = `${Math.round(Number(control.value) * 100)}%`;
        const viewName = titleCase(cameraPose.view);
        sides.target.heading.textContent = `Model · ${viewName}`;
        viewStatus.textContent = autoAlignRun ? 'Matching reference…'
            : multiple ? `${Number(!issue) + Number(!!inactiveView.recipe)} of 2 views ready`
            : cameraRecipe ? `${viewName} · ${titleCase(cameraPose.projection)}` : '';
        for (const key of ['source', 'target']) {
            const side = sides[key];
            side.placeholder.hidden = !!side.image;
            side.canvas.hidden = !side.image;
            side.placeholder.textContent = side.error || (side.loading
                ? key === 'source' ? 'Loading reference image…' : 'Loading model preview…'
                : key === 'source' ? 'Choose a reference image.' : 'Model preview unavailable.');
            side.panButton.setAttribute('aria-pressed', String(side.panMode));
            side.canvas.style.cursor =
                side.panMode || mode === 'pan' || (key === 'target' && mode === 'orbit')
                    ? gesture?.kind === 'orbit' ? 'grabbing' : 'grab'
                    : key === 'target' && !useGuides.checked
                      ? gesture?.cursor || 'move'
                      : 'crosshair';
            side.canvas.style.touchAction = 'none';
            draw(key);
            side.notice.textContent = side.image
                ? (key === 'source'
                    ? 'Draw the region you want to transfer.'
                    : mode === 'orbit'
                      ? 'Drag or use the arrow keys to rotate.'
                      : useGuides.checked
                        ? 'Click the matching feature on the model.'
                        : mode === 'placement' ? 'Drag to move the overlay. Pull a handle to resize it.' : 'Drag to position the overlay.')
                : '';
        }
        polygonControls.hidden = !(isDrawingMode() && shape === 'polygon');
        closePolygonButton.disabled = draft.length < 3;
        undoPointButton.disabled = !draft.length;
        guidePanel.hidden = guideTop.hidden = !useGuides.checked;
        placementPanel.hidden = mode !== 'placement' || useGuides.checked;
        undoButton.disabled = pending || !!autoAlignRun || (!history.length && !draft.length);
        const sizeDisabled = useGuides.checked || !ready || pending || !validRect(sourceRect);
        const size = relativeSize(placement);
        sizeControls.forEach(({control, value}, index) => {
            control.disabled = sizeDisabled;
            control.value = size[index];
            value.value = `${Math.round(size[index] * 100)}%`;
            control.setAttribute('aria-valuetext', value.value);
        });
        lockProportions.disabled = sizeDisabled;
        linkPreviewZoom.disabled = !ready || pending;
        for (const b of nudge.children) b.disabled = useGuides.checked || !ready || pending;
        rectInputs.forEach((control, i) => {
            if (document.activeElement !== control) control.value = (sourceRect[i] * 100).toFixed(1);
        });
        pairButtons.forEach((b, i) => {
            b.setAttribute('aria-pressed', String(i === activePair));
            const count = Number(!!sides.source.points[i]) + Number(!!sides.target.points[i]);
            b.textContent = `Point ${i + 1} · ${count}/2`;
        });
        pairTitle.textContent = `Matching point ${activePair + 1} of 4`;
        for (const key of ['source', 'target'])
            for (const axis of [0, 1]) {
                const control = pointInputs[`${key}${axis}`];
                if (document.activeElement !== control)
                    control.value = sides[key].points[activePair]
                        ? (sides[key].points[activePair][axis] * 100).toFixed(1)
                        : '';
            }
        instruction.textContent =
            mode === 'pan'
                ? 'Drag either view to pan.'
                : mode === 'orbit'
                  ? 'Rotate the model to match the reference. Choose Align to adjust the overlay.'
                  : mode === 'guides'
                    ? 'Mark the same feature in both views. Use four points spread across the image.'
                    : mode === 'placement'
                      ? 'Move the overlay or drag its handles. Unlock proportions to resize width and height separately.'
                      : shape === 'lasso'
                        ? 'Draw freely around the area and release to close the lasso.'
                        : shape === 'polygon'
                          ? 'Click along the outline, then press Enter or choose Close polygon.'
                          : 'Select the area in the reference and check its placement on the model.';
        alignmentStatus.textContent = ready ? issue : '';
    }
    function setPreviewZoom(key) {
        applyZoom(key);
        if (!linkPreviewZoom.checked) return;
        const other = key === 'source' ? 'target' : 'source';
        sides[other].zoom.value = sides[key].zoom.value;
        applyZoom(other);
    }
    function applyZoom(key) {
        const side = sides[key],
            oldWidth = side.canvas.clientWidth || side.viewport.clientWidth,
            oldHeight = side.canvas.clientHeight || side.viewport.clientHeight;
        const center = [
            (side.viewport.scrollLeft + side.viewport.clientWidth / 2) / oldWidth,
            (side.viewport.scrollTop + side.viewport.clientHeight / 2) / oldHeight,
        ];
        const aspect = side.image?.naturalWidth ? side.image.naturalWidth / side.image.naturalHeight : 1;
        side.canvas.style.width = `${Math.min(side.viewport.clientWidth, side.viewport.clientHeight * aspect) * Number(side.zoom.value)}px`;
        side.zoomValue.textContent = `${Math.round(Number(side.zoom.value) * 100)}%`;
        side.viewport.scrollLeft = center[0] * side.canvas.clientWidth - side.viewport.clientWidth / 2;
        side.viewport.scrollTop = center[1] * side.canvas.clientHeight - side.viewport.clientHeight / 2;
        draw(key);
    }
    const position = (key, event) => {
        const rect = sides[key].canvas.getBoundingClientRect();
        return [
            clamp((event.clientX - rect.left) / rect.width),
            clamp((event.clientY - rect.top) / rect.height),
        ];
    };
    const isDrawingMode = () => mode === 'selection' || mode === 'orbit';
    function pointerDown(key, event) {
        if (pending || !sides[key].image || event.button !== 0) return;
        const side = sides[key],
            point = position(key, event);
        event.preventDefault();
        side.canvas.focus();
        if (side.panMode || mode === 'pan' || event.altKey) {
            gesture = {
                kind: 'pan',
                key,
                start: [event.clientX, event.clientY],
                scroll: [side.viewport.scrollLeft, side.viewport.scrollTop],
            };
        } else if (key === 'target' && mode === 'orbit') {
            gesture = {
                kind: 'orbit', key, start: [event.clientX, event.clientY],
                previous: cameraPose, moved: false,
            };
        } else if (mode === 'guides') {
            useGuides.checked = true;
            const rect = side.canvas.getBoundingClientRect();
            let hit = -1,
                nearest = 15;
            if (side.points[activePair])
                side.points.forEach((p, index) => {
                    if (!p) return;
                    const distance = Math.hypot(
                        (p[0] - point[0]) * rect.width,
                        (p[1] - point[1]) * rect.height,
                    );
                    if (distance < nearest) {
                        hit = index;
                        nearest = distance;
                    }
                });
            if (hit >= 0) activePair = hit;
            remember();
            gesture = { kind: 'guide', key, index: activePair, newPoint: !side.points[activePair] };
            side.points[activePair] = point;
        } else if (key === 'target') {
            if (useGuides.checked) {
                ui.notice.textContent =
                    'Placement is controlled by the guide pairs. Edit a guide or turn off paired guides to move it freely.';
                return;
            }
            remember();
            const handle = overlayHandleAt(point, side.canvas);
            const original = [...placement];
            if (handle) {
                const anchor = pointInRect(sourceRect, 1 - handle.x, 1 - handle.y);
                const fixed = mapPoint(original, anchor);
                const span = handle.point.map((value, axis) => value - fixed[axis]);
                gesture = {kind: 'resize', key, start: point, original, anchor, span, cursor: handle.cursor};
            } else gesture = {kind: 'placement', key, start: point, original};
        } else if (isDrawingMode()) {
            if (shape === 'polygon') {
                if (!draft.length) remember();
                if (
                    draft.length >= 3 &&
                    Math.hypot(
                        (point[0] - draft[0][0]) * side.canvas.clientWidth,
                        (point[1] - draft[0][1]) * side.canvas.clientHeight,
                    ) < 12
                ) {
                    closePolygon();
                    return;
                }
                if (draft.length >= 128) {
                    ui.notice.textContent =
                        'A polygon can contain up to 128 points. Close it or undo a point.';
                    return;
                }
                if (
                    !draft.length ||
                    Math.hypot(point[0] - draft.at(-1)[0], point[1] - draft.at(-1)[1]) > 1e-5
                )
                    draft.push(point);
                changed();
                return;
            }
            remember();
            sourcePolygon = null;
            gesture = { kind: shape === 'lasso' ? 'lasso' : 'region', key, start: point };
            if (gesture.kind === 'lasso') draft = [point];
        } else return;
        side.canvas.setPointerCapture(event.pointerId);
        changed();
    }
    function pointerMove(key, event) {
        const point = position(key, event);
        hover = point;
        if (!gesture) {
            if (key === 'source' && draft.length) draw('source');
            if (key === 'target' && mode === 'placement' && !sides.target.panMode && !useGuides.checked) {
                const handle = overlayHandleAt(point, sides.target.canvas);
                sides.target.canvas.style.cursor = handle?.cursor || 'move';
            }
            return;
        }
        if (gesture.key !== key) return;
        const side = sides[key];
        if (gesture.kind === 'pan') {
            side.viewport.scrollLeft = gesture.scroll[0] + gesture.start[0] - event.clientX;
            side.viewport.scrollTop = gesture.scroll[1] + gesture.start[1] - event.clientY;
            return;
        }
        if (gesture.kind === 'orbit') {
            const dx = event.clientX - gesture.start[0];
            const dy = event.clientY - gesture.start[1];
            if (!gesture.moved) {
                if (Math.hypot(dx, dy) < 2) return;
                remember();
                gesture.moved = true;
            }
            const scale = 180 / Math.max(160, Math.min(side.canvas.clientWidth, side.canvas.clientHeight));
            rotateCamera(-dx * scale, dy * scale, gesture.previous, false);
            return;
        }
        if (gesture.kind === 'guide') side.points[gesture.index] = point;
        if (gesture.kind === 'placement') {
            placement = [...gesture.original];
            placement[2] += point[0] - gesture.start[0];
            placement[5] += point[1] - gesture.start[1];
        }
        if (gesture.kind === 'resize') {
            const factors = gesture.span.map((span, axis) => Math.abs(span) > 1e-8
                ? 1 + (point[axis] - gesture.start[axis]) / span : 1);
            placement = resizeOverlay(gesture.original, gesture.anchor, factors, lockProportions.checked || event.shiftKey);
        }
        if (gesture.kind === 'region')
            sourceRect = [
                Math.min(point[0], gesture.start[0]),
                Math.min(point[1], gesture.start[1]),
                Math.max(point[0], gesture.start[0]),
                Math.max(point[1], gesture.start[1]),
            ];
        if (gesture.kind === 'lasso') {
            const last = draft.at(-1);
            if (
                Math.hypot(
                    (point[0] - last[0]) * side.canvas.clientWidth,
                    (point[1] - last[1]) * side.canvas.clientHeight,
                ) >= 2
            )
                draft.push(point);
        }
        changed();
    }
    function pointerUp(key, event) {
        if (!gesture || gesture.key !== key) return;
        const current = gesture;
        gesture = null;
        if (sides[key].canvas.hasPointerCapture(event.pointerId))
            sides[key].canvas.releasePointerCapture(event.pointerId);
        if (current.kind === 'lasso') closePolygon(true);
        if (
            current.kind === 'guide' &&
            current.newPoint &&
            sides.source.points[current.index] &&
            sides.target.points[current.index]
        ) {
            const next = [0, 1, 2, 3].find((i) => !sides.source.points[i] || !sides.target.points[i]);
            if (next !== undefined) activePair = next;
        }
        changed();
    }
    function cancelGesture() {
        if (gesture && gesture.kind !== 'pan' && (gesture.kind !== 'orbit' || gesture.moved)) {
            gesture = null;
            draft = [];
            undo();
        }
        gesture = null;
        changed();
    }
    function closePolygon(fromLasso = false) {
        if (draft.length < 3) return;
        let points = fromLasso ? simplifyLasso(draft, 128) : draft;
        if (
            points.length > 1 &&
            Math.hypot(points[0][0] - points.at(-1)[0], points[0][1] - points.at(-1)[1]) < 1e-5
        )
            points = points.slice(0, -1);
        if (!validatePolygon(points)) {
            ui.notice.textContent =
                'This outline crosses itself or is too small. Undo the last point or draw it again.';
            if (fromLasso) {
                draft = [];
                sourcePolygon = null;
            }
            changed();
            return;
        }
        sourcePolygon = points.map((p) => [...p]);
        sourceRect = polygonBounds(sourcePolygon);
        draft = [];
        hover = null;
        ui.notice.textContent = '';
        changed();
    }
    useGuides.onchange = () => {
        remember();
        history.at(-1).useGuides = !useGuides.checked;
        mode = useGuides.checked ? 'guides' : 'placement';
        changed();
    };
    sizeControls.forEach(({control}, index) => {
        const beginScale = () => {
            remember();
            scaleStart = {control, matrix: [...placement]};
        };
        control.onpointerdown = beginScale;
        control.oninput = () => {
            if (useGuides.checked) return;
            if (scaleStart?.control !== control) beginScale();
            const matrix = scaleStart.matrix;
            const factors = [1, 1];
            factors[index] = Number(control.value) / relativeSize(matrix)[index];
            placement = resizeOverlay(matrix, pointInRect(sourceRect, .5, .5), factors, lockProportions.checked);
            changed();
        };
        control.onchange = control.onpointerup = control.onpointercancel = () => { scaleStart = null; };
    });
    overlay.onchange = overlayOpacity.oninput = opacity.oninput = feather.oninput = changed;
    async function chooseReference(descriptor, selection) {
        cancelAutoAlign();
        const version = ++imageVersion;
        sides.source.image = null;
        sides.source.error = '';
        sides.source.loading = true;
        selectedReference = null;
        changed();
        try {
            const image = await loadImage(descriptor.url);
            if (version !== imageVersion || !ui.element.open) return;
            sides.source.image = image;
            sides.source.loading = false;
            sides.source.canvas.height = Math.round((1024 * image.naturalHeight) / image.naturalWidth);
            contentBounds = alphaBounds(image);
            sourceRect = [...contentBounds];
            sourcePolygon = null;
            draft = [];
            history = [];
            activePair = 0;
            sides.source.points = [];
            sides.target.points = [];
            useGuides.checked = false;
            mode = 'selection';
            shape = 'ellipse';
            selectedReference = selection;
            initialisePlacement();
            sides.source.zoom.value = linkPreviewZoom.checked ? sides.target.zoom.value : 1;
            applyZoom('source');
            if (cameraRecipe) ui.notice.textContent = '';
            changed();
        } catch (error) {
            if (version === imageVersion && ui.element.open) {
                sides.source.error = error.message;
                sides.source.loading = false;
                changed();
            }
        }
    }
    reference.onchange = () => {
        if (uploadUrl) URL.revokeObjectURL(uploadUrl);
        uploadUrl = null;
        uploadName.textContent = '';
        const item = board?.references?.find((entry) => entry.id === reference.value);
        if (item) chooseReference(item, { reference: item.id });
        else {
            cancelAutoAlign();
            imageVersion++;
            selectedReference = null;
            sides.source.image = null;
            sides.source.loading = false;
            changed();
        }
    };
    upload.onchange = async () => {
        const file = upload.files?.[0];
        if (!file || uploading) return;
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
            ui.notice.textContent = 'Choose a PNG, JPEG or WebP image.';
            upload.value = '';
            return;
        }
        if (file.size > 25 * 1024 * 1024) {
            ui.notice.textContent = 'Choose an image no larger than 25 MB.';
            upload.value = '';
            return;
        }
        cancelAutoAlign();
        const version = opening;
        uploading = true;
        changed();
        ui.notice.textContent = 'Uploading reference image…';
        try {
            const data = new FormData();
            data.append('file', file);
            const result = await api('/api/v1/uploads', { method: 'POST', body: data });
            if (version !== opening || !ui.element.open) return;
            if (!result.id) throw Error('The upload did not return a reference ID.');
            if (uploadUrl) URL.revokeObjectURL(uploadUrl);
            uploadUrl = URL.createObjectURL(file);
            reference.value = '';
            uploadName.textContent = file.name;
            await chooseReference({ url: uploadUrl }, { upload_id: result.id });
        } catch (error) {
            if (version === opening && ui.element.open) ui.notice.textContent = error.message;
        } finally {
            if (version === opening) {
                uploading = false;
                changed();
            }
            upload.value = '';
        }
    };
    function projectionRecipe(bounds) {
        const value = {
            ...selectedReference,
            linked_selection: true,
            camera: cameraRecipe,
            source_rect: [...sourceRect],
            target_rect: bounds.map(clamp),
            mask_shape: sourcePolygon ? 'polygon' : shape,
            opacity: Number(opacity.value),
            feather: Number(feather.value),
        };
        if (sourcePolygon) value.source_polygon = sourcePolygon.map((p) => [...p]);
        if (useGuides.checked) {
            value.source_points = sides.source.points.map((p) => [...p]);
            value.target_points = sides.target.points.map((p) => [...p]);
        }
        return value;
    }
    submit.onclick = async () => {
        if (submit.disabled || !base) return;
        let value;
        const useGeneration = projectionMethod !== 'manual';
        if (projectionMethod === 'generation_reference') {
            if (!generationReferenceRecipe) return;
            value = generationReferenceRecipe;
        } else if (useGeneration) {
            if (!board?.generation_views?.supported) return;
            value = { method: 'generation_views' };
        } else {
            const { bounds, issue } = inspectView();
            if (issue || (inactiveView && !inactiveView.recipe)) return;
            const current = projectionRecipe(bounds);
            const other = inactiveView?.recipe;
            value = other ? { views: activeViewIndex === 0 ? [current, other] : [other, current] } : current;
        }
        const id = base.id, version = opening;
        pending = true;
        changed();
        ui.notice.textContent = projectionMethod === 'generation_views' ? 'Queueing Pixel Match…' : 'Queueing texture projection…';
        try {
            const result = await api(`/api/v1/models/${id}/texture-projection`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(value),
            });
            if (version === opening) ui.close();
            await startJob(result.id);
        } catch (error) {
            if (version === opening && ui.element.open) ui.notice.textContent = error.message;
        } finally {
            if (version === opening) {
                pending = false;
                changed();
            }
        }
    };
    ui.element.addEventListener('close', () => {
        if (ui.element.open) return;
        opening++;
        imageVersion++;
        base = board = cameraRecipe = selectedReference = null;
        generationReferenceRun?.abort();
        generationReferenceRun = generationReferenceRecipe = null;
        cancelAutoAlign();
        if (cameraRequest) cancelAnimationFrame(cameraRequest);
        cameraRequest = 0;
        cameraFit = false;
        preview?.dispose();
        preview = null;
        pending = uploading = false;
        gesture = null;
        draft = [];
        history = [];
        closeToolbarPanel();
        resizeObserver.disconnect();
        for (const side of Object.values(sides)) {
            side.image = null;
            side.points = [];
            side.canvas.width = side.canvas.height = 1;
        }
        if (uploadUrl) URL.revokeObjectURL(uploadUrl);
        if (inactiveView?.uploadUrl) URL.revokeObjectURL(inactiveView.uploadUrl);
        inactiveView = null;
        uploadUrl = null;
        upload.value = '';
        uploadName.textContent = '';
    });
    const resizeObserver = new ResizeObserver(() => {
        if (ui.element.open && projectionMethod === 'manual') {
            for (const key of ['source', 'target']) applyZoom(key);
        }
    });
    function resetView(view) {
        selectedReference = cameraRecipe = uploadUrl = null;
        reference.value = uploadName.textContent = '';
        const [yaw, pitch] = cameraViews[view];
        cameraPose = { ...defaultCamera(), view, yaw, pitch };
        scaleStart = null;
        lockProportions.checked = true;
        linkPreviewZoom.checked = false;
        useGuides.checked = false;
        overlay.checked = true;
        overlayOpacity.value = 0.5;
        shape = 'ellipse';
        mode = 'selection';
        opacity.value = 1;
        feather.value = 0.06;
        sourceRect = [0, 0, 1, 1];
        contentBounds = [...sourceRect];
        sourcePolygon = null;
        placement = [1, 0, 0, 0, 1, 0];
        initialPlacement = [...placement];
        draft = [];
        hover = null;
        history = [];
        activePair = 0;
        setAutoAlignStatus();
        for (const side of Object.values(sides)) {
            side.image = null;
            side.error = '';
            side.loading = false;
            side.points = [];
            side.canvas.width = side.canvas.height = 1024;
            side.zoom.value = 1;
            side.canvas.style.width = '100%';
            side.panMode = false;
            side.viewport.scrollTop = side.viewport.scrollLeft = 0;
            side.zoomValue.textContent = '100%';
        }
    }
    return {
        async open(job) {
            if (!job || job.state !== 'done' || ui.element.open) return;
            base = job;
            board = null;
            pending = uploading = false;
            projectionMethod = 'manual';
            manualViewports = [];
            manualNotice = '';
            inactiveView = null;
            activeViewIndex = 0;
            resetView('front');
            const version = ++opening;
            sides.target.loading = true;
            summary.textContent = nameOf(job);
            ui.show();
            for (const side of Object.values(sides)) resizeObserver.observe(side.viewport);
            ui.notice.textContent = 'Checking this model…';
            changed();
            try {
                const response = await api(`/api/v1/models/${job.id}/texture-projection`);
                if (version !== opening || !ui.element.open) return;
                board = response;
                const single = savedProjectionMethod() === 'generation_reference';
                const estimated = board.generation_views?.camera_model === 'moge';
                generationMethod.querySelector('small').textContent = single
                    ? 'One saved image · Auto Align'
                    : estimated ? 'Pixel Match · Estimated camera' : 'Pixel Match · Saved cameras';
                generationTitle.textContent = single ? 'Saved generation image' : 'Pixel Match';
                generationDescription.textContent = single ? 'Match the model to the saved TRELLIS.2 cutout.'
                    : estimated ? 'Project the saved Pixal3D cutout from its estimated camera.'
                      : 'Project the saved cutouts using their generation cameras.';
                generationFootnote.textContent = single ? 'Creates a linked copy with color projected onto the visible surface.'
                    : 'Creates a linked copy. Pixel Match blends visible details and matches color around seams.';
                const roles = board.generation_views?.views ?? [];
                savedRoles.replaceChildren(...roles.map((role) => {
                    const item = el('li');
                    const file = generationViewFiles[role];
                    if (file) {
                        const image = el('img', 'tp-generation-image');
                        image.alt = '';
                        image.loading = 'lazy';
                        image.decoding = 'async';
                        image.addEventListener('error', () => image.remove(), {once: true});
                        image.src = `/api/jobs/${job.id}/files/${file}`;
                        item.append(image);
                    }
                    item.append(el('span', '', titleCase(role)));
                    return item;
                }));
                if (single) {
                    const saved = board.references?.find(({id}) => id === board.generation_reference.reference);
                    if (saved) {
                        const item = el('li', '', '');
                        const image = el('img', 'tp-generation-image');
                        image.alt = '';
                        image.src = saved.url;
                        item.append(image, el('span', '', 'Generation image'));
                        savedRoles.append(item);
                    }
                }
                savedRoles.setAttribute('aria-label', single || estimated ? 'Saved image' : 'Saved views');
                generationUnavailable.textContent = job.operation === 'texture_projection' || job.settings?.source_kind === 'texture_projection'
                    ? 'Open the original generated model in the Library to use its saved references.'
                    : (job.settings?.engine === 'pixal3d' ? board.generation_views?.reason : board.generation_reference?.reason)
                        || board.generation_views?.reason || 'Saved generation references are unavailable for this model.';
                reference.replaceChildren(el('option', '', 'Choose a reference'));
                reference.firstChild.value = '';
                for (const item of board.references || []) {
                    const option = el('option', '', item.label);
                    option.value = item.id;
                    reference.append(option);
                }
                if (!board.supported) {
                    sides.target.loading = false;
                    ui.notice.textContent =
                        board.reason || 'Texture projection is not available for this model.';
                    changed();
                    return;
                }
                if (board.linked_selection !== true) {
                    board.supported = false;
                    sides.target.loading = false;
                    ui.notice.textContent =
                        'The server needs the matching Texture Projection update. Restart Studio and reload this page.';
                    changed();
                    return;
                }
                ui.notice.textContent = 'Preparing the model preview…';
                const ref = board.references?.[0];
                if (ref) {
                    reference.value = ref.id;
                    chooseReference(ref, { reference: ref.id });
                }
                const loaded = await loadProjectionPreview(
                    `/api/jobs/${job.id}/files/model.glb?v=${encodeURIComponent(job.result?.revision || job.id)}`,
                    () => version === opening && ui.element.open,
                );
                if (!loaded) return;
                preview = loaded;
                sides.target.loading = false;
                queueCamera(true);
                ui.notice.textContent = '';
                changed();
            } catch (error) {
                if (version === opening && ui.element.open) {
                    sides.target.loading = false;
                    sides.target.error = error.message;
                    ui.notice.textContent = error.message;
                    changed();
                }
            }
        },
    };
}

export function createBakedGameCopy({api, startJob}) {
    const ui = dialog('bakedGameCopy', 'Game Copy from HD', '', '', 'game-copy-dialog');
    const summary = el('p', 'gc-model-name');
    ui.element.querySelector('.studio-dialog-head h2').after(summary);
    const profile = select([['object', 'Object'], ['humanoid', 'Humanoid']], 'object');
    const profileHelp = el('p', 'hint gc-profile-help');
    profileHelp.id = 'bakedGameProfileHelp';
    profile.setAttribute('aria-describedby', profileHelp.id);
    const triangles = input('number', 28000, {min: 5000, max: 150000, step: 1, required: ''}); triangles.id = 'bakedGameTriangles';
    const texture = select([[1024, '1K'], [2048, '2K'], [4096, '4K']], 2048); texture.id = 'bakedGameTexture';
    const normals = input('checkbox', ''); normals.checked = true; normals.id = 'bakedGameNormals';
    const normalLabel = el('label', 'check'); normalLabel.append(normals, el('span', '', 'Bake surface detail to a normal map'));
    const normalHint = el('p', 'hint');
    const form = el('form'); form.id = 'bakedGameForm';
    const options = el('fieldset', 'gc-options');
    const pair = el('div', 'gc-field-pair');
    pair.append(field('Target triangles', triangles), field('Texture resolution', texture));
    const regions = createGameCopyRegions(disposeModel);
    options.append(el('legend', '', 'Reduction settings'), field('Profile', profile), profileHelp, pair, normalLabel, normalHint);
    const aside = el('div', 'gc-sidebar');
    aside.append(options, regions.section);
    form.append(regions.surface, aside);
    const submit = button('Create game copy', 'cube', 'primary'); submit.type = 'submit'; submit.setAttribute('form', form.id); submit.id = 'confirmBakedGameCopy';
    const footnote = el('small', 'gc-footnote', 'Creates a new model with UVs and textures baked from the HD source.');
    ui.body.append(form); ui.foot.append(footnote, button('Cancel', 'close', 'secondary', () => ui.close()), submit);
    let base = null, supported = false, opening = 0, pending = false;
    function updateProfileHelp() {
        profileHelp.textContent = profile.value === 'humanoid'
            ? 'Prioritizes the face and hands of an upright, front-facing T-pose. Add zones for extra detail.'
            : '';
    }
    profile.onchange = () => {
        triangles.value = profile.value === 'humanoid' ? 14000 : 28000;
        triangles.setCustomValidity('');
        updateProfileHelp();
    };
    ui.element.addEventListener('close', () => {
        if (ui.element.open) return;
        opening++;
        base = null;
        supported = false;
        pending = false;
        regions.reset();
    });
    form.onsubmit = async event => {
        event.preventDefault(); if (!base || !supported || pending || !form.reportValidity()) return;
        const id = base.id, version = opening; pending = true; submit.disabled = true; ui.notice.textContent = 'Queueing game copy…';
        const recipe = {
            triangles: Number(triangles.value), texture_size: Number(texture.value),
            profile: profile.value, bake_normals: normals.checked,
        };
        if (regions.values().length) recipe.protected_regions = regions.values();
        try {
            const result = await api(`/api/v1/models/${id}/baked-game-copy`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(recipe)});
            if (version === opening) ui.close(); await startJob(result.id);
        } catch (error) { if (version === opening && ui.element.open) ui.notice.textContent = error.message; }
        finally { if (version === opening) { pending = false; submit.disabled = !supported; } }
    };
    return {async open(job) {
        if (!job || job.state !== 'done') return;
        if (ui.element.open) return;
        base = job; supported = false; pending = false; const version = ++opening;
        regions.reset();
        options.disabled = true;
        profileHelp.textContent = '';
        normals.checked = true; normals.disabled = false; normalHint.textContent = '';
        triangles.setCustomValidity('');
        summary.textContent = nameOf(job); submit.disabled = true; ui.show(); ui.notice.textContent = 'Checking this model…';
        const current = () => version === opening && ui.element.open;
        try {
            const response = await api(`/api/v1/models/${job.id}/baked-game-copy`);
            if (!current()) return;
            supported = response.supported === true && response.ready !== false;
            const available = response.profiles || ['object'];
            profile.replaceChildren(...available.filter(value => ['object', 'humanoid'].includes(value)).map(value => {
                const option = el('option', '', value === 'humanoid' ? 'Humanoid' : 'Object');
                option.value = value;
                return option;
            }));
            profile.value = response.humanoid && available.includes('humanoid') ? 'humanoid' : 'object';
            if (!profile.value) profile.selectedIndex = 0;
            updateProfileHelp();
            triangles.value = response.default_triangles || (profile.value === 'humanoid' ? 14000 : 28000);
            texture.replaceChildren(...(response.texture_sizes || [1024, 2048, 4096]).map(size => { const option = el('option', '', `${size / 1024}K`); option.value = size; return option; })); texture.value = 2048;
            if (!texture.value) texture.selectedIndex = 0;
            if (response.triangle_limits?.length === 2) { triangles.min = response.triangle_limits[0]; triangles.max = response.triangle_limits[1]; }
            normals.disabled = response.has_normal_map === true;
            normalHint.textContent = normals.disabled ? 'Normal-map transfer is required to preserve the source model’s existing surface detail.' : '';
            ui.notice.textContent = supported ? '' : response.reason || response.dependencies?.reason || (response.ready === false ? 'Game-copy tools are not ready. Install the required components and restart Studio.' : 'Game Copy from HD is not available for this model.'); submit.disabled = !supported;
            options.disabled = !supported;
            if (supported) {
                const url = `/api/jobs/${job.id}/files/model.glb?v=${encodeURIComponent(job.result?.revision || job.id)}`;
                await regions.start(url, response.protected_region_limit, current);
            }
        } catch (error) { if (version === opening && ui.element.open) ui.notice.textContent = error.message; }
    }};
}
