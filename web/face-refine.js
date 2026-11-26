import {button, dialog, el, field, input} from './workbench-ui.js?v=3.4.0';
import {createFaceCrop} from './face-crop.js?v=face-refine';
import {createFaceReliefPreview} from './face-relief-preview.js?v=studio-timing';

export function createFaceRefinement({api, startJob, getMaterialAdjustment}) {
    const ui = dialog('faceRefineDialog', 'Refine face', 'Prepare', '', 'face-refine-dialog');
    let session;
    const current = s => s === session && !s.controller.signal.aborted && ui.element.open;
    const notice = text => { ui.notice.textContent = text; };

    function discard(s) {
        clearTimeout(s.timer);
        s.renderController?.abort();
        s.viewer?.dispose();
        s.viewer = null;
        if (s.draft) {
            const draft = s.draft;
            s.draft = null;
            void api(draft, {method: 'DELETE'}).catch(() => {});
        }
    }
    function release(s) {
        s.controller.abort();
        discard(s);
        s.crop?.dispose();
        if (s.localURL) URL.revokeObjectURL(s.localURL);
    }
    ui.element.addEventListener('close', () => {
        if (ui.element.open || !session) return;
        release(session);
        session = null;
        ui.body.replaceChildren();
    });

    function controls(s) {
        const choose = button('Choose portrait', 'image', 'secondary', () => chooser.click());
        const chooser = input('file', '', {accept: 'image/png,image/jpeg,image/webp'});
        chooser.hidden = true;
        chooser.onchange = () => {
            const file = chooser.files[0];
            if (file) void loadImage(s, file);
        };
        const useOriginal = button('Use original image', 'restore', 'quiet', () => void loadImage(s, null));
        useOriginal.hidden = !s.file || !s.reference;
        const preview = button('Preview face', 'play', 'primary', () => void generate(s));
        preview.disabled = !s.crop;
        ui.foot.replaceChildren(choose, chooser, useOriginal, preview);
        return preview;
    }
    async function loadImage(s, file) {
        const request = ++s.imageRequest;
        s.crop?.dispose();
        s.crop = null;
        s.file = file;
        if (s.localURL) URL.revokeObjectURL(s.localURL);
        s.localURL = file ? URL.createObjectURL(file) : null;
        const url = s.localURL || s.reference;
        ui.body.replaceChildren();
        const preview = controls(s);
        if (!url) {
            notice('Choose a front-facing portrait of this character to get started.');
            return;
        }
        notice('Checking the portrait…');
        try {
            let suggestion = s.suggested;
            if (file) {
                const data = new FormData();
                data.append('image', file);
                const report = await api(s.base + '/detect', {method: 'POST', body: data, signal: s.controller.signal});
                suggestion = report.crop;
            }
            if (!current(s) || request !== s.imageRequest) return;
            const crop = await createFaceCrop(url, suggestion);
            if (!current(s) || request !== s.imageRequest) { crop.dispose(); return; }
            s.crop = crop;
            ui.body.replaceChildren(crop.element);
            preview.disabled = false;
            notice(suggestion ? '' : 'Choose the head by moving and resizing the frame.');
        } catch (error) {
            if (current(s) && request === s.imageRequest) {
                notice(error.name === 'EncodingError' ? 'This image could not be opened. Choose a PNG, JPEG or WebP portrait.' : error.message);
            }
        }
    }
    function editCrop(s, error) {
        discard(s);
        ++s.attempt;
        ui.body.replaceChildren(s.crop.element);
        controls(s);
        notice(error || '');
    }
    function showProgress(s) {
        const stage = el('strong', '', 'Preparing face preview…');
        const bar = el('progress');
        bar.max = 100;
        bar.setAttribute('aria-label', 'Face refinement progress');
        const progress = el('div', 'face-refine-progress');
        progress.append(stage, bar);
        ui.body.replaceChildren(progress);
        ui.foot.replaceChildren(button('Back to crop', 'back', 'secondary', () => editCrop(s)));
        notice('');
        return {stage, bar};
    }
    async function generate(s) {
        const attempt = ++s.attempt;
        const active = () => current(s) && s.attempt === attempt;
        const progress = showProgress(s);
        const data = new FormData();
        data.append('crop', JSON.stringify(s.crop.value()));
        if (s.file) data.append('image', s.file);
        try {
            const job = await api(s.base + '/previews', {method: 'POST', body: data});
            const draft = s.base + '/previews/' + encodeURIComponent(job.id);
            if (!active()) {
                void api(draft, {method: 'DELETE'}).catch(() => {});
                return;
            }
            s.draft = draft;
            let transientFailures = 0;
            async function poll() {
                if (!active()) return;
                let status;
                try {
                    status = await api(draft, {signal: s.controller.signal});
                } catch (error) {
                    if (!active()) return;
                    if (error instanceof TypeError && ++transientFailures <= 3) {
                        notice('Reconnecting to Studio…');
                        s.timer = setTimeout(poll, 1500);
                    } else {
                        editCrop(s, error.message);
                    }
                    return;
                }
                if (!active()) return;
                transientFailures = 0;
                if (status.state === 'done') {
                    try { await showResult(s, status, active); }
                    catch (error) { if (active()) editCrop(s, error.message); }
                    return;
                }
                if (status.state === 'error' || status.state === 'cancelled') {
                    editCrop(s, status.error || status.stage || 'Face preview stopped.');
                    return;
                }
                notice('');
                progress.stage.textContent = status.stage || 'Preparing face preview…';
                if (Number.isFinite(status.progress)) progress.bar.value = status.progress;
                s.timer = setTimeout(poll, 900);
            }
            await poll();
        } catch (error) {
            if (active()) editCrop(s, error.message);
        }
    }
    async function showResult(s, status, active) {
        const scene = el('div', 'face-result-view');
        const tools = el('div', 'face-result-tools');
        tools.setAttribute('role', 'group');
        tools.setAttribute('aria-label', 'Face geometry');
        const before = button('Original', 'cube', 'secondary');
        const after = button('Refined', 'layers', 'secondary');
        const frame = button('Frame face', 'fit', 'secondary', () => s.viewer?.frame());
        tools.append(before, after, frame);
        const controlsPanel = el('div', 'face-result-controls');
        const strength = input('range', '65', {min: 0, max: 100, step: 1});
        const strengthField = field('Intensity', strength);
        const value = el('output', '', '65%');
        strengthField.append(value);
        controlsPanel.append(tools, strengthField);
        ui.body.replaceChildren(scene, controlsPanel);
        const save = button('Create copy', 'layers', 'primary');
        const back = button('Adjust crop', 'image', 'secondary', () => editCrop(s));
        ui.foot.replaceChildren(back, save);
        for (const control of [before, after, frame, strength, save]) control.disabled = true;
        notice('Loading the face in 3D…');
        const modelURL = '/api/jobs/' + encodeURIComponent(s.model.id) + '/files/model.glb?v=' +
            encodeURIComponent(s.model.result?.revision || 0);
        s.renderController = new AbortController();
        const viewer = await createFaceReliefPreview(scene, modelURL, status, s.renderController.signal, getMaterialAdjustment?.(s.model.id));
        if (!active()) { viewer.dispose(); return; }
        s.viewer = viewer;
        for (const control of [before, after, frame, strength, save]) control.disabled = false;
        function display(refined) {
            before.setAttribute('aria-pressed', String(!refined));
            after.setAttribute('aria-pressed', String(refined));
            value.textContent = strength.value + '%';
            strength.setAttribute('aria-valuetext', value.textContent);
            save.lastChild.textContent = refined ? 'Create copy' : 'Create refined copy · ' + value.textContent;
            viewer.setStrength(refined ? Number(strength.value) / 100 : 0);
        }
        before.onclick = () => display(false);
        after.onclick = () => display(true);
        strength.oninput = () => display(true);
        display(true);
        notice('');
        save.onclick = async () => {
            for (const control of [save, back, strength, before, after]) control.disabled = true;
            notice('Creating a refined copy…');
            try {
                const result = await api(s.draft + '/copy', {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({strength: Number(strength.value) / 100})});
                if (!active()) return;
                ui.close();
                await startJob(result.id);
            } catch (error) {
                if (!active()) return;
                for (const control of [save, back, strength, before, after]) control.disabled = false;
                notice(error.message);
            }
        };
    }
    async function open(model) {
        if (!model || model.state !== 'done') return;
        if (session) release(session);
        const s = session = {model, base: '/api/jobs/' + encodeURIComponent(model.id) + '/face-refine',
            controller: new AbortController(), attempt: 0, imageRequest: 0};
        ui.show();
        ui.body.replaceChildren();
        ui.foot.replaceChildren();
        notice('Checking the model and its reference…');
        try {
            const info = await api(s.base, {signal: s.controller.signal});
            if (!current(s)) return;
            if (!info.available) { notice(info.reason || 'Refine face requires a static, textured model.'); return; }
            s.reference = info.reference_url;
            s.suggested = info.crop;
            await loadImage(s, null);
        } catch (error) {
            if (current(s)) notice(error.message);
        }
    }
    return {open};
}
