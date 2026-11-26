import {button, dialog, el, field, input, select} from './workbench-ui.js?v=3.4.0';
import {detailRefinements} from './hd-master-finishing.js?v=detail-refinement-native';
import {createAdvancedControls, optionHelp} from './advanced-controls.js?v=studio-timing';
import {canOpenFinishingBoard} from './card-menu.js?v=texture-variant';

const number = value => Number.isFinite(value) ? value.toLocaleString('en-US') : '—';
const nameOf = job => job?.metadata?.display_name || job?.settings?.name || 'Source model';
const canCompare = job => job?.state === 'done' && job.media?.available !== false;
const labels = {queued: 'Queued', running: 'Finishing', done: 'Completed', error: 'Failed', cancelled: 'Cancelled'};
const textureEndpoint = id => `/api/v1/models/${encodeURIComponent(id)}/multiview-texture-refinement`;
const defaults = [
    {label: 'High-detail copy', triangles: 1200000, bake_normals: false},
    {label: 'Game · 14K triangles', triangles: 14000, bake_normals: true},
    {label: 'Lightweight · 10K triangles', triangles: 10000, bake_normals: true},
];

export function createFinishingBoard({api, getRecords, refresh, compare}) {
    const ui = dialog('finishingBoard', 'Finishing board', '',
        'Queue geometry and texture variants, then compare the results.', 'finishing-board');
    const summary = el('p', 'finishing-summary');
    const texture = el('section', 'finishing-refinement');
    const textureHead = el('div', 'finishing-refinement-head');
    const textureTitle = el('h3', '', 'Texture refinement');
    textureTitle.id = 'finishingBoardTexture';
    textureHead.append(textureTitle);
    texture.setAttribute('aria-labelledby', textureTitle.id);
    const textureHint = el('p', 'hint');
    textureHint.id = 'finishingBoardTextureHint';
    textureHint.setAttribute('role', 'status');
    const textureMethod = select([['multiview', 'Multiview refinement'],
        ['ultratex', 'UltraTex · 2K (local preview)']], 'multiview');
    textureMethod.setAttribute('aria-describedby', textureHint.id);
    const textureMethodField = field('Texture method', textureMethod);
    const textureActions = el('div', 'finishing-refinement-actions');
    const textureQueue = button('Queue texture refinement', 'image', 'secondary');
    textureQueue.setAttribute('aria-describedby', textureHint.id);
    textureActions.append(textureQueue);
    const textureRows = el('div', 'finishing-rows');
    texture.append(textureHead, textureMethodField, textureHint, textureActions, textureRows);
    const selected = el('p', 'finishing-choice');
    const form = el('form', 'finishing-form');
    form.id = 'finishingBoardRecipes';
    const refinement = el('form', 'finishing-refinement');
    refinement.hidden = true;
    refinement.setAttribute('aria-labelledby', 'finishingBoardRefinement');
    const refinementHead = el('div', 'finishing-refinement-head');
    const refinementTitle = el('h3', '', 'Detail refinement');
    refinementTitle.id = 'finishingBoardRefinement';
    const credit = el('a', '', 'Powered by Hi3D 1.0');
    credit.href = 'http://hi3d.ai/';
    credit.target = '_blank';
    credit.rel = 'noopener noreferrer';
    refinementHead.append(refinementTitle, credit);
    const refinementHint = el('p', 'hint');
    const refinementGroup = el('fieldset', 'hd-master-options finishing-refinement-choices');
    refinementGroup.setAttribute('aria-label', 'Detail refinement resolution');
    const refinementChoices = Object.entries(detailRefinements).map(([value, method]) => {
        const control = input('radio', value, {name: 'detail_refinement'});
        const label = el('label', 'hd-master-method');
        const copy = el('span', 'hd-master-method-copy');
        const detail = el('small', '', method.detail);
        detail.id = `board-refinement-${value}`;
        control.setAttribute('aria-label', method.name);
        control.setAttribute('aria-describedby', detail.id);
        copy.append(el('strong', '', method.name), detail);
        label.append(control, copy);
        refinementGroup.append(label);
        return {method, control, detail};
    });
    const refinementSettings = el('fieldset', 'finishing-hi3d-options');
    refinementSettings.hidden = refinementSettings.disabled = true;
    refinementSettings.setAttribute('aria-label', 'Advanced Hi3D settings');
    const retextureField = el('div', 'finishing-retexture');
    retextureField.hidden = true;
    const retexture = input('checkbox', '', {id: 'boardRetextureAfterHi3d', 'aria-describedby': 'boardRetextureStatus'});
    const retextureLabel = el('label', 'check');
    retextureLabel.append(retexture, el('span', '', 'Retexture after Hi3D'));
    const retextureStatus = el('p', 'hint');
    retextureStatus.id = 'boardRetextureStatus';
    retextureStatus.setAttribute('role', 'status');
    retextureField.append(retextureLabel, retextureStatus);
    optionHelp(retextureLabel, 'Retexture after Hi3D',
        'Generates new PBR materials from the saved cutout on the refined mesh. Uses 12 steps, guidance 1 and the selected texture size.');
    const refinementTexture = select([], '');
    const refinementActions = el('div', 'finishing-refinement-actions');
    const refinementQueue = button('Queue refinement', 'layers', 'primary');
    refinementQueue.type = 'submit';
    refinementQueue.disabled = true;
    refinementActions.append(field('Texture size', refinementTexture), refinementQueue);
    refinement.append(refinementHead, refinementHint, refinementGroup, refinementSettings, retextureField, refinementActions);
    const results = el('section', 'finishing-results');
    const rows = el('div', 'finishing-rows');
    results.setAttribute('aria-labelledby', 'finishingBoardResults');
    const heading = el('h3', '', 'Source and variants');
    heading.id = 'finishingBoardResults';
    results.append(heading, selected, rows);
    ui.body.append(summary, texture, refinement, form, results);
    const queue = button('Queue variants', 'layers', 'primary');
    queue.type = 'submit';
    queue.setAttribute('form', form.id);
    const footerHint = el('small', 'wb-footnote', 'Shared cache · Finishing uses the GPU');
    ui.foot.append(footerHint, queue);

    let base = null, board = null, textureInfo = null, drafts = [], opening = 0, pending = false;
    let hi3dControls, hi3dControlsPromise;
    let textureDefault, textureChosen;
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

    function textureVariants() {
        return getRecords().filter(job => job.settings?.source_kind === 'multiview_texture_refinement'
            && job.settings.parent_id === base?.id)
            .sort((left, right) => (right.settings.created || 0) - (left.settings.created || 0));
    }

    function updateTexture() {
        const variants = textureVariants();
        const active = variants.some(job => job.state === 'queued' || job.state === 'running');
        const option = textureInfo?.methods?.[textureMethod.value]
            || (textureMethod.value === 'multiview' ? textureInfo : null);
        textureMethod.disabled = pending || !textureInfo;
        textureQueue.disabled = pending || option?.ready !== true || active;
        let hint = option?.reason || textureInfo?.reason || 'Texture refinement is unavailable for this model.';
        if (option?.ready === true) {
            const size = option.atlas;
            const recipe = [Number.isFinite(option.triangles) ? `${number(option.triangles)} triangles` : '',
                Array.isArray(size) && size.length === 2 ? `${size[0]} × ${size[1]} source atlas` : '']
                .filter(Boolean).join(' · ');
            if (option.native) {
                hint = 'Creates 2K color, metal and roughness maps from the original reference and keeps the existing mesh and UVs.';
            } else {
                hint = textureMethod.value === 'ultratex'
                    ? 'Generates color, metal and roughness from six views on the existing 2K UV atlas. Local non-commercial testing.'
                    : 'Refines the baked color from six generated views and keeps the existing UVs and material maps.';
            }
            if (recipe) hint += ` ${recipe}.`;
        } else if (!textureInfo) {
            hint = 'Checking the saved model and reference…';
        }
        if (textureHint.textContent !== hint) textureHint.textContent = hint;
        textureRows.hidden = !variants.length;
        const revision = JSON.stringify(variants.map(job => [job.id, nameOf(job), job.state, job.stage,
            job.error, job.progress, canCompare(job)]));
        if (revision === textureRows.dataset.revision) return;
        textureRows.dataset.revision = revision;
        textureRows.replaceChildren(...variants.map(variant => {
            const row = el('article', 'finishing-row');
            const copy = el('div');
            const running = variant.state === 'queued' || variant.state === 'running';
            const stage = [labels[variant.state] || variant.state,
                variant.error || (variant.state === 'done' ? '' : variant.stage)].filter(Boolean).join(' · ');
            copy.append(el('strong', '', nameOf(variant)), el('small', variant.state === 'error' ? 'finishing-failure' : '', stage));
            if (running) {
                const progress = el('progress');
                progress.max = 100;
                progress.value = variant.progress || 0;
                progress.setAttribute('aria-label', 'Progress for ' + nameOf(variant));
                copy.append(progress);
            }
            const actions = el('div', 'finishing-row-actions');
            const compareCopy = button('Compare', 'compare', 'secondary', () => {
                const source = getRecords().find(job => job.id === base.id) || base;
                if (!canCompare(source)) return;
                ui.close();
                compare.open(source, variant, {candidates: [source, ...textureVariants().filter(canCompare)],
                    backToBoard: () => { ui.show(); update(); }});
            });
            compareCopy.disabled = !canCompare(variant);
            actions.append(compareCopy);
            row.append(copy, actions);
            return row;
        }));
    }

    async function loadTextureCapability(id, token) {
        try {
            const capability = await api(`${textureEndpoint(id)}?include_methods=true`);
            if (token === opening) {
                textureInfo = capability || {ready: false};
                textureMethod.options[0].hidden = capability?.methods?.ultratex?.native === true && capability.ready !== true;
                textureMethod.value = !capability?.ready && capability?.methods?.ultratex?.ready
                    ? 'ultratex' : 'multiview';
            }
        } catch (error) {
            if (token === opening) textureInfo = {ready: false, reason: error.message};
        }
        if (token === opening) update();
    }

    textureMethod.onchange = updateTexture;
    textureQueue.onclick = async () => {
        if (textureQueue.disabled) return;
        const token = opening;
        pending = true;
        ui.notice.textContent = 'Queueing texture refinement…';
        update();
        try {
            const params = new URLSearchParams({texture_method: textureMethod.value});
            await api(`${textureEndpoint(base.id)}?${params}`, {method: 'POST'});
            if (token === opening) ui.notice.textContent = 'Texture refinement queued. The new copy will appear here.';
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

    function updateQueueButton() {
        const count = drafts.filter(draft => draft.enabled.checked).length;
        queue.disabled = pending || !board || !count;
        queue.lastChild.textContent = count ? `Queue ${count} variant${count === 1 ? '' : 's'}` : 'Queue variants';
        const availableRefinement = !refinement.hidden && refinementChoices.some(({control}) => !control.disabled);
        refinementGroup.disabled = pending;
        refinementTexture.disabled = pending || !availableRefinement;
        const chosenRefinement = refinementChoices.some(({control}) => control.checked && !control.disabled);
        const editable = !!board?.hi3d_options && availableRefinement && chosenRefinement;
        refinementSettings.hidden = !editable;
        refinementSettings.disabled = pending || !editable;
        const showRetexture = !!board?.retexture && availableRefinement && chosenRefinement;
        retextureField.hidden = !showRetexture;
        retexture.disabled = pending || !showRetexture || board.retexture.available !== true;
        refinementQueue.disabled = pending || !board || !availableRefinement || !chosenRefinement
            || !refinementTexture.value || (editable && !hi3dControls?.ready);
    }

    function selectRefinementTexture() {
        if (textureChosen) return;
        const canUse4k = [...refinementTexture.options].some(option => option.value === '4096');
        refinementTexture.value = retexture.checked && canUse4k ? '4096' : textureDefault;
    }

    function configureRefinement() {
        const config = board.master_finishing;
        const support = config?.methods || {};
        refinement.hidden = !refinementChoices.some(({control}) => support[control.value]);
        if (refinement.hidden) return;
        const textureSizes = board.texture_sizes.filter(size => [1024, 2048, 4096].includes(size));
        refinementTexture.replaceChildren(...textureSizes.map(size => new Option(`${size / 1024}K`, size)));
        const size = [base.settings.texture_size, config.default_texture_size, textureSizes[0]]
            .find(size => textureSizes.includes(size));
        textureDefault = String(size || '');
        refinementTexture.value = textureDefault;
        textureChosen = base.settings.source_kind === 'refinish' && !!detailRefinements[base.settings.master_finishing];
        retexture.checked = board.retexture?.available === true && board.retexture.enabled === true;
        retextureStatus.textContent = board.retexture?.available === false
            ? board.retexture.reason || 'The local texturing pipeline is unavailable.' : '';
        retextureStatus.hidden = !retextureStatus.textContent;
        selectRefinementTexture();
        for (const {method, control, detail} of refinementChoices) {
            const capability = support[control.value];
            control.disabled = capability?.available !== true;
            control.checked = false;
            detail.textContent = control.disabled ? (capability?.reason || 'Unavailable for this source.') : method.detail;
        }
        const first = refinementChoices.find(({control}) => !control.disabled);
        if (first) first.control.checked = true;
        credit.hidden = !first;
        const source = Number.isFinite(config.source_resolution) ? ` · ${config.source_resolution}³ native source` : '';
        refinementHint.textContent = first
            ? `TRELLIS.2 single image${source} · Saved cutout`
            : 'Requires a native TRELLIS.2 single-image source at 512³, 1024³ or 1536³ with a saved cutout.';
        refinementActions.hidden = !first;
    }

    async function configureHi3dOptions(token) {
        if (!board.hi3d_options) return;
        if (!hi3dControlsPromise) {
            refinementSettings.replaceChildren();
            hi3dControlsPromise = createAdvancedControls(refinementSettings, {
                groupId: 'hi3d', title: 'Advanced Hi3D', idPrefix: 'board_option_',
            });
        }
        const controls = await hi3dControlsPromise;
        if (token !== opening) return;
        hi3dControls = controls;
        if (!controls.ready) {
            hi3dControlsPromise = null;
            return;
        }
        controls.apply(board.hi3d_options);
        controls.group('hi3d').open = false;
    }

    function recipeForm() {
        form.replaceChildren(el('h3', '', 'New recipes'));
        const textureChoices = board.texture_sizes.map(size => [size, `${size / 1024}K`]);
        const textureDefault = board.texture_sizes.includes(base.settings.texture_size) ? base.settings.texture_size : 2048;
        drafts = defaults.map(recipe => {
            const draft = el('fieldset', 'finishing-recipe');
            draft.append(el('legend', '', recipe.label));
            const enabled = input('checkbox', '');
            enabled.checked = true;
            const triangles = input('number', recipe.triangles, {min: 1000, max: 8000000, step: 1, required: ''});
            const texture = select(textureChoices, textureDefault);
            const modes = [['remesh', 'Remesh and simplify'], ['direct', 'Direct simplification']];
            if (board.repair_surface) modes.unshift(['repair', 'Surface reconstruction']);
            if (board.hd_reconstruction) modes.push(['hd', 'HD Reconstruction (768)']);
            if (board.wtivo_reconstruction) modes.push(['wtivo', 'WTiVo Reconstruction (1024)']);
            const mode = select(modes, board.repair_surface ? 'repair' : 'remesh');
            const repair = input('checkbox', '');
            repair.checked = false;
            const normals = input('checkbox', '');
            normals.checked = Boolean(recipe.bake_normals && board.normal_bake);
            const donorChoices = [['decoded', 'Original decoded surface']];
            if (board.normal_sources?.includes('parent')) donorChoices.push(['parent', 'Selected model · finished HD']);
            const donor = select(donorChoices, 'decoded');
            const donorField = field('Normal map detail source', donor);
            const resolutions = board.remesh_resolutions || [512];
            const grid = select(resolutions.map(size => [size, String(size)]), resolutions.includes(1024) ? 1024 : 512);
            const sampling = select((board.texture_sampling_modes || ['trilinear']).map(value =>
                [value, value === 'nearest' ? 'Nearest voxel · experimental' : 'Interpolated voxels']), 'trilinear');
            const gridField = field('Reconstruction resolution', grid);
            const repairField = field('Fill small holes', repair);
            const samplingField = field('Texture voxel sampling', sampling);
            const toggle = () => {
                const wtivo = mode.value === 'wtivo';
                const usesGrid = !['direct', 'hd', 'wtivo'].includes(mode.value);
                for (const control of [triangles, texture, mode]) control.disabled = !enabled.checked;
                normals.disabled = !enabled.checked || !board.normal_bake;
                donor.disabled = !enabled.checked || !normals.checked || !board.normal_bake;
                donorField.hidden = !normals.checked;
                repair.disabled = !enabled.checked || wtivo || mode.value === 'repair' || mode.value === 'hd';
                repairField.hidden = wtivo;
                sampling.disabled = !enabled.checked || wtivo;
                samplingField.hidden = wtivo;
                grid.disabled = !enabled.checked || !usesGrid;
                gridField.hidden = !usesGrid;
                updateQueueButton();
            };
            enabled.addEventListener('change', toggle);
            mode.addEventListener('change', () => {
                if (mode.value === 'hd') triangles.value = 1200000;
                toggle();
            });
            normals.addEventListener('change', toggle);
            draft.append(field('Include recipe', enabled), field('Target triangles', triangles),
                field('Texture size', texture), field('Finishing method', mode), gridField,
                repairField, field('Transfer surface detail to a normal map', normals), donorField, samplingField);
            form.append(draft);
            toggle();
            return {enabled, triangles, texture, mode, repair, normals, donor, grid, sampling};
        });
        form.append(el('p', 'hint', 'The saved master stays intact. Each recipe creates a separate copy using the original geometry and materials. A normal map carries surface relief, but cannot restore a lost silhouette or finger spacing.'));
        form.append(el('p', 'hint', 'These are comparison recipes, not validated game assets. Surface reconstruction includes hole repair and may close intended openings or soften detail. Higher reconstruction resolution uses more GPU memory. Nearest-voxel sampling can reveal blocks; it does not generate new texture detail.'));
        if (!board.normal_sources?.includes('parent')) form.append(el('p', 'hint', 'To bake from a finished HD model, first create and open a new high-detail copy. Older exports lack the alignment information required for this transfer.'));
        if (!board.normal_bake) form.append(el('p', 'hint', 'Install Blender to enable normal-map baking.'));
    }

    function rowFor(id) {
        if (cards.has(id)) return cards.get(id);
        const root = el('article', 'finishing-row');
        const title = el('strong');
        const metrics = el('p');
        const stage = el('small');
        const progress = el('progress');
        progress.max = 100;
        const hi3dSummary = el('small');
        const hi3dSettings = el('details', 'finishing-variant-options');
        const settingsList = el('dl');
        hi3dSettings.append(el('summary', '', 'Hi3D settings'), settingsList);
        const text = el('div');
        text.append(title, metrics, stage, progress, hi3dSummary, hi3dSettings);
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
        const card = {root, title, metrics, stage, progress, compareButton, keep, hi3dSummary, hi3dSettings, settingsList};
        cards.set(id, card);
        return card;
    }

    function renderHi3dRecipe(card, options) {
        const scheduler = options?.hi3d_scheduler;
        const summary = [
            typeof scheduler === 'string' && scheduler ? scheduler[0].toUpperCase() + scheduler.slice(1) : '',
            Number.isFinite(options?.hi3d_steps) ? `${options.hi3d_steps} steps per pass` : '',
            Number.isFinite(options?.hi3d_seed) ? `seed ${options.hi3d_seed}` : '',
        ].filter(Boolean).join(' · ');
        card.hi3dSummary.hidden = !summary;
        card.hi3dSummary.textContent = summary;
        const values = (hi3dControls?.fields || []).filter(({key}) => options?.[key] != null)
            .map(({key, label, choices}) => {
                const value = options[key];
                const display = choices?.find(choice => choice.value === value)?.label
                    ?? (typeof value === 'boolean' ? (value ? 'On' : 'Off') : String(value));
                return [label, display];
            });
        card.hi3dSettings.hidden = !values.length;
        const revision = JSON.stringify(values);
        if (card.hi3dRevision !== revision) {
            card.settingsList.replaceChildren(...values.flatMap(([label, value]) => [el('dt', '', label), el('dd', '', value)]));
            card.hi3dRevision = revision;
        }
    }

    function update() {
        if (!ui.element.open) return;
        updateQueueButton();
        updateTexture();
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
            const method = isSource ? 'Original' : detailRefinements[job.settings?.master_finishing]?.name
                || (job.settings?.wtivo_reconstruction ? 'WTiVo 1024'
                    : job.generation_options?.mesh_mode === 'direct' ? 'Direct' : 'Remesh');
            const pieces = [method];
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
            if (!isSource && result.neural_inferences === 0) pieces.push('Cached source');
            const retextureResult = result.finishing?.retexture_after_refinement;
            if (retextureResult?.state === 'done') pieces.push('Retextured after Hi3D');
            if (retextureResult?.state === 'failed') pieces.push('Source materials baked after the texture pass failed');
            card.title.textContent = nameOf(job);
            card.metrics.textContent = pieces.join(' · ');
            renderHi3dRecipe(card, !isSource && detailRefinements[job.settings?.master_finishing]
                ? job.settings.generation_options : null);
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

    refinement.onchange = updateQueueButton;
    refinementTexture.onchange = () => { textureChosen = true; };
    retexture.onchange = selectRefinementTexture;
    refinementSettings.addEventListener('invalid', () => hi3dControls?.openGroup('hi3d'), true);
    refinement.onsubmit = async event => {
        event.preventDefault();
        if (refinementQueue.disabled) return;
        const choice = refinementChoices.find(({control}) => control.checked && !control.disabled);
        if (!choice) return;
        const payload = {
            master_finishing: choice.control.value,
            texture_size: Number(refinementTexture.value),
        };
        if (!refinementSettings.disabled) payload.hi3d_options = hi3dControls.read();
        if (board.retexture) payload.retexture_after_refinement = !retexture.disabled && retexture.checked;
        const token = opening;
        pending = true;
        ui.notice.textContent = 'Queueing detail refinement…';
        update();
        try {
            const response = await api(`/api/jobs/${base.id}/refinish`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            if (token === opening) {
                ui.notice.textContent = response.paused ? 'Refinement queued. Resume processing in Queue.'
                    : 'Refinement queued. Progress and results will appear here.';
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

    form.onsubmit = async event => {
        event.preventDefault();
        if (!board || pending || !form.reportValidity()) return;
        const variants = drafts.filter(draft => draft.enabled.checked).map(draft => {
            const shared = {
                triangles: draft.triangles.valueAsNumber,
                texture_size: Number(draft.texture.value),
                bake_normals: draft.normals.checked,
                normal_source: draft.normals.checked ? draft.donor.value : 'decoded',
            };
            if (draft.mode.value === 'wtivo') return {...shared, wtivo_reconstruction: true};
            return {
                ...shared,
                mesh_mode: draft.mode.value === 'direct' ? 'direct' : 'remesh',
                repair_surface: draft.mode.value === 'repair',
                ...(draft.mode.value === 'hd' ? {hd_reconstruction: true} : {}),
                repair_holes: draft.repair.checked,
                pre_simplify: false,
                remesh_resolution: Number(draft.grid.value),
                texture_sampling: draft.sampling.value,
            };
        });
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
        canOpen: canOpenFinishingBoard,
        async open(job) {
            if (!canOpenFinishingBoard(job)) return;
            const token = ++opening;
            base = job;
            board = null;
            textureInfo = null;
            pending = false;
            drafts = [];
            hi3dControls = null;
            cards.clear();
            rows.replaceChildren();
            form.replaceChildren();
            ui.foot.hidden = true;
            refinement.hidden = true;
            results.hidden = true;
            summary.textContent = nameOf(job);
            ui.show();
            ui.notice.textContent = job.can_refinish ? 'Loading the saved finishing source…' : '';
            update();
            const textureCapability = loadTextureCapability(job.id, token);
            if (!job.can_refinish) {
                await textureCapability;
                return;
            }
            try {
                const response = await api(`/api/jobs/${job.id}/finishing-board`);
                if (token !== opening) return;
                board = response;
                const source = getRecords().find(model => model.id === board.source_id);
                const size = board.cache_bytes / 1024 ** 2;
                summary.textContent = `${nameOf(source || job)} · ${size.toFixed(1)} MiB shared cache`;
                configureRefinement();
                await configureHi3dOptions(token);
                if (token !== opening) return;
                recipeForm();
                ui.foot.hidden = false;
                results.hidden = false;
                ui.notice.textContent = '';
                update();
            } catch (error) {
                if (token === opening) ui.notice.textContent = error.message;
            }
            await textureCapability;
        },
        update,
    };
}
