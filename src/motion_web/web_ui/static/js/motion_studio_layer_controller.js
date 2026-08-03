import { createMotionStudioEventScope } from './motion_studio_controller_events.js';

export function selectMotionStudioLayer(state, layerId) {
  const id = String(layerId || '');
  if (!(state.project?.layers || []).some((layer) => String(layer.layer_id) === id)) {
    return false;
  }
  state.selectedLayerId = id;
  return true;
}

export function updateMotionStudioMergeSelection(state, layerId, selected) {
  if (!(state.mergeLayerIds instanceof Set)) state.mergeLayerIds = new Set();
  if (selected) state.mergeLayerIds.add(layerId);
  else state.mergeLayerIds.delete(layerId);
  if (!state.mergeLayerIds.has(state.mergeAppendLayerId)) state.mergeAppendLayerId = '';
  return [...state.mergeLayerIds];
}

export function openMotionStudioLayerManager(el, renderLayerManager) {
  renderLayerManager();
  el.studioLayerManagerModal?.classList.remove('hidden');
  document.body.classList.add('modal-open');
}

export function closeMotionStudioLayerManager(el) {
  el.studioLayerManagerModal?.classList.add('hidden');
  document.body.classList.remove('modal-open');
}

export function createMotionStudioLayerController({ el, handlers = {} }) {
  const events = createMotionStudioEventScope();
  let bound = false;
  const bind = () => {
    if (bound) return;
    bound = true;
    events.bind(el.studioLayerRows, 'change', handlers.onLayerChange);
    events.bind(el.studioLayerRows, 'click', handlers.onLayerClick);
    events.bind(el.studioLayerManagerOpenButton, 'click', handlers.onManagerOpen);
    events.bind(el.studioLayerManagerCloseButton, 'click', handlers.onManagerClose);
    events.bind(el.studioLayerManagerTabs, 'click', handlers.onManagerTab);
    events.bind(el.studioManagerLayerRows, 'click', handlers.onManagerLayerClick);
    events.bind(el.studioManagerLayerRows, 'change', handlers.onManagerLayerChange);
    events.bind(el.studioManagerMergeRows, 'change', handlers.onMergeSelectionChange);
    events.bind(el.studioMergeMode, 'change', handlers.onMergeModeChange);
    events.bind(el.studioMergeAppendLayer, 'change', handlers.onAppendLayerChange);
    events.bind(el.studioSelectedLayerDetailButton, 'click', handlers.onDetail);
    events.bind(el.studioSelectedLayerCopyButton, 'click', handlers.onCopy);
    events.bind(el.studioSelectedLayerEditButton, 'click', handlers.onEdit);
    events.bind(el.studioSelectedLayerLockButton, 'click', handlers.onLock);
    events.bind(el.studioSelectedLayerDeleteButton, 'click', handlers.onDelete);
    events.bind(el.studioPlaybackGraphButton, 'click', handlers.onPlaybackGraph);
    events.bind(el.studioCompositionDetailButton, 'click', handlers.onCompositionDetail);
    events.bind(el.studioLayerDetailTabs, 'click', handlers.onDetailTab);
    events.bind(el.studioMergeButton, 'click', handlers.onMerge);
  };
  return {
    bind,
    destroy() {
      events.destroy();
      bound = false;
    },
  };
}
