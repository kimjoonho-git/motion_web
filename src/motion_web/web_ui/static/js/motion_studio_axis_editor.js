import { createMotionStudioEventScope } from './motion_studio_controller_events.js';

const MOTION_ID_PATTERN = /^[1-9]\d*-[1-9]\d*$/;

export function motionStudioValidMotionId(value) {
  return MOTION_ID_PATTERN.test(String(value || '').trim());
}

export function motionStudioSelectedAxisIds(axisList) {
  return [...(axisList?.querySelectorAll?.('input:checked') || [])]
    .map((input) => String(input.value || ''))
    .filter(Boolean);
}

export function setMotionStudioAxisSelection(axisList, checked) {
  const inputs = [...(axisList?.querySelectorAll?.('input') || [])];
  inputs.forEach((input) => { input.checked = Boolean(checked); });
  return inputs.filter((input) => input.checked).map((input) => String(input.value || ''));
}

export function createMotionStudioAxisEditorController({
  el,
  canChangeSelection = () => true,
  onSelectionChange = () => {},
  onAddAxis = () => {},
  onCopyAxis = () => {},
  onDeleteAxis = () => {},
  onControlChange = () => {},
}) {
  const events = createMotionStudioEventScope();
  let bound = false;
  const bind = () => {
    if (bound) return;
    bound = true;
    events.bind(el.studioEditorSelectAllButton, 'click', () => {
      if (!canChangeSelection()) return;
      setMotionStudioAxisSelection(el.studioEditorAxisList, true);
      onSelectionChange(motionStudioSelectedAxisIds(el.studioEditorAxisList));
    });
    events.bind(el.studioEditorSelectNoneButton, 'click', () => {
      if (!canChangeSelection()) return;
      setMotionStudioAxisSelection(el.studioEditorAxisList, false);
      onSelectionChange([]);
    });
    events.bind(el.studioEditorAxisList, 'change', () => {
      if (!canChangeSelection()) return;
      onSelectionChange(motionStudioSelectedAxisIds(el.studioEditorAxisList));
    });
    events.bind(el.studioEditorAddAxisSelect, 'input', onControlChange);
    events.bind(el.studioEditorCopyAxisSource, 'change', onControlChange);
    events.bind(el.studioEditorCopyAxisTarget, 'input', onControlChange);
    events.bind(el.studioEditorAddAxisButton, 'click', onAddAxis);
    events.bind(el.studioEditorCopyAxisButton, 'click', onCopyAxis);
    events.bind(el.studioEditorDeleteAxisButton, 'click', onDeleteAxis);
  };
  return {
    bind,
    destroy() {
      events.destroy();
      bound = false;
    },
  };
}
