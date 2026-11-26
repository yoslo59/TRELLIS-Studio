import {textureMethodOf} from './texture-methods.js?v=figure-master-ultratex';

// Seeds vary from object to object. Option order also varies between saved presets and form fields.
function orderedOptions(options) {
  return Object.fromEntries(Object.keys(options).sort()
    .filter(key => !/(^|_)seed$/.test(key))
    .map(key => [key, options[key] && typeof options[key] === 'object'
      ? orderedOptions(options[key]) : options[key]]));
}

function recipeKey(settings) {
  const id = settings.profile?.id || settings.preset;
  if (!id) return null;
  if (settings.workflow_id) {
    if (!settings.workflow_options) return null;
    return JSON.stringify([id, settings.workflow_id, settings.engine, orderedOptions(settings.workflow_options)]);
  }
  const options = settings.generation_options;
  if (!options) return null;
  return JSON.stringify([id, settings.preset, settings.engine || 'trellis2', settings.view_mode || 'single',
    settings.views?.length || 1, settings.resolution, settings.triangles, settings.texture_size,
    textureMethodOf(settings), settings.remove_bg, !!settings.keep_finishing_source,
    settings.master_finishing || '', !!settings.repair_surface, !!settings.hd_reconstruction,
    !!settings.retexture_after_refinement, orderedOptions(options)]);
}

export function createProfileHistory(output) {
  let samples = new Map(), historySignature, selectedKey;
  function duration(job) {
    if (job.stage_timings?.version === 1 && !job.stage_timings.partial &&
        Number.isFinite(job.stage_timings.elapsed_s)) return job.stage_timings.elapsed_s;
    return job.result.seconds ?? job.elapsed;
  }
  function display() {
    const stats = samples.get(selectedKey);
    let label = '';
    if (stats && stats.count >= 3) {
      const minutes = seconds => Math.max(1, Math.round(seconds / 60));
      const low = minutes(stats.shortest), high = minutes(stats.longest);
      label = (low === high ? 'About ' + low : low + '–' + high) +
        ' min observed · ' + stats.count + ' matching runs';
    }
    output.hidden = !label;
    if (output.textContent !== label) output.textContent = label;
  }
  return {
    select(settings) {
      selectedKey = settings && recipeKey(settings);
      display();
    },
    update(jobs) {
      const completed = jobs.filter(({state, operation, settings: s, result}) =>
        state === 'done' && (!operation || operation === 'generation') && result &&
        (!s.source_kind || s.source_kind === 'generation') && !s.parent_id &&
        !['imported','rigged','print','terrain_unity'].includes(s.asset_type));
      const signature = completed.map(j => [j.id, duration(j), j.result.revision].join(':')).join('|');
      if (signature === historySignature) return;
      historySignature = signature;
      samples = new Map();
      for (const job of completed) {
        const seconds = duration(job);
        if (!Number.isFinite(seconds) || seconds <= 0) continue;
        const key = recipeKey(job.settings);
        if (!key) continue;
        const stats = samples.get(key) || {count:0, shortest:Infinity, longest:0};
        stats.count++;
        stats.shortest = Math.min(stats.shortest, seconds);
        stats.longest = Math.max(stats.longest, seconds);
        samples.set(key, stats);
      }
      display();
    },
  };
}
