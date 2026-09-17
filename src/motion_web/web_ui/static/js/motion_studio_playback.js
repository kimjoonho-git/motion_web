import { MOTION_STUDIO_PERIOD_SEC } from './motion_studio_constants.js';

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
  // 시간은 테이크 하나에서 온다 · §6-81
  //
  // 전에는 `runtime_progress` · `initialization_progress` · `elapsed_sec` ·
  // `playback_duration_sec` 중 **어느 게 진짜인지가 상태에 달려 있었고**,
  // 여기서 그 퍼즐을 매번 다시 풀었다 · 녹화 중의 축 길이를 놓쳐 플레이헤드가
  // 멈춘 것이 그 대가였다 · §6-79
  const sourceElapsed = Math.max(0, Number(status?.elapsed_sec) || 0);
  const elapsed = clock && clock.runtimeState === runtimeState
    ? Math.max(0, clock.sourceElapsed + ((now() - clock.receivedAt) / 1000))
    : sourceElapsed;
  const total = Math.max(
    recording ? elapsed : 0,
    Number(status?.total_sec) || Number(duration) || 0,
  );
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
  // 단계 진행은 테이크 시계와 별개다 · 초기 이동 3.2 / 5.0 초 같은 것
  const phaseElapsed = Math.max(0, Number(status?.phase_elapsed_sec) || 0);
  const phaseTotal = Math.max(0, Number(status?.phase_total_sec) || 0);
  return {
    runtimeState, displayState, label, chip, elapsed, total,
    ratio: total > 0 ? Math.min(1, elapsed / total) : 0,
    showPlayhead: initializing || playing || stopping || recording,
    playheadTime: playing || stopping || recording ? Math.min(total, elapsed) : 0,
    message: initializing && phase !== 'countdown' && phaseTotal > 0
      ? `초기 위치 이동 ${timeText(phaseElapsed)} / ${timeText(phaseTotal)} · 완료 후 3초 준비 뒤 재생합니다.`
      : String(status?.message || '레이어 재생을 시작하면 진행 위치가 그래프에 표시됩니다.'),
  };
}

export function syncMotionStudioPlaybackClock(state, currentTime) {
  const runtimeState = String(state.status?.state || 'idle');
  const sourceElapsed = Math.max(0, Number(state.status?.elapsed_sec) || 0);
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


/** 추가 녹화 안내 한 줄 · 지금 어느 축이 잠겼고 언제 풀리는지 · §6-79
 *
 * 그래프의 잠금 띠는 한눈에 보이지만, 페이더를 잡고 있는 사람은 화면을 계속
 * 보고 있지 않다 · 글로도 알 수 있어야 한다.
 *
 * 축이 여럿이면 **일부만 잠긴다** · "지금 녹화가 되는가" 가 아니라 "어느 축이
 * 되는가" 를 말해야 한다.
 */
export function motionStudioOverdubHint(ownedSpans, elapsedSec) {
  const entries = Object.entries(ownedSpans || {}).filter(
    ([, spans]) => Array.isArray(spans) && spans.length,
  );
  if (!entries.length) return '';
  const now = Math.max(0, Number(elapsedSec) || 0);
  const seconds = (value) => `${(Math.round(value * 10) / 10).toFixed(1)}초`;

  const locked = [];
  let nextLockAt = Infinity;
  for (const [motionId, spans] of entries) {
    const active = spans.find(
      (span) => now >= (Number(span?.[0]) || 0) - 1e-9
        && now <= (Number(span?.[1]) || 0) + 1e-9,
    );
    if (active) {
      locked.push(`${motionId}(${seconds(Number(active[1]) || 0)}까지)`);
      continue;
    }
    for (const span of spans) {
      const start = Number(span?.[0]) || 0;
      if (start > now) nextLockAt = Math.min(nextLockAt, start);
    }
  }
  if (locked.length) {
    const rest = entries.length - locked.length;
    return `재생 중 · ${locked.join(', ')} 잠김`
      + (rest > 0 ? ` · 나머지 축은 녹화됩니다` : ' · 지금은 녹화되지 않습니다');
  }
  return Number.isFinite(nextLockAt)
    ? `전 축 녹화 가능 · ${seconds(nextLockAt)}부터 재생이 시작됩니다`
    : '전 축 녹화 가능 · 재생할 구간이 끝났습니다';
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
    // 그래프가 실제로 쓴 시간축을 그대로 쓴다 · §6-83
    //
    // 여기서 다시 셈하면 어긋난다 · 캔버스는 5초씩 늘어나는데 여기는 녹화
    // 시각에 맞춰 놓여, 그릴 때마다 재생 표시가 튀었다.
    const graphDuration = Math.max(
      MOTION_STUDIO_PERIOD_SEC,
      Number(state.detailGraph.timeSpan) || Number(state.detailGraph.duration) || 0,
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
    // 추가 녹화 중에는 지금 어느 축이 잠겼는지를 앞에 세운다 · §6-79
    const overdubHint = motionStudioOverdubHint(
      state.status?.overdub_spans, playback.elapsed,
    );
    if (el.studioPlaybackTime) {
      el.studioPlaybackTime.textContent = `${timeText(playback.elapsed)} / ${timeText(playback.total)}`;
    }
    if (el.studioPlaybackLayerCount) {
      // 녹화 중에 "합성 그래프" 라고 적혀 있으면 지금 무엇을 보는지 헷갈린다 ·
      // 무엇이 돌고 있는지를 말한다 · §6-85
      const count = state.detailGraph?.enabledLayerCount
        ?? Number(state.status?.playback_layer_count || 0);
      const recorded = (state.status?.recording_motion_ids || []).length;
      el.studioPlaybackLayerCount.textContent = overdubHint
        ? `기존 ${count}개 재생 · 녹화된 축 ${recorded}개`
        : (playback.displayState === 'recording'
          ? `녹화 중 · 기록된 축 ${recorded}개`
          : `재생 선택 ${count}개 · 합성 그래프`);
    }
    if (el.studioPlaybackProgressBar) {
      el.studioPlaybackProgressBar.style.width = `${(playback.ratio * 100).toFixed(2)}%`;
    }
    const message = overdubHint || playback.message;
    if (el.studioPlaybackMessage) el.studioPlaybackMessage.textContent = message;
    if (el.studioPlaybackQuickPhase) {
      el.studioPlaybackQuickPhase.className = `status-chip ${playback.chip}`;
      el.studioPlaybackQuickPhase.textContent = playback.label;
    }
    if (el.studioPlaybackQuickTime) {
      el.studioPlaybackQuickTime.textContent = `${timeText(playback.elapsed)} / ${timeText(playback.total)}`;
    }
    if (el.studioPlaybackQuickMessage) el.studioPlaybackQuickMessage.textContent = message;
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
