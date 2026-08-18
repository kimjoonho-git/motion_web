let installedDialogs = null;

export function createDialogManager({ el } = {}) {
  const queue = [];
  let active = null;

  function setVisible(visible) {
    el?.appDialogModal?.classList.toggle('hidden', !visible);
    document.body.classList.toggle('app-dialog-open', visible);
  }

  function renderNext() {
    if (active || queue.length === 0) return;
    active = queue.shift();
    const {
      type,
      title,
      message,
      defaultValue,
      confirmLabel,
      cancelLabel,
      tone,
    } = active.options;
    if (el?.appDialogModal) el.appDialogModal.dataset.tone = tone;
    if (el?.appDialogTitle) el.appDialogTitle.textContent = title;
    if (el?.appDialogMessage) el.appDialogMessage.textContent = message;
    const inputVisible = type === 'prompt';
    el?.appDialogInputWrap?.classList.toggle('hidden', !inputVisible);
    if (el?.appDialogInput) {
      el.appDialogInput.value = inputVisible ? defaultValue : '';
    }
    if (el?.appDialogCancelButton) {
      el.appDialogCancelButton.classList.toggle('hidden', type === 'alert');
      el.appDialogCancelButton.textContent = cancelLabel;
    }
    if (el?.appDialogConfirmButton) {
      el.appDialogConfirmButton.textContent = confirmLabel;
      el.appDialogConfirmButton.classList.toggle('danger', tone === 'danger');
    }
    setVisible(true);
    window.setTimeout(() => {
      if (inputVisible) el?.appDialogInput?.focus();
      else el?.appDialogConfirmButton?.focus();
    }, 0);
  }

  function settle(value) {
    if (!active) return;
    const { resolve, previousFocus } = active;
    active = null;
    setVisible(false);
    resolve(value);
    previousFocus?.focus?.();
    renderNext();
  }

  function open(options) {
    return new Promise((resolve) => {
      queue.push({
        options,
        resolve,
        previousFocus: document.activeElement,
      });
      renderNext();
    });
  }

  function alert(message, {
    title = '알림',
    confirmLabel = '확인',
    tone = 'info',
  } = {}) {
    return open({
      type: 'alert',
      title,
      message: String(message || ''),
      defaultValue: '',
      confirmLabel,
      cancelLabel: '',
      tone,
    });
  }

  function confirm(message, {
    title = '확인',
    confirmLabel = '확인',
    cancelLabel = '취소',
    tone = 'warning',
  } = {}) {
    return open({
      type: 'confirm',
      title,
      message: String(message || ''),
      defaultValue: '',
      confirmLabel,
      cancelLabel,
      tone,
    });
  }

  function prompt(message, {
    title = '입력',
    defaultValue = '',
    confirmLabel = '확인',
    cancelLabel = '취소',
    tone = 'info',
  } = {}) {
    return open({
      type: 'prompt',
      title,
      message: String(message || ''),
      defaultValue: String(defaultValue ?? ''),
      confirmLabel,
      cancelLabel,
      tone,
    });
  }

  el?.appDialogConfirmButton?.addEventListener('click', () => {
    if (!active) return;
    settle(active.options.type === 'prompt' ? el?.appDialogInput?.value ?? '' : true);
  });
  el?.appDialogCancelButton?.addEventListener('click', () => {
    settle(active?.options.type === 'prompt' ? null : false);
  });
  el?.appDialogInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      settle(el.appDialogInput.value);
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !active || active.options.type === 'alert') return;
    event.preventDefault();
    settle(active.options.type === 'prompt' ? null : false);
  });
  setVisible(false);

  return { alert, confirm, prompt, dismissAll: () => {
    queue.length = 0;
    if (active) {
      const { resolve, previousFocus } = active;
      active = null;
      setVisible(false);
      resolve(false);
      previousFocus?.focus?.();
    }
  } };
}

export function installDialogManager(options) {
  installedDialogs = createDialogManager(options);
  return installedDialogs;
}

function dialogs() {
  if (!installedDialogs) throw new Error('dialog manager is not installed');
  return installedDialogs;
}

export function showAlert(message, options) {
  return dialogs().alert(message, options);
}

export function showConfirm(message, options) {
  return dialogs().confirm(message, options);
}

export function showPrompt(message, options) {
  return dialogs().prompt(message, options);
}

export function dismissAllDialogs() {
  installedDialogs?.dismissAll();
}
