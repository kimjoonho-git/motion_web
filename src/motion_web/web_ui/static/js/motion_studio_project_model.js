export function applyMotionStudioProjectPatch(project, patch) {
  if (!patch || typeof patch !== 'object') return project || null;
  const metadata = (
    patch.metadata && typeof patch.metadata === 'object'
      ? patch.metadata : {}
  );
  const sameProject = (
    project
    && String(project.project_id || '') === String(metadata.project_id || '')
  );
  const existingLayers = sameProject && Array.isArray(project.layers)
    ? project.layers : [];
  const deleted = new Set(
    (patch.delete_layer_ids || []).map((value) => String(value)),
  );
  const byId = new Map(
    existingLayers
      .filter((layer) => layer && !deleted.has(String(layer.layer_id || '')))
      .map((layer) => [String(layer.layer_id || ''), layer]),
  );
  for (const layer of patch.upsert_layers || []) {
    if (!layer || typeof layer !== 'object') continue;
    byId.set(String(layer.layer_id || ''), layer);
  }
  const layers = [];
  for (const value of patch.layer_order || []) {
    const layerId = String(value || '');
    if (!byId.has(layerId)) continue;
    layers.push(byId.get(layerId));
    byId.delete(layerId);
  }
  layers.push(...byId.values());
  return {
    ...(sameProject ? project : {}),
    ...metadata,
    layers,
  };
}

export function motionStudioSetLayerEnabled(project, layerId, enabled) {
  if (!project || !Array.isArray(project.layers)) return project || null;
  const targetId = String(layerId || '');
  let changed = false;
  const layers = project.layers.map((layer) => {
    if (String(layer?.layer_id || '') !== targetId) return layer;
    const nextEnabled = Boolean(enabled);
    if ((layer.enabled !== false) === nextEnabled) return layer;
    changed = true;
    return { ...layer, enabled: nextEnabled };
  });
  return changed ? { ...project, layers } : project;
}

export function motionStudioEditorValidationProject(
  project,
  layer,
  extraMotionIds = [],
) {
  const selected = new Set([
    ...motionStudioLayerMotionIds(layer),
    ...extraMotionIds.map((value) => String(value || '')).filter(Boolean),
  ]);
  const source = project || {};
  return {
    ...source,
    layers: (source.layers || []).map((item) => {
      if (item.layer_id === layer?.layer_id) {
        return {
          layer_id: item.layer_id,
          name: item.name,
          enabled: item.enabled,
          locked: item.locked,
          frames: [],
        };
      }
      return {
        layer_id: item.layer_id,
        name: item.name,
        enabled: item.enabled,
        locked: item.locked,
        frames: (item.frames || []).flatMap((frame) => {
          const values = Object.fromEntries(
            Object.entries(frame.values || {}).filter(
              ([motionId]) => selected.has(motionId),
            ),
          );
          return Object.keys(values).length ? [{ ...frame, values }] : [];
        }),
      };
    }),
  };
}

export function motionStudioMergePreviewProject(project, layerIds) {
  const selected = new Set(layerIds.map((value) => String(value || '')));
  return {
    ...(project || {}),
    layers: (project?.layers || []).filter(
      (layer) => selected.has(String(layer.layer_id || '')),
    ),
  };
}

export function motionStudioLayerDuration(layer) {
  return Math.max(
    0,
    ...(layer?.frames || [])
      .map((frame) => Number(frame.time_sec))
      .filter((timeSec) => Number.isFinite(timeSec) && timeSec >= 0),
  );
}

export function motionStudioLayerDataEqual(first, second) {
  return JSON.stringify({
    frames: first?.frames || [],
    point_curves: first?.point_curves || [],
  }) === JSON.stringify({
    frames: second?.frames || [],
    point_curves: second?.point_curves || [],
  });
}

export function motionStudioLayerMotionIds(layer) {
  return [...new Set((layer?.frames || []).flatMap(
    (frame) => Object.keys(frame?.values || {}),
  ))];
}

export function motionStudioCanCreatePointCurve(layer, motionId) {
  const targetId = String(motionId || '').trim();
  if (!targetId) return false;
  if ((layer?.point_curves || []).some(
    (curve) => String(curve?.motion_id || '') === targetId,
  )) return false;
  const values = (layer?.frames || [])
    .filter((frame) => Object.hasOwn(frame?.values || {}, targetId))
    .map((frame) => Number(frame.values[targetId]));
  if (!values.length || values.some((value) => !Number.isFinite(value))) return false;
  return Math.max(...values) - Math.min(...values) < 1e-9;
}

export function motionStudioPointCurveIsApplied(layer, curveId) {
  const targetId = String(curveId || '');
  return Boolean(targetId) && (layer?.point_curves || []).some(
    (curve) => String(curve?.curve_id || '') === targetId,
  );
}

export function resolveMotionStudioSelectedLayerId(layers, selectedLayerId = '') {
  const available = Array.isArray(layers) ? layers : [];
  if (available.some((layer) => layer.layer_id === selectedLayerId)) return selectedLayerId;
  return String(available[0]?.layer_id || '');
}
