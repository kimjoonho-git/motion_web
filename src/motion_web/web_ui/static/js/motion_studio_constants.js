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

/** 상태에 더해 화면이 `phase` 로 받는 이름 · 카운트다운만 따로 보인다. */
export const MOTION_STUDIO_STATUS_PHASES = Object.freeze([
  ...MOTION_STUDIO_STATES,
  'countdown',
].sort());
