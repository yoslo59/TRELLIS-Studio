import {button, dialog, el, field, input, select} from './workbench-ui.js?v=3.3.5';

const number = value => Number.isFinite(value) ? value.toLocaleString('en-US') : '—';
const nameOf = job => job?.metadata?.display_name || job?.settings?.name || 'Source model';
const canCompare = job => job?.state === 'done' && job.media?.available !== false;
const labels = {queued: 'Queued', running: 'Finishing', done: 'Completed', error: 'Failed', cancelled: 'Cancelled'};
const defaults = [
    {label: 'Fine detail', triangles: 60000, mesh_mode: 'direct', repair_holes: false},
    {label: 'Game target', triangles: 28000, mesh_mode: 'remesh', repair_holes: true},
    {label: 'Lightweight', triangles: 12000, mesh_mode: 'remesh', repair_holes: true},
];

export function createFinishingBoard({api, getRecords, refresh, compare}) {
    const ui = dialog('finishingBoard', 'Finishing board', 'CACHED SOURCE',
        'Queue finishing recipes on one saved source, then compare and keep a result.', 'finishing-board');
    const summary = el('p', 'finishing-summary');
    const selected = el('p', 'finishing-choice');
    const form = el('form', 'finishing-form');
    form.id = 'finishingBoardRecipes';
    const results = el('section', 'finishing-results');
    const rows = el('div', 'finishing-rows');
    results.setAttribute('aria-labelledby', 'finishingBoardResults');
    const heading = el('h3', '', 'Source and variants');
    heading.id = 'finishingBoardResults';
    results.append(heading, selected, rows);
    ui.body.append(summary, form, results);
    const queue = button('Queue variants', 'layers', 'primary');
    queue.type = 'submit';
    queue.setAttribute('form', form.id);
    const footerHint = el('small', 'wb-footnote', 'Shared cache · Finishing uses the GPU');
    ui.foot.append(footerHint, queue);

    let base = null, board = null, drafts = [], opening = 0, pending = false;
    const cards = new Map();
    const request = (method, value) => api(`/api/jobs/${base.id}/finishing-board`, {
        method,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(value),
    });

    function family() {
        if (!board) return [];
        const jobs = getRecords();
        const source = jobs.find(job => job.id === board.source_id);
        const variants = jobs.filter(job => job.settings?.source_kind === 'refinish'
            && job.settings.finish_source_id === board.source_id);
        variants.sort((left, right) => (right.settings.created || 0) - (left.settings.created || 0));
        return source ? [source, ...variants] : variants;
    }

    function updateQueueButton() {
        const count = drafts.filter(draft => draft.enabled.checked).length;
        queue.disabled = pending || !board || !count;
        queue.lastChild.textContent = count ? `Queue ${count} variant${count === 1 ? '' : 's'}` : 'Queue variants';
    }

    function recipeForm() {
        form.replaceChildren(el('h3', '', 'New recipes'));
        const textureChoices = board.texture_sizes.map(size => [size, `${size / 1024}K`]);
        const textureDefault = board.texture_sizes.includes(base.settings.texture_size) ? base.settings.texture_size : 2048;
        drafts = defaults.map(recipe => {
            const draft = el('fieldset', 'finishing-recipe');
            draft.append(el('legend', '', recipe.label));
            const enabled = input('checkbox', '');
            enabled.checked = !(base.settings.triangles === recipe.triangles
                && base.settings.texture_size === textureDefault
                && base.generation_options?.mesh_mode === recipe.mesh_mode
                && base.generation_options?.repair_holes === recipe.repair_holes);
            const triangles = input('number', recipe.triangles, {min: 1000, max: 8000000, step: 1, required: ''});
            const texture = select(textureChoices, textureDefault);
            const mode = select([['remesh', 'Remesh and simplify'], ['direct', 'Direct simplification']], recipe.mesh_mode);
            const repair = input('checkbox', '');
            repair.checked = recipe.repair_holes;
            const toggle = () => {
                for (const control of [triangles, texture, mode, repair]) control.disabled = !enabled.checked;
                updateQueueButton();
            };
            enabled.addEventListener('change', toggle);
            draft.append(field('Include recipe', enabled), field('Target triangles', triangles),
                field('Texture size', texture), field('Finishing method', mode), field('Fill small holes', repair));
            form.append(draft);
            for (const control of [triangles, texture, mode, repair]) control.disabled = !enabled.checked;
            return {enabled, triangles, texture, mode, repair};
        });
        form.append(el('p', 'hint', 'Recipes share the reference, decoded geometry, materials and seeds. Finishing runs without another neural inference.'));
    }

    function rowFor(id) {
        if (cards.has(id)) return cards.get(id);
        const root = el('article', 'finishing-row');
        const title = el('strong');
        const metrics = el('p');
        const stage = el('small');
        const progress = el('progress');
        progress.max = 100;
        const text = el('div');
        text.append(title, metrics, stage, progress);
        const actions = el('div', 'finishing-row-actions');
        const compareButton = button('Compare', 'compare', 'secondary', () => {
            const jobs = family().filter(canCompare);
            const picked = jobs.find(job => job.id === id);
            const baseline = jobs.find(job => job.id === board.source_id && job.id !== id)
                || jobs.find(job => job.id !== id);
            if (!picked || !baseline) return;
            ui.close();
            compare.open(baseline, picked, {candidates: jobs, backToBoard: () => { ui.show(); update(); }});
        });
        const keep = button('Keep', 'check', 'secondary', () => choose(id));
        actions.append(compareButton, keep);
        root.append(text, actions);
        rows.append(root);
        const card = {root, title, metrics, stage, progress, compareButton, keep};
        cards.set(id, card);
        return card;
    }

    function update() {
        if (!ui.element.open) return;
        updateQueueButton();
        if (!board) return;
        const jobs = family();
        const retained = jobs.find(job => job.id === board.selected_id);
        selected.textContent = retained ? 'Kept for this board · ' + nameOf(retained)
            : board.selected_id ? 'The kept model is unavailable. Restore it or choose another result.'
                : 'Choose the variant to keep after reviewing its shape and materials.';
        const ids = new Set(jobs.map(job => job.id));
        for (const [id, card] of cards) {
            if (!ids.has(id)) {
                card.root.remove();
                cards.delete(id);
            }
        }
        const baseline = jobs.find(job => job.id === board.source_id);
        const baselineTriangles = baseline?.result?.triangles;
        const canPair = jobs.filter(canCompare).length >= 2;
        for (const [index, job] of jobs.entries()) {
            const card = rowFor(job.id);
            if (rows.children[index] !== card.root) rows.insertBefore(card.root, rows.children[index] || null);
            const isSource = job.id === board.source_id;
            const isKept = job.id === board.selected_id;
            const result = job.result || {};
            const pieces = [isSource ? 'Original' : job.generation_options?.mesh_mode === 'direct' ? 'Direct' : 'Remesh'];
            pieces.push(Number.isFinite(result.triangles) ? `${number(result.triangles)} triangles`
                : `${number(job.settings.triangles)} target triangles`);
            const texture = result.texture_size ?? job.settings.texture_size;
            if (texture) pieces.push(`${texture / 1024}K textures`);
            if (!isSource && baselineTriangles && result.triangles) {
                const difference = (result.triangles / baselineTriangles - 1) * 100;
                pieces.push(`${difference > 0 ? '+' : ''}${difference.toFixed(1)}% vs original`);
            }
            if (Number.isFinite(result.seconds)) pieces.push(`${result.seconds} s`);
            if (Number.isFinite(result.peak_vram_gib)) pieces.push(`${result.peak_vram_gib} GiB peak VRAM`);
            if (result.neural_inferences === 0) pieces.push('0 inferences');
            card.title.textContent = nameOf(job);
            card.metrics.textContent = pieces.join(' · ');
            card.stage.textContent = [labels[job.state] || job.state,
                job.error || (job.state === 'done' ? '' : job.stage)].filter(Boolean).join(' · ');
            card.stage.classList.toggle('finishing-failure', job.state === 'error');
            card.progress.hidden = !['queued', 'running'].includes(job.state);
            card.progress.value = job.progress || 0;
            card.progress.setAttribute('aria-label', 'Progress for ' + nameOf(job));
            card.compareButton.disabled = !canPair || !canCompare(job);
            card.keep.disabled = pending || !canCompare(job);
            card.keep.lastChild.textContent = isKept ? 'Clear choice' : 'Keep';
            card.root.classList.toggle('finishing-kept', isKept);
        }
    }

    async function choose(id) {
        if (pending || !board) return;
        const token = opening;
        pending = true;
        ui.notice.textContent = '';
        update();
        try {
            const response = await request('PATCH', {selected_id: board.selected_id === id ? null : id});
            if (token === opening) board.selected_id = response.selected_id;
        } catch (error) {
            if (token === opening) ui.notice.textContent = error.message;
        } finally {
            if (token === opening) {
                pending = false;
                update();
            }
        }
    }

    form.onsubmit = async event => {
        event.preventDefault();
        if (!board || pending || !form.reportValidity()) return;
        const variants = drafts.filter(draft => draft.enabled.checked).map(draft => ({
            triangles: draft.triangles.valueAsNumber,
            texture_size: Number(draft.texture.value),
            mesh_mode: draft.mode.value,
            repair_holes: draft.repair.checked,
        }));
        if (!variants.length) return;
        const token = opening;
        pending = true;
        ui.notice.textContent = 'Queuing variants…';
        update();
        try {
            const response = await request('POST', {variants});
            if (token === opening) {
                ui.notice.textContent = response.paused ? 'Variants queued. Resume processing in Queue.'
                    : 'Variants queued. Progress and results will appear here.';
            }
            await refresh();
        } catch (error) {
            if (token === opening) ui.notice.textContent = error.message;
        } finally {
            if (token === opening) {
                pending = false;
                update();
            }
        }
    };

    return {
        async open(job) {
            if (!job?.can_refinish) return;
            const token = ++opening;
            base = job;
            board = null;
            pending = false;
            drafts = [];
            cards.clear();
            rows.replaceChildren();
            form.replaceChildren();
            results.hidden = true;
            summary.textContent = nameOf(job);
            ui.show();
            ui.notice.textContent = 'Loading the saved finishing source…';
            update();
            try {
                const response = await api(`/api/jobs/${job.id}/finishing-board`);
                if (token !== opening) return;
                board = response;
                const source = getRecords().find(model => model.id === board.source_id);
                const size = board.cache_bytes / 1024 ** 2;
                summary.textContent = `${nameOf(source || job)} · ${size.toFixed(1)} MiB shared cache`;
                recipeForm();
                results.hidden = false;
                ui.notice.textContent = '';
                update();
            } catch (error) {
                if (token === opening) ui.notice.textContent = error.message;
            }
        },
        update,
    };
}
