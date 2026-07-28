const TERMINAL_FAILURES = new Set(['failure', 'timeout', 'cancelled']);

export function trackedMotorRestartState(operation, expectedOperationId) {
  const expectedId = String(expectedOperationId || '').trim();
  if (!expectedId) {
    return {
      state: 'waiting',
      detail: '재시작 요청의 작업 ID를 기다리는 중입니다.',
    };
  }
  const operationId = String(operation?.operation_id || '').trim();
  const operationType = String(operation?.type || '').trim();
  if (operationId !== expectedId || operationType !== 'motor_restart') {
    return {
      state: 'waiting',
      detail: `현재 재시작 작업 ${expectedId} 상태 수신 대기`,
    };
  }
  const status = String(operation?.status || '').trim();
  const detail = operation?.error
    || operation?.message
    || `작업 단계 · ${operation?.phase || '확인 중'}`;
  if (TERMINAL_FAILURES.has(status)) {
    return { state: status, detail };
  }
  if (status !== 'success') {
    return { state: 'running', detail };
  }
  return { state: 'success', detail };
}
