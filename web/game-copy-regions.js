import { button, el, field, input } from './workbench-ui.js?v=3.4.0';
import { openReductionRegionPreview } from './reduction-region-preview.js?v=native-options';

const radiusPercent = new Intl.NumberFormat('en', { maximumFractionDigits: 2 });

export function createGameCopyRegions(disposeModel) {
    const surface = el('section', 'gc-surface');
    surface.setAttribute('aria-label', 'Model preview');
    const tools = el('div', 'gc-toolbar');
    const modes = group('Preview tool');
    const orbit = button('Orbit', 'rotate', 'gc-tool', () => selectMode('orbit'));
    const add = button('Add zone', 'plus', 'gc-tool', () => selectMode('pick'));
    const views = group('Camera');
    const closer = button('', 'plus', 'gc-tool', () => preview?.zoom(.78));
    const farther = button('', 'minus', 'gc-tool', () => preview?.zoom(1.28));
    closer.setAttribute('aria-label', 'Zoom in');
    farther.setAttribute('aria-label', 'Zoom out');
    closer.title = 'Zoom in';
    farther.title = 'Zoom out';
    const fit = button('Fit', 'fit', 'gc-tool', () => preview?.fit());
    modes.append(orbit, add);
    views.append(farther, closer, fit);
    tools.append(modes, views);

    const viewport = el('div', 'gc-viewport');
    const empty = el('p', 'gc-viewport-state');
    empty.setAttribute('role', 'status');
    viewport.append(empty);
    const guidance = el('p', 'gc-view-help');
    guidance.id = 'bakedGameRegionHelp';
    guidance.setAttribute('role', 'status');
    surface.append(tools, viewport, guidance);

    const section = el('section', 'gc-regions');
    const heading = el('div', 'gc-section-head');
    const count = el('span', 'gc-count');
    const clear = button('Clear all', 'trash', 'gc-tool', () => {
        regions.length = 0;
        active = -1;
        update();
        add.focus({ preventScroll: true });
    });
    heading.append(el('h3', '', 'Protected zones'), count, clear);
    const list = el('ol', 'gc-zone-list');
    list.setAttribute('aria-label', 'Protected zones');
    const noZones = el('p', 'hint gc-no-zones', 'Add zones to preserve details such as cables, handles and straps.');
    const editor = el('div', 'gc-zone-editor');
    const activeLabel = el('strong');
    const radius = input('range', 4, { min: .25, max: 40, step: .25 });
    const strength = input('range', 85, { min: 1, max: 100, step: 1 });
    const radiusValue = el('output');
    const strengthValue = el('output');
    const remove = button('Remove zone', 'trash', 'gc-tool', () => {
        regions.splice(active, 1);
        active = Math.min(active, regions.length - 1);
        update();
        (list.querySelector('[aria-pressed=true]') || add).focus({ preventScroll: true });
    });
    const editorHead = el('div', 'gc-section-head');
    editorHead.append(activeLabel, remove);
    editor.append(editorHead, slider('Radius', radius, radiusValue), slider('Priority', strength, strengthValue));
    section.append(heading, noZones, list, editor);

    const regions = [];
    let preview = null, active = -1, limit = 0, mode = 'orbit';
    radius.oninput = strength.oninput = () => {
        const region = regions[active];
        region.radius = Number(radius.value) * preview.localSpan / 100;
        region.strength = Number(strength.value) / 100;
        applySliders();
        preview.setZones(regions, active);
    };
    reset();

    function slider(label, control, output) {
        const wrapper = field(label, control);
        control.id = `bakedGameZone${label}`;
        wrapper.htmlFor = control.id;
        output.setAttribute('aria-hidden', 'true');
        wrapper.firstChild.append(output);
        return wrapper;
    }

    function group(label) {
        const set = el('div', 'gc-tool-group');
        set.setAttribute('role', 'group');
        set.setAttribute('aria-label', label);
        return set;
    }

    function selectMode(next) {
        mode = next;
        orbit.setAttribute('aria-pressed', String(mode === 'orbit'));
        add.setAttribute('aria-pressed', String(mode === 'pick'));
        viewport.classList.toggle('is-picking', mode === 'pick');
        preview?.setMode(mode);
        guidance.textContent = mode === 'pick'
            ? 'Select a point to protect. With the preview focused, use arrow keys and Enter.'
            : 'Drag to rotate. Right-drag to pan. Scroll, pinch, or use the zoom controls.';
    }

    function applySliders() {
        const percent = `${radiusPercent.format(Number(radius.value))}%`;
        radiusValue.textContent = percent;
        strengthValue.textContent = `${strength.value}%`;
        radius.setAttribute('aria-valuetext', `${percent} of the model span`);
        strength.setAttribute('aria-valuetext', strengthValue.textContent);
    }

    function update() {
        count.textContent = `${regions.length} / ${limit}`;
        clear.hidden = regions.length === 0;
        noZones.hidden = regions.length > 0;
        editor.hidden = active < 0;
        add.disabled = !preview || regions.length >= limit;
        const entries = regions.map((_, index) => {
            const row = el('li');
            const item = button(`Zone ${index + 1}`, 'select', 'gc-tool', () => {
                active = index;
                update();
                list.children[index].firstChild.focus({ preventScroll: true });
            });
            item.setAttribute('aria-pressed', String(index === active));
            row.append(item);
            return row;
        });
        list.replaceChildren(...entries);
        if (active >= 0) {
            activeLabel.textContent = `Zone ${active + 1}`;
            radius.value = regions[active].radius / preview.localSpan * 100;
            strength.value = regions[active].strength * 100;
            applySliders();
        }
        preview?.setZones(regions, active);
    }

    function onPick(center) {
        if (!center) {
            guidance.textContent = 'Select a point directly on the model surface.';
            return;
        }
        if (regions.length >= limit) return;
        regions.push({ center, radius: preview.localSpan * .04, strength: .85 });
        active = regions.length - 1;
        if (regions.length === limit) selectMode('orbit');
        else guidance.textContent = 'Adjust the selected zone or select another point on the model.';
        update();
    }

    function reset() {
        preview?.dispose();
        preview = null;
        regions.length = 0;
        active = -1;
        limit = 0;
        surface.hidden = section.hidden = true;
        selectMode('orbit');
        update();
    }

    async function start(url, supportedLimit, isCurrent) {
        if (!Number.isInteger(supportedLimit) || supportedLimit <= 0) return;
        limit = Math.min(24, supportedLimit);
        surface.hidden = false;
        tools.hidden = guidance.hidden = false;
        for (const control of [orbit, add, closer, farther, fit]) control.disabled = true;
        guidance.textContent = '';
        empty.hidden = false;
        empty.textContent = 'Loading the high-detail model…';
        try {
            const loaded = await openReductionRegionPreview(viewport, {
                url, isCurrent, disposeModel, limit, statusId: guidance.id, onPick,
            });
            if (!loaded) return;
            preview = loaded;
            section.hidden = false;
            empty.hidden = true;
            for (const control of [orbit, closer, farther, fit]) control.disabled = false;
            selectMode('orbit');
            update();
        } catch (error) {
            if (!isCurrent()) return;
            tools.hidden = guidance.hidden = true;
            empty.textContent = 'The 3D preview is unavailable. You can still create a game copy.';
        }
    }

    return { surface, section, reset, start, values: () => regions };
}
