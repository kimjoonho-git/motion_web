export function createMotionStudioEventScope() {
  const bindings = [];
  let destroyed = false;

  return {
    bind(target, type, listener, options) {
      if (destroyed || !target?.addEventListener || typeof listener !== 'function') return;
      if (bindings.some((binding) => (
        binding.target === target
        && binding.type === type
        && binding.listener === listener
      ))) return;
      target.addEventListener(type, listener, options);
      bindings.push({ target, type, listener, options });
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      bindings.splice(0).forEach(({ target, type, listener, options }) => {
        target.removeEventListener?.(type, listener, options);
      });
    },
    get size() {
      return bindings.length;
    },
  };
}

export function createMotionStudioRequestFence() {
  let epoch = 0;
  return {
    capture() {
      return epoch;
    },
    invalidate() {
      epoch += 1;
      return epoch;
    },
    isCurrent(token) {
      return token === epoch;
    },
  };
}
