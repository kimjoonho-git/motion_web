export function createMotionStudioGraphScheduler({
  draw,
  requestFrame = (callback) => globalThis.requestAnimationFrame(callback),
  cancelFrame = (frameId) => globalThis.cancelAnimationFrame(frameId),
}) {
  let pendingFrame = 0;

  function schedule() {
    if (pendingFrame) return;
    pendingFrame = requestFrame(() => {
      pendingFrame = 0;
      draw();
    });
  }

  function flush() {
    if (pendingFrame) {
      cancelFrame(pendingFrame);
      pendingFrame = 0;
    }
    draw();
  }

  function cancel() {
    if (!pendingFrame) return;
    cancelFrame(pendingFrame);
    pendingFrame = 0;
  }

  return { schedule, flush, cancel };
}
