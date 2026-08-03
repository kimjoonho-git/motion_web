export function reconcileMotionStudioLayerManagerSelection(
  state,
  layers,
  layerPointCoverageIssues,
) {
  const layerIds = new Set(layers.map((layer) => String(layer.layer_id || '')));
  if (state.selectedLayerId && !layerIds.has(state.selectedLayerId)) state.selectedLayerId = '';
  if (state.layerManagerTab !== 'merge') return;
  const mergeableLayerIds = new Set(layers
    .filter((layer) => !layer.locked && layerPointCoverageIssues(layer).length === 0)
    .map((layer) => String(layer.layer_id || '')));
  state.mergeLayerIds = new Set(
    [...state.mergeLayerIds].filter((layerId) => mergeableLayerIds.has(layerId)),
  );
  if (!state.mergeLayerIds.has(state.mergeAppendLayerId)) state.mergeAppendLayerId = '';
}

export function renderMotionStudioLayerManager({
  state,
  el,
  escapeHtml,
  layerSummary,
  layerPointCoverageIssues,
}) {
  const layers = state.project?.layers || [];
  reconcileMotionStudioLayerManagerSelection(state, layers, layerPointCoverageIssues);

  el.studioLayerManagerTabs?.querySelectorAll('[data-layer-manager-tab]').forEach((button) => {
    const active = button.dataset.layerManagerTab === state.layerManagerTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  el.studioLayerManagerPanels?.forEach((panel) => {
    panel.classList.toggle('hidden', panel.dataset.layerManagerPanel !== state.layerManagerTab);
  });

  if (el.studioManagerLayerRows && state.layerManagerTab === 'copy') {
    el.studioManagerLayerRows.innerHTML = layers.length ? layers.map((layer) => {
      const selected = layer.layer_id === state.selectedLayerId;
      return `<tr class="${selected ? 'selected-row' : ''}" data-manager-layer-id="${escapeHtml(layer.layer_id)}">
        <td><input type="radio" name="studio-manager-layer" data-manager-layer-select ${selected ? 'checked' : ''} aria-label="개별 관리 레이어 선택"></td>
        <td><strong>${escapeHtml(layer.name)}</strong></td>
        <td><span>${layerSummary(layer)}</span>${layer.locked ? ' <span class="status-chip off">잠금</span>' : ''}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="3" class="empty">레이어가 없습니다</td></tr>';
  }

  if (el.studioManagerMergeRows && state.layerManagerTab === 'merge') {
    el.studioManagerMergeRows.innerHTML = layers.length ? layers.map((layer) => {
      const checked = state.mergeLayerIds.has(layer.layer_id);
      const pointIssues = layerPointCoverageIssues(layer);
      const disabled = layer.locked || pointIssues.length > 0;
      return `<tr data-manager-merge-layer-id="${escapeHtml(layer.layer_id)}">
        <td><input type="checkbox" data-manager-layer-merge ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''} aria-label="합칠 레이어 선택"></td>
        <td><strong>${escapeHtml(layer.name)}</strong></td>
        <td><span>${layerSummary(layer)}</span>${layer.locked ? ' <span class="status-chip off">잠금</span>' : ''}${pointIssues.length ? ` <span class="status-chip warn">포인트 필요 · ${escapeHtml(pointIssues.join(', '))}</span>` : ''}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="3" class="empty">레이어가 없습니다</td></tr>';
  }
  if (el.studioMergeMode && state.layerManagerTab === 'merge') {
    el.studioMergeMode.value = state.mergeMode;
  }
  if (el.studioMergeAppendLayer && state.layerManagerTab === 'merge') {
    const selectedLayers = layers.filter(
      (layer) => state.mergeLayerIds.has(String(layer.layer_id || '')),
    );
    el.studioMergeAppendLayer.innerHTML = (
      '<option value="">선택한 레이어 중 지정</option>'
      + selectedLayers.map((layer) => (
        `<option value="${escapeHtml(layer.layer_id)}">${escapeHtml(layer.name)}</option>`
      )).join('')
    );
    el.studioMergeAppendLayer.value = state.mergeAppendLayerId;
    el.studioMergeAppendLayer.disabled = state.mergeMode !== 'append';
  }
}
