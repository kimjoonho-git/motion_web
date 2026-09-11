import {
  drawMotionStudioLayerGraph,
  motionStudioGraphTimeSpan,
} from './motion_studio_graph.js?v=20260911113528';

/** 그래프 한 장을 그린다 · 시간축을 **한 번만** 셈해 모두가 같은 값을 쓴다 · §6-83
 *
 * 캔버스와 플레이헤드가 각자 셈하던 때에는 서로 어긋났다 · 캔버스는 5초씩
 * 늘어나는데 플레이헤드는 녹화 시각에 맞춰 놓이니, 그릴 때마다 재생 표시가
 * 튀었다. 셈한 값을 `detailGraph.timeSpan` 에 적어 두고 플레이헤드가 그걸 읽는다.
 *
 * `baseTracks` 는 추가 녹화 중 **지금 재생되고 있는 기존 레이어**다 · 점선으로
 * 옅게 깔린다. 녹화가 시작되면 새로 기록되는 것만 남고 기존 모션이 사라져,
 * 언제 얹어야 할지 알 수 없던 것을 고친 것이다.
 *
 * `sampleIntervalSec` 는 서버가 솎아 보낸 미리보기의 점 간격이다 · 이걸 모르면
 * 솎아낸 간격을 "빈 구간" 으로 보고 선을 전부 끊는다 · §6-84
 */
export function createMotionStudioGraphPainter({ state, el, updatePlayhead }) {
  function trackDuration(tracks) {
    let last = 0;
    for (const points of tracks?.values() || []) {
      const point = points[points.length - 1];
      last = Math.max(last, Number(point?.timeSec) || 0);
    }
    return last;
  }

  return function paint(tracks, playback, baseTracks = null, sampleIntervalSec = 0) {
    const dataDuration = Math.max(
      Number(state.detailGraph?.duration) || 0,
      trackDuration(tracks),
      trackDuration(baseTracks),
    );
    const timeSpan = motionStudioGraphTimeSpan(dataDuration, playback);
    if (state.detailGraph) state.detailGraph.timeSpan = timeSpan;
    return drawMotionStudioLayerGraph({
      canvas: el.studioLayerGraph,
      playhead: el.studioLayerPlayhead,
      tracks,
      baseTracks,
      playback,
      timeSpan,
      // 서버가 솎아 보낸 간격 · 이걸 모르면 선이 낱개로 쪼개진다 · §6-84
      sampleIntervalSec,
      // 추가 녹화 중 재생이 쥔 구간 · 서버가 쥔 그대로 그린다 · §6-79
      ownedSpans: state.status?.overdub_spans || null,
      updatePlayhead,
      devicePixelRatio: globalThis.devicePixelRatio || 1,
    });
  };
}
