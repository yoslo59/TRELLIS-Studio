import {el, field, input} from './workbench-ui.js?v=3.4.0';

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export async function createFaceCrop(url, suggested) {
    const image = new Image();
    image.alt = 'Portrait reference';
    image.src = url;
    await image.decode();
    const width = image.naturalWidth, height = image.naturalHeight;
    const maxSide = Math.min(width, height);
    const minSide = Math.min(maxSide, Math.max(16, Math.round(maxSide / 50)));
    let side = suggested ? Math.max(suggested[2] * width, suggested[3] * height) : maxSide * .65;
    let x = suggested ? (suggested[0] + suggested[2] / 2) * width : width / 2;
    let y = suggested ? (suggested[1] + suggested[3] / 2) * height : height / 2;

    const element = el('div', 'face-crop-layout');
    const viewport = el('div', 'face-crop-viewport');
    const bounds = el('div', 'face-crop-image');
    const box = el('button', 'face-crop-box');
    box.type = 'button';
    box.setAttribute('aria-label', 'Move face crop. Use the arrow keys to position it.');
    box.title = 'Drag to move the face crop or use the arrow keys';
    bounds.append(image, box);
    viewport.append(bounds);
    const detail = el('div', 'face-crop-detail');
    const portrait = el('canvas');
    portrait.width = portrait.height = 320;
    portrait.setAttribute('role', 'img');
    portrait.setAttribute('aria-label', 'Cropped portrait that will be used for the face');
    const zoom = input('range', Math.round(side), {min: minSide, max: maxSide, step: 1});
    detail.append(el('h3', '', 'Face crop'), portrait, field('Crop size', zoom),
        el('p', 'hint', 'Drag the frame to include the whole head and a little of the neck.'));
    element.append(viewport, detail);

    // Keep the overlay attached to the actual image bounds inside the letterboxed view.
    function fit() {
        const scale = Math.min(viewport.clientWidth / width, viewport.clientHeight / height);
        bounds.style.width = width * scale + 'px';
        bounds.style.height = height * scale + 'px';
    }
    const observer = new ResizeObserver(fit);
    observer.observe(viewport);
    function update() {
        side = clamp(side, minSide, maxSide);
        x = clamp(x, side / 2, width - side / 2);
        y = clamp(y, side / 2, height - side / 2);
        box.style.left = (x - side / 2) / width * 100 + '%';
        box.style.top = (y - side / 2) / height * 100 + '%';
        box.style.width = side / width * 100 + '%';
        box.style.height = side / height * 100 + '%';
        zoom.value = Math.round(side);
        zoom.setAttribute('aria-valuetext', Math.round(side) + ' pixels');
        const ctx = portrait.getContext('2d');
        ctx.clearRect(0, 0, portrait.width, portrait.height);
        ctx.drawImage(image, x - side / 2, y - side / 2, side, side, 0, 0, portrait.width, portrait.height);
    }
    zoom.oninput = () => { side = Number(zoom.value); update(); };
    let drag;
    const location = event => {
        const rect = bounds.getBoundingClientRect();
        return [(event.clientX - rect.left) * width / rect.width, (event.clientY - rect.top) * height / rect.height];
    };
    bounds.onpointerdown = event => {
        if (event.button !== 0 || !event.isPrimary) return;
        const [px, py] = location(event);
        drag = event.target === box ? [x - px, y - py] : [0, 0];
        box.focus({preventScroll: true});
        bounds.setPointerCapture(event.pointerId);
        x = px + drag[0]; y = py + drag[1]; update();
        event.preventDefault();
    };
    bounds.onpointermove = event => {
        if (!drag || !bounds.hasPointerCapture(event.pointerId)) return;
        const [px, py] = location(event);
        x = px + drag[0]; y = py + drag[1]; update();
    };
    bounds.onlostpointercapture = () => { drag = null; };
    box.onkeydown = event => {
        const vector = {ArrowLeft: [-1,0], ArrowRight: [1,0], ArrowUp: [0,-1], ArrowDown: [0,1]}[event.key];
        if (!vector) return;
        event.preventDefault();
        const step = event.shiftKey ? 10 : 1;
        x += vector[0] * step; y += vector[1] * step; update();
    };
    update();
    return {
        element,
        value: () => [(x - side / 2) / width, (y - side / 2) / height, side / width, side / height],
        dispose: () => observer.disconnect(),
    };
}
