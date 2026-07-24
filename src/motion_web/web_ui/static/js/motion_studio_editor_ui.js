function text(value) {
  return String(value ?? '').trim();
}

function configuredMotorAxis(motor) {
  return Number(
    motor?.config?.controller_index
    ?? motor?.controller_index
    ?? motor?.axis,
  );
}

function configuredMotorRef(motor) {
  const explicit = text(motor?.motor_ref);
  if (explicit) return explicit.toLowerCase();
  const type = text(motor?.motor_type || motor?.motor_type_label).toLowerCase();
  if (type.includes('dynamixel')) {
    const busId = Number(motor?.config?.bus_id ?? motor?.identity?.bus_id ?? motor?.bus_id);
    return Number.isInteger(busId) && busId >= 0 ? `dynamixel:id:${busId}` : '';
  }
  const alias = Number(
    motor?.config?.alias
    ?? motor?.identity?.ethercat_alias
    ?? motor?.alias,
  );
  return Number.isFinite(alias) && alias > 0 ? `ac_servo:alias:${alias}` : '';
}

export function motionStudioEditorAxisLabel(
  motionId,
  mappingRows = [],
  configuredMotors = [],
) {
  const id = text(motionId);
  const mapping = (Array.isArray(mappingRows) ? mappingRows : []).find(
    (row) => text(row?.motion_id) === id,
  );
  if (!mapping) return id;
  const targetRef = text(mapping.motor_ref).toLowerCase();
  const targetAxis = Number(mapping.motor_axis);
  const motor = (Array.isArray(configuredMotors) ? configuredMotors : []).find(
    (candidate) => (
      (targetRef && configuredMotorRef(candidate) === targetRef)
      || (!targetRef && Number.isFinite(targetAxis)
        && configuredMotorAxis(candidate) === targetAxis)
    ),
  );
  const name = text(motor?.name || motor?.display_name);
  return name && name !== '-' ? `${id}  ${name}` : id;
}

export function motionStudioEditorInspectorState({
  preview = false,
  pointDraftUnsaved = false,
  savedPointCurve = false,
  pointSelected = false,
  rangeSelected = false,
} = {}) {
  if (preview) return {
    key: 'preview',
    label: '미리보기 중',
    guide: '결과를 확인한 뒤 작업본에 반영하거나 실행 취소하세요.',
  };
  if (pointDraftUnsaved && !savedPointCurve) return {
    key: 'unsaved-point',
    label: '저장 전 포인트 모션',
    guide: '포인트 변환 결과를 작업본에 반영하고 저장해야 포인트 편집이 활성화됩니다.',
  };
  if (pointSelected) return {
    key: 'point',
    label: '포인트 선택',
    guide: '시간·모션값·탄젠트·곡선 차수를 조정할 수 있습니다.',
  };
  if (savedPointCurve) return {
    key: 'saved-point',
    label: '저장된 포인트 구간',
    guide: '포인트 두 개를 선택해 시간과 모션값을 편집할 수 있습니다.',
  };
  if (rangeSelected) return {
    key: 'motion-range',
    label: '일반 모션 구간',
    guide: '선택 구간을 포인트 모션으로 변환할 수 있습니다.',
  };
  return {
    key: 'none',
    label: '선택 없음',
    guide: '그래프에서 모션점이나 포인트를 선택하세요.',
  };
}

export function renderMotionStudioEditorPresentation(el, {
  saveState = 'saved',
  savedAt = '',
  saveError = '',
  inspector = motionStudioEditorInspectorState(),
  showDangerZone = false,
} = {}) {
  const saveLabels = {
    saved: savedAt ? `저장 완료 · ${savedAt}` : '저장됨',
    dirty: '저장되지 않음',
    preview: '미리보기 중',
    saving: '저장 중…',
    failed: `저장 실패${saveError ? ` · ${saveError}` : ''}`,
  };
  if (el.studioEditorSaveStatus) {
    el.studioEditorSaveStatus.textContent = saveLabels[saveState] || saveLabels.dirty;
    el.studioEditorSaveStatus.className = `status-chip ${
      saveState === 'saved' ? 'on' : saveState === 'failed' ? 'off' : 'warn'
    }`;
  }
  if (el.studioEditorInspectorState) {
    el.studioEditorInspectorState.textContent = inspector.label;
    el.studioEditorInspectorState.dataset.state = inspector.key;
  }
  if (el.studioEditorSelectionGuide) {
    el.studioEditorSelectionGuide.textContent = inspector.guide;
  }
  el.studioEditorDangerZone?.classList.toggle('hidden', !showDangerZone);
}

export function requestMotionStudioEditorSave(el, summary = {}) {
  const modal = el.studioEditorSaveConfirmModal;
  if (!modal) return Promise.resolve(false);
  if (el.studioEditorSaveLayerName) {
    el.studioEditorSaveLayerName.textContent = text(summary.layerName) || '-';
  }
  if (el.studioEditorSaveEditCount) {
    el.studioEditorSaveEditCount.textContent = `${Number(summary.editCount) || 0}회`;
  }
  if (el.studioEditorSavePointChange) {
    el.studioEditorSavePointChange.textContent = summary.pointCurvesChanged
      ? '변경됨' : '변경 없음';
  }
  if (el.studioEditorSaveWarningCount) {
    el.studioEditorSaveWarningCount.textContent = `${Number(summary.warningCount) || 0}건`;
  }
  modal.classList.remove('hidden');
  return new Promise((resolve) => {
    const finish = (confirmed) => {
      modal.classList.add('hidden');
      el.studioEditorSaveCancelButton?.removeEventListener('click', cancel);
      el.studioEditorSaveConfirmButton?.removeEventListener('click', confirm);
      modal.removeEventListener('click', backdrop);
      resolve(confirmed);
    };
    const cancel = () => finish(false);
    const confirm = () => finish(true);
    const backdrop = (event) => {
      if (event.target === modal) finish(false);
    };
    el.studioEditorSaveCancelButton?.addEventListener('click', cancel);
    el.studioEditorSaveConfirmButton?.addEventListener('click', confirm);
    modal.addEventListener('click', backdrop);
  });
}
