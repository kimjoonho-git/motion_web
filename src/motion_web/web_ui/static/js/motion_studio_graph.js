export function motionStudioLayerTracks(layer) {
  const tracks = new Map();
  for (const frame of layer?.frames || []) {
    const timeSec = Number(frame.time_sec || 0);
    for (const [motionId, rawValue] of Object.entries(frame.values || {})) {
      const value = Number(rawValue);
      if (!Number.isFinite(timeSec) || !Number.isFinite(value)) continue;
      if (!tracks.has(motionId)) tracks.set(motionId, []);
      tracks.get(motionId).push({ timeSec, value });
    }
  }
  for (const points of tracks.values()) {
    points.sort((left, right) => left.timeSec - right.timeSec);
  }
  return tracks;
}

export function motionStudioSampleTrack(points, timeSec) {
  if (!points?.length) return null;
  let low = 0; let high = points.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (points[middle].timeSec < timeSec) low = middle + 1;
    else high = middle;
  }
  const point = points[low];
  if (point && Math.abs(point.timeSec - timeSec) < 1e-7) return point.value;
  const previous = points[low - 1];
  if (!point || !previous) return null;
  const span = point.timeSec - previous.timeSec;
  if (span > 0.031 || span <= 0) return null;
  const ratio = (timeSec - previous.timeSec) / span;
  return previous.value + ((point.value - previous.value) * ratio);
}

export function motionStudioCompositionTracks(layers, mappingRows = []) {
  const enabledLayers = layers.filter((layer) => layer.enabled !== false);
  const sources = enabledLayers.map((layer) => motionStudioLayerTracks(layer));
  const motionIds = new Set(sources.flatMap((tracks) => [...tracks.keys()]));
  const manualInitialValues = new Map(mappingRows.filter((row) => (
    String(row.initial_mode || 'first_frame') === 'manual'
  )).map((row) => [String(row.motion_id), Number(row.initial_motion_position_deg || 0)]));
  const firstPoints = new Map();
  for (const source of sources) {
    for (const [motionId, points] of source.entries()) {
      if (!points.length) continue;
      const current = firstPoints.get(motionId);
      if (!current || points[0].timeSec < current.timeSec) firstPoints.set(motionId, points[0]);
    }
  }
  const duration = Math.max(0, ...enabledLayers.flatMap((layer) => (
    (layer.frames || []).map((frame) => Number(frame.time_sec || 0))
  )));
  const sampleCount = Math.max(0, Math.ceil(duration / 0.02));
  const tracks = new Map([...motionIds].map((motionId) => [motionId, []]));
  const lastValues = new Map([...motionIds].map((motionId) => {
    const firstPoint = firstPoints.get(motionId);
    return [motionId, manualInitialValues.has(motionId)
      ? manualInitialValues.get(motionId) : Number(firstPoint?.value || 0)];
  }));
  for (let index = 1; index <= sampleCount; index += 1) {
    const timeSec = Number((index * 0.02).toFixed(9));
    for (const motionId of motionIds) {
      let value = null;
      for (const source of sources) {
        const candidate = motionStudioSampleTrack(source.get(motionId), timeSec);
        if (candidate !== null) value = candidate;
      }
      const firstPoint = firstPoints.get(motionId);
      if (value === null && firstPoint && timeSec < firstPoint.timeSec) {
        value = manualInitialValues.has(motionId)
          ? manualInitialValues.get(motionId)
          : firstPoint.value;
      }
      if (value === null) value = lastValues.get(motionId) ?? 0;
      lastValues.set(motionId, value);
      tracks.get(motionId).push({ timeSec, value });
    }
  }
  return { tracks, duration, sampleCount, enabledLayers };
}
