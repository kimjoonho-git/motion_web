import { MOTION_STUDIO_PERIOD_SEC } from './motion_studio_constants.js';

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
  const sampleCount = Math.max(0, Math.ceil(duration / MOTION_STUDIO_PERIOD_SEC));
  const tracks = new Map([...motionIds].map((motionId) => [motionId, []]));
  const lastValues = new Map([...motionIds].map((motionId) => {
    const firstPoint = firstPoints.get(motionId);
    return [motionId, manualInitialValues.has(motionId)
      ? manualInitialValues.get(motionId) : Number(firstPoint?.value || 0)];
  }));
  for (let index = 1; index <= sampleCount; index += 1) {
    const timeSec = Number((index * MOTION_STUDIO_PERIOD_SEC).toFixed(9));
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

/** 곡선을 조각으로 나눈다 · 값이 비는 구간에서 선을 끊는다.
 *
 * `gapSec` 는 **받은 데이터의 실제 간격**을 따라야 한다 · §6-84
 *
 * 녹화 중 미리보기는 서버가 240점까지 솎아서 보낸다 · 그래서 점 간격이 20ms 가
 * 아니라 stride 배가 된다. 임계를 20ms 에 묶어 두면 솎아낸 간격이 전부 "빈 구간"
 * 으로 보여 모든 점이 낱개로 쪼개지고 **선이 하나도 안 그려진다**.
 *
 * 녹화 4.82초(241프레임)부터 그랬다 · 추가 녹화는 기존 레이어가 끝난 뒤부터
 * 기록하므로 그 시점엔 이미 솎아내는 중이라, 새로 녹화한 것이 처음부터 끝까지
 * 안 보였다.
 */
export function motionStudioDisplaySegments(
  points, maximumPoints = 1200, gapSec = 0.031,
) {
  const source = Array.isArray(points) ? points : [];
  if (!source.length) return [];
  const limit = Math.max(0.031, Number(gapSec) || 0);
  const segments = [];
  let current = [];
  for (const point of source) {
    const previous = current[current.length - 1];
    if (previous && Number(point.timeSec) - Number(previous.timeSec) > limit) {
      segments.push(current);
      current = [];
    }
    current.push(point);
  }
  if (current.length) segments.push(current);
  const total = source.length;
  return segments.map((segment) => {
    const budget = Math.max(
      4,
      Math.floor((Math.max(4, Number(maximumPoints) || 1200) * segment.length) / total),
    );
    if (segment.length <= budget) return segment;
    const bucketSize = Math.max(1, Math.ceil(segment.length / Math.max(1, budget / 4)));
    const selected = [];
    for (let start = 0; start < segment.length; start += bucketSize) {
      const end = Math.min(segment.length, start + bucketSize);
      let minimum = start;
      let maximum = start;
      for (let index = start + 1; index < end; index += 1) {
        if (segment[index].value < segment[minimum].value) minimum = index;
        if (segment[index].value > segment[maximum].value) maximum = index;
      }
      const indices = [...new Set([start, minimum, maximum, end - 1])]
        .sort((left, right) => left - right);
      selected.push(...indices.map((index) => segment[index]));
    }
    return selected;
  });
}

export function motionStudioVisiblePoints(points, startTime, endTime) {
  const source = Array.isArray(points) ? points : [];
  if (!source.length) return [];
  const start = Number(startTime);
  const end = Number(endTime);
  let low = 0;
  let high = source.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(source[middle].timeSec) < start) low = middle + 1;
    else high = middle;
  }
  const first = Math.max(0, low - 1);
  low = first;
  high = source.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(source[middle].timeSec) <= end) low = middle + 1;
    else high = middle;
  }
  return source.slice(first, Math.min(source.length, low + 1));
}

export function motionStudioEditorIssueTimes(validation = {}, selectedMotionIds = []) {
  const selected = new Set(selectedMotionIds.map(String));
  return [
    ...(validation?.conflicts || []).map((item) => Number(item.start_sec)),
    ...(validation?.range_warnings || [])
      .filter((item) => selected.has(String(item.motion_id || '')))
      .map((item) => Number(item.time_sec)),
  ].filter(Number.isFinite);
}
