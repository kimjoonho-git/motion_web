import { MOTION_STUDIO_PERIOD_SEC } from './motion_studio_constants.js?v=20260803-studio-structure-2';

export function motionStudioPlaybackView({
  status = {},
  clock = null,
  duration = 0,
  now = () => performance.now(),
  timeText,
}) {
  const runtimeState = String(status?.state || 'idle');
  const phase = String(status?.phase || runtimeState);
  const initializing = runtimeState === 'initializing';
  const playing = runtimeState === 'playing';
  const recording = runtimeState === 'recording';
  const stopping = runtimeState === 'stopping';
  const progress = status?.runtime_progress || {};
  const initializationProgress = status?.initialization_progress || {};
  const sourceElapsed = playing || stopping
    ? Math.max(0, Number(progress.elapsed_sec ?? status?.elapsed_sec) || 0)
    : recording ? Math.max(0, Number(status?.elapsed_sec) || 0) : 0;
  const elapsed = clock && clock.runtimeState === runtimeState
    ? Math.max(0, clock.sourceElapsed + ((now() - clock.receivedAt) / 1000))
    : sourceElapsed;
  const total = recording
    ? Math.max(elapsed, Number(duration) || 0)
    : Math.max(0, Number(status?.playback_duration_sec) || Number(duration) || 0);
  let label = '대기'; let chip = 'off'; let displayState = 'idle';
  if (runtimeState === 'error') {
    label = '오류'; chip = 'danger'; displayState = 'error';
  } else if (initializing && phase === 'countdown') {
    label = '재생 준비'; chip = 'warn'; displayState = 'countdown';
  } else if (initializing) {
    label = '초기 위치 이동'; chip = 'warn'; displayState = 'initializing';
  } else if (playing) {
    label = '모션 재생'; chip = 'on'; displayState = 'playing';
  } else if (stopping) {
    label = '정지 중'; chip = 'warn'; displayState = 'stopping';
  } else if (recording) {
    label = '녹화 중'; chip = 'on'; displayState = 'recording';
  }
  const initElapsed = Math.max(0, Number(initializationProgress.elapsed_sec) || 0);
  const initDuration = Math.max(0, Number(initializationProgress.duration_sec) || 0);
  return {
    runtimeState, displayState, label, chip, elapsed, total,
    ratio: total > 0 ? Math.min(1, elapsed / total) : 0,
    showPlayhead: initializing || playing || stopping || recording,
    playheadTime: playing || stopping || recording ? Math.min(total, elapsed) : 0,
    message: initializing && phase !== 'countdown' && initDuration > 0
      ? `초기 위치 이동 ${timeText(initElapsed)} / ${timeText(initDuration)} · 완료 후 3초 준비 뒤 재생합니다.`
      : String(status?.message || '합성 미리보기를 시작하면 진행 위치가 그래프에 표시됩니다.'),
  };
}

export function syncMotionStudioPlaybackClock(state, currentTime) {
  const runtimeState = String(state.status?.state || 'idle');
  const progress = state.status?.runtime_progress || {};
  const sourceElapsed = runtimeState === 'playing' || runtimeState === 'stopping'
    ? Math.max(0, Number(progress.elapsed_sec ?? state.status?.elapsed_sec) || 0)
    : runtimeState === 'recording'
      ? Math.max(0, Number(state.status?.elapsed_sec) || 0)
      : 0;
  const running = ['playing', 'recording'].includes(runtimeState);
  const previous = state.playbackClock;
  if (!running) {
    state.playbackClock = null;
    return null;
  }
  if (
    !previous
    || previous.runtimeState !== runtimeState
    || Math.abs(previous.sourceElapsed - sourceElapsed) > 0.0005
  ) {
    const previousEstimate = previous && previous.runtimeState === runtimeState
      ? previous.sourceElapsed + ((currentTime - previous.receivedAt) / 1000)
      : sourceElapsed;
    state.playbackClock = {
      runtimeState,
      sourceElapsed: Math.max(sourceElapsed, previousEstimate),
      receivedAt: currentTime,
    };
  }
  return state.playbackClock;
}

export function createMotionStudioPlaybackController({
  state,
  el,
  timeText,
  now = () => performance.now(),
  requestFrame = (callback) => requestAnimationFrame(callback),
  cancelFrame = (frameId) => cancelAnimationFrame(frameId),
}) {
  function view(duration = 0) {
    return motionStudioPlaybackView({
      status: state.status,
      clock: state.playbackClock,
      duration,
      now,
      timeText,
    });
  }

  function syncClock() {
    return syncMotionStudioPlaybackClock(state, now());
  }

  function updatePlayhead(playback) {
    const playhead = el.studioLayerPlayhead;
    const canvas = el.studioLayerGraph;
    if (!playhead || !canvas || !playback.showPlayhead || !state.detailGraph?.duration) {
      playhead?.classList.add('hidden');
      return;
    }
    const width = canvas.getBoundingClientRect().width || canvas.clientWidth || 0;
    if (width <= 70) return;
    const graphDuration = Math.max(
      MOTION_STUDIO_PERIOD_SEC,
      Number(state.detailGraph.duration) || MOTION_STUDIO_PERIOD_SEC,
    );
    const ratio = Math.min(1, Math.max(0, Number(playback.playheadTime) / graphDuration));
    playhead.style.left = `${52 + (ratio * (width - 70))}px`;
    playhead.classList.toggle(
      'initializing', ['initializing', 'countdown'].includes(playback.displayState),
    );
    playhead.classList.remove('hidden');
    const label = playhead.querySelector('span');
    if (label) {
      label.textContent = playback.displayState === 'initializing'
        ? '시작 위치' : timeText(playback.playheadTime);
    }
  }

  function renderMonitor(duration = state.detailGraph?.duration || 0) {
    const playback = view(duration);
    if (el.studioPlaybackMonitor) el.studioPlaybackMonitor.dataset.state = playback.displayState;
    if (el.studioPlaybackPhase) {
      el.studioPlaybackPhase.className = `status-chip ${playback.chip}`;
      el.studioPlaybackPhase.textContent = playback.label;
    }
    if (el.studioPlaybackTime) {
      el.studioPlaybackTime.textContent = `${timeText(playback.elapsed)} / ${timeText(playback.total)}`;
    }
    if (el.studioPlaybackLayerCount) {
      const count = state.detailGraph?.enabledLayerCount
        ?? Number(state.status?.playback_layer_count || 0);
      el.studioPlaybackLayerCount.textContent = `재생 선택 ${count}개 · 합성 그래프`;
    }
    if (el.studioPlaybackProgressBar) {
      el.studioPlaybackProgressBar.style.width = `${(playback.ratio * 100).toFixed(2)}%`;
    }
    if (el.studioPlaybackMessage) el.studioPlaybackMessage.textContent = playback.message;
    if (el.studioPlaybackQuickPhase) {
      el.studioPlaybackQuickPhase.className = `status-chip ${playback.chip}`;
      el.studioPlaybackQuickPhase.textContent = playback.label;
    }
    if (el.studioPlaybackQuickTime) {
      el.studioPlaybackQuickTime.textContent = `${timeText(playback.elapsed)} / ${timeText(playback.total)}`;
    }
    if (el.studioPlaybackQuickMessage) el.studioPlaybackQuickMessage.textContent = playback.message;
    return playback;
  }

  function animate() {
    if (state.playbackAnimationFrame) return;
    const tick = () => {
      state.playbackAnimationFrame = 0;
      const runtimeState = String(state.status?.state || 'idle');
      const playback = renderMonitor();
      updatePlayhead(playback);
      if (['playing', 'recording'].includes(runtimeState)) {
        state.playbackAnimationFrame = requestFrame(tick);
      }
    };
    state.playbackAnimationFrame = requestFrame(tick);
  }

  function cancel() {
    if (!state.playbackAnimationFrame) return;
    cancelFrame(state.playbackAnimationFrame);
    state.playbackAnimationFrame = 0;
  }

  return { animate, cancel, renderMonitor, syncClock, updatePlayhead, view };
}
