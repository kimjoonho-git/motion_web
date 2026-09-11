/** 녹화 중 실시간 그래프 · 무엇이 보이는지까지 화면에 적는다 · §6-85
 *
 * 추가 녹화는 **기존 레이어를 보면서** 얹는 일이다 · 그래서 기존 레이어를
 * 점선으로 바탕에 깔고, 지금 녹화되는 것을 실선으로 그 위에 얹는다.
 *
 * 머리말의 "시간" 은 **녹화한 길이**다 · 그래프 축 길이가 아니다. 축 길이를
 * 적었더니 추가 녹화 시작부터 기존 레이어 길이가 떠 있고 그 시간이 지나도록
 * 꿈쩍하지 않아, 멈춘 것처럼 보였다.
 */
export function createMotionStudioRecordingPreview({
  state, el, escapeHtml, layerTracks, compositionTracks, activeMapping,
  renderPlaybackMonitor, drawLayerGraph, periodSec,
}) {
  return function renderRecordingPreview() {
    if (String(state.status?.state || '') !== 'recording') return false;
    const frames = Array.isArray(state.status?.recording_preview_frames)
      ? state.status.recording_preview_frames : [];
    // 추가 녹화는 **기존 레이어를 보면서** 얹는 일이다 · §6-83
    //
    // 녹화가 시작되면 그래프가 새로 기록되는 것만 보여 주고 기존 모션이
    // 사라졌다 · 그러면 언제 얹어야 할지 알 수가 없다. 바탕으로 함께 그린다.
    const overdub = String(state.status?.record_mode || '') === 'overdub';
    const base = overdub
      ? compositionTracks(state.project?.layers || [], activeMapping()?.rows || [])
      : null;
    // 녹화한 길이와 그래프 축 길이는 다르다 · §6-85
    const recordedSec = Math.max(
      Number(state.status?.elapsed_sec) || 0,
      ...frames.map((frame) => Number(frame.time_sec) || 0),
    );
    const duration = Math.max(recordedSec, Number(base?.duration) || 0);
    const previewKey = `${Number(state.status?.recorded_frames || 0)}:${Number(state.status?.recording_preview_stride || 1)}`;
    if (state.recordingPreviewKey === previewKey && state.detailGraph?.recordingPreview) {
      renderPlaybackMonitor(duration);
      return true;
    }
    state.recordingPreviewKey = previewKey;
    const tracks = layerTracks({ frames });
    state.layerDetailMode = 'composition';
    state.selectedLayerId = '';
    state.activeLayerDetailTab = 'graph';
    state.detailGraph = {
      tracks,
      duration,
      enabledLayerCount: Number(state.status?.playback_layer_count || 0),
      compositionMode: true,
      recordingPreview: true,
    };
    if (el.studioLayerDetailName) {
      el.studioLayerDetailName.textContent = overdub ? '추가 녹화 중' : '녹화 중';
    }
    if (el.studioLayerDetailStatus) {
      // 화면에 지금 무엇이 보이는지를 말한다 · §6-85
      //
      // 전에는 "화면 표시 간격 80ms · 원본은 20ms로 기록" 이라고 적혀 있었다 ·
      // 만드는 사람에게나 뜻이 있는 말이고, 정작 점선·실선·띠가 무엇인지는
      // 아무 데도 없었다.
      el.studioLayerDetailStatus.textContent = overdub
        ? '점선 = 재생 중인 기존 레이어 · 실선 = 지금 녹화 중 · 띠 구간은 MIDI 잠김'
        : (tracks.size
          ? '실선 = 지금 녹화 중 · MIDI SELECT 축이 그려집니다'
          : 'MIDI SELECT 후 움직인 축이 그래프에 표시됩니다');
    }
    if (el.studioLayerDetailFrames) {
      el.studioLayerDetailFrames.textContent = `${Number(state.status?.recorded_frames || 0)}개`;
    }
    if (el.studioLayerDetailDuration) {
      // **녹화한 길이**다 · 그래프 축 길이가 아니다 · §6-85
      //
      // 축 길이를 적으니 추가 녹화 시작부터 기존 레이어 길이(8.92초)가 떠 있고
      // 그 시간이 지나도록 꿈쩍하지 않았다 · 멈춘 것처럼 보인다.
      el.studioLayerDetailDuration.textContent = `${recordedSec.toFixed(3)}초`;
    }
    if (el.studioLayerDetailAxisCount) {
      const recorded = (state.status?.recording_motion_ids || []).length;
      el.studioLayerDetailAxisCount.textContent = `${recorded}개`;
    }
    el.studioLayerDetailTabs?.querySelectorAll('[data-studio-layer-detail-tab]').forEach((button) => {
      const active = button.dataset.studioLayerDetailTab === 'graph';
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    el.studioLayerDetail?.querySelectorAll('[data-studio-layer-detail-panel]').forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.studioLayerDetailPanel !== 'graph');
    });
    if (el.studioLayerGraphLegend) {
      // 그려진 것을 빠짐없이 적는다 · 바탕 레이어가 범례에 없으면 점선이
      // 무엇인지 알 수 없다 · §6-85
      const colors = ['#1f6feb', '#d97706', '#16803c', '#a23ab7', '#d33b3b', '#0f8b8d'];
      const entry = (motionId, index, suffix) => (
        `<span><i style="background:${colors[index % colors.length]}"></i>`
        + `${escapeHtml(motionId)}${suffix}</span>`
      );
      el.studioLayerGraphLegend.innerHTML = [
        ...[...(base?.tracks?.keys() || [])].map((id, i) => entry(id, i, ' · 기존(점선)')),
        ...[...tracks.keys()].map((id, i) => entry(id, i, ' · 녹화')),
      ].join('');
    }
    const view = renderPlaybackMonitor(duration);
    // 서버가 솎아 보낸 간격을 함께 넘긴다 · §6-84
    drawLayerGraph(
      tracks, view, base?.tracks || null,
      Math.max(1, Number(state.status?.recording_preview_stride) || 1)
        * periodSec,
    );
    return true;
  };
}
