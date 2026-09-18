export const MOTION_STUDIO_PERIOD_SEC = 0.02;
export const MOTION_STUDIO_PERIOD_MS = MOTION_STUDIO_PERIOD_SEC * 1000;
export const MOTION_STUDIO_TIME_EPSILON = 1e-9;

/** 스튜디오 상태 이름 · 서버의 `motion_studio.take.STATES` 와 같아야 한다 · §6-88
 *
 * 같은 이름을 파이썬 50곳과 JS 54곳이 맨 문자열로 들고 있었다 · 한쪽에 새
 * 이름이 생기면 다른 쪽은 모른 채로 돌고, 알 방법도 없었다.
 *
 * 여기 적어 두고 양쪽이 같은지 검사가 지킨다 · 주기(`MOTION_STUDIO_PERIOD_SEC`)
 * 를 `motion_common.timing` 이 한 곳에서 정의하는 것과 같은 방식이다.
 */
export const MOTION_STUDIO_STATES = Object.freeze([
  'error',
  'idle',
  'initializing',
  'playing',
  'recording',
  'stopping',
]);

/** 스튜디오가 **일하는 중**인 상태 · §6-167
 *
 * 「지금 손대면 안 된다」의 뜻이다 · 초기 이동과 정지 처리도 포함한다.
 *
 * 전에는 이 목록이 세 곳에 손으로 적혀 있었다 (`motion_studio.js` 둘,
 * `motion_studio_editor_math.js` 하나) · 게다가 한 곳은 **순서까지 달라서**
 * 같은 것인지 한눈에 안 보였다.
 */
export const MOTION_STUDIO_BUSY_STATES = Object.freeze(
  MOTION_STUDIO_STATES.filter((state) => !['error', 'idle'].includes(state)),
);

/** 스튜디오에서 **축이 실제로 움직이는** 상태 · §6-167
 *
 * 위보다 좁다 · 초기 이동(`initializing`)과 정지 처리(`stopping`)는 빠진다 ·
 * 서버 쪽 `run_state.is_moving()` 과 같은 구분이다.
 */
export const MOTION_STUDIO_MOVING_STATES = Object.freeze(['playing', 'recording']);

/** 상태에 더해 화면이 `phase` 로 받는 이름 · 카운트다운만 따로 보인다. */
export const MOTION_STUDIO_STATUS_PHASES = Object.freeze([
  ...MOTION_STUDIO_STATES,
  'countdown',
].sort());
