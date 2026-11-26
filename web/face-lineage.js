export function refinedFaceLabel(settings) {
  if (settings?.source_kind !== 'face_refinement') return '';
  const value = settings.face_refinement?.strength;
  return Number.isFinite(value) && value >= 0 && value <= 1
    ? 'Refined face · ' + Math.round(100 * value) + '%' : 'Refined face';
}

export function faceSource(settings, records) {
  if (settings?.source_kind !== 'face_refinement' || !settings.parent_id) return null;
  return records.find(record => record.id === settings.parent_id && record.state === 'done') || null;
}
