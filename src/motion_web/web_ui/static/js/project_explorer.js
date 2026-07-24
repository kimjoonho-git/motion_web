import {
  activateProjectFile,
  copyProjectFile,
  createProject,
  deleteProject,
  deleteProjectFile,
  fetchProject,
  fetchProjectFile,
  fetchReadOnlyProjectFile,
  fetchProjects,
  importProjectFile,
  openProjectFileEditor,
  projectFileDownloadUrl,
  renameProjectFile,
  saveProjectFile,
  saveProjectMemo,
  selectProject,
} from './api.js?v=20260722-motor-config-delete';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const CATEGORY_VIEW = {
  project_root: { icon: 'ⓘ' },
  motor_axes: { icon: '⚙' },
  motion_axis_matching: { icon: '⇄' },
  motions: { icon: '▶' },
  layers: { icon: '▤' },
  logs: { icon: '≡' },
  runtime: { icon: '◇' },
  trash: { icon: '⌫' },
};

const MANAGED_FILE_CATEGORIES = new Set([
  'motor_axes', 'motion_axis_matching', 'motions', 'layers',
]);

export function createProjectExplorerController({
  el,
  onOpenEditor = () => {},
  onManageFile = () => {},
  onAddMotionLayer = async () => {},
  onNavigate = () => {},
  onProjectChange = async () => {},
}) {
  const state = {
    projects: [], project: null, tree: [], selectedFile: null, busy: false, projectRoot: '',
    copySourceProjectId: '', copySourceTree: [], runtimeProjectId: '', memoDraft: '', memoDirty: false,
    memoError: '',
    projectGeneration: null,
  };

  function memoSupported() {
    return Boolean(state.project
      && Object.prototype.hasOwnProperty.call(state.project, 'memo'));
  }

  function loadMemoDraft() {
    state.memoDraft = String(state.project?.memo || '').slice(0, 4000);
    state.memoDirty = false;
    state.memoError = '';
  }

  function setMessage(message, error = false) {
    if (!el.projectExplorerMessage) return;
    el.projectExplorerMessage.textContent = message || '';
    el.projectExplorerMessage.classList.toggle('error-text', error);
  }

  function renderProjectList() {
    if (el.projectExplorerCurrentName) {
      el.projectExplorerCurrentName.textContent = state.project?.name || '선택된 프로젝트 없음';
      el.projectExplorerCurrentName.classList.toggle('empty', !state.project);
      el.projectExplorerCurrentName.title = state.project?.project_id || '';
    }
    if (el.headerProjectName) {
      el.headerProjectName.textContent = state.project?.name || '프로젝트 없음';
      el.headerProjectName.title = state.project?.name || '현재 프로젝트 없음';
      el.headerProjectName.classList.toggle('empty', !state.project);
    }
    const runtimeProject = state.projects.find(
      (project) => project.project_id === state.runtimeProjectId,
    );
    const runtimeConfigCurrent = Boolean(runtimeProject?.setup_status?.motor_applied);
    if (el.projectSelectedStatus) {
      el.projectSelectedStatus.textContent = state.project?.name || '선택 없음';
    }
    if (el.projectRuntimeStatus) {
      el.projectRuntimeStatus.textContent = runtimeProject
        ? `${runtimeProject.name}${runtimeConfigCurrent ? '' : ' · 재적용 필요'}`
        : '적용 정보 없음';
      el.projectRuntimeStatus.classList.toggle(
        'warning-text',
        Boolean((runtimeProject && !runtimeConfigCurrent)
          || (state.project && state.runtimeProjectId
            && state.project.project_id !== state.runtimeProjectId)),
      );
    }
    if (!el.projectExplorerSelect) return;
    const selected = state.project?.project_id || el.projectExplorerSelect.value;
    el.projectExplorerSelect.innerHTML = '<option value="">프로젝트 선택</option>' + state.projects.map((project) => (
      `<option value="${escapeHtml(project.project_id)}">${escapeHtml(project.name)}</option>`
    )).join('');
    el.projectExplorerSelect.value = selected || '';
    if (el.projectCopySourceProject) {
      const currentId = state.project?.project_id || '';
      const available = state.projects.filter((project) => project.project_id !== currentId);
      el.projectCopySourceProject.innerHTML = '<option value="">원본 프로젝트 선택</option>' + available.map((project) => (
        `<option value="${escapeHtml(project.project_id)}">${escapeHtml(project.name)}</option>`
      )).join('');
      if (available.some((project) => project.project_id === state.copySourceProjectId)) {
        el.projectCopySourceProject.value = state.copySourceProjectId;
      } else {
        state.copySourceProjectId = '';
        state.copySourceTree = [];
      }
    }
    if (el.projectCopySourceFile) {
      const previous = el.projectCopySourceFile.value;
      const options = state.copySourceTree.filter((folder) => (
        MANAGED_FILE_CATEGORIES.has(folder.category)
      )).flatMap((folder) => (
        (folder.children || []).map((file) => ({
          value: JSON.stringify([folder.category, file.name]),
          label: `${folder.name} / ${file.name}`,
        }))
      ));
      el.projectCopySourceFile.innerHTML = '<option value="">파일 선택</option>' + options.map((item) => (
        `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`
      )).join('');
      if (options.some((item) => item.value === previous)) el.projectCopySourceFile.value = previous;
    }
  }

  function treeFileCount(nodes) {
    return (nodes || []).reduce((sum, node) => (
      sum + (node.node_type === 'folder' ? treeFileCount(node.children) : 1)
    ), 0);
  }

  function renderReadOnlyNodes(nodes, depth = 0) {
    if (!Array.isArray(nodes) || nodes.length === 0) {
      return '<div class="project-tree-empty">파일 없음</div>';
    }
    return nodes.map((node, index) => {
      const last = index === nodes.length - 1;
      if (node.node_type === 'folder') {
        const count = treeFileCount(node.children);
        return `<details class="project-tree-internal-folder" ${depth === 0 ? 'open' : ''}>`
          + `<summary><span class="project-tree-branch">${last ? '└' : '├'}</span>`
          + `<span class="project-tree-internal-folder-icon">▸</span>`
          + `<strong>${escapeHtml(node.name)}</strong><small>${count}</small></summary>`
          + `<div class="project-tree-internal-children">${renderReadOnlyNodes(node.children, depth + 1)}</div>`
          + '</details>';
      }
      const selected = state.selectedFile?.read_only
        && state.selectedFile?.relative_path === node.relative_path;
      return `<button type="button" class="project-tree-readonly-file${selected ? ' selected' : ''}" `
        + `data-project-readonly-open data-project-path="${escapeHtml(node.relative_path || '')}" `
        + `title="${escapeHtml(node.relative_path || node.name)} · 원문 보기(읽기 전용)">`
        + `<span class="project-tree-branch">${last ? '└' : '├'}</span>`
        + '<span class="project-tree-readonly-icon">·</span>'
        + `<span class="project-tree-name">${escapeHtml(node.name)}</span>`
        + `<small>${formatBytes(node.size)}</small>`
        + '<span class="project-tree-readonly-badge">읽기 전용</span></button>';
    }).join('');
  }

  function renderTree() {
    if (!el.projectExplorerTree) return;
    if (!state.project) {
      el.projectExplorerTree.innerHTML = '<div class="empty">프로젝트를 만들거나 선택하세요</div>';
      return;
    }
    const totalFiles = state.tree.reduce((sum, folder) => sum + treeFileCount(folder.children), 0);
    const folders = state.tree.map((folder) => {
      const view = CATEGORY_VIEW[folder.category] || { icon: '□' };
      const readOnly = Boolean(folder.read_only);
      const children = readOnly ? renderReadOnlyNodes(folder.children) : folder.children.map((file, fileIndex) => {
        const isLogFile = file.category === 'logs';
        const selected = state.selectedFile?.category === file.category
          && state.selectedFile?.file_name === file.name;
        const addButton = file.category === 'motions'
          ? `<button type="button" class="project-tree-action project-tree-add" data-project-add-layer title="스튜디오 레이어로 추가" aria-label="${escapeHtml(file.name)} 레이어로 추가">＋</button>`
          : '';
        const bankInfo = file.category === 'motion_axis_matching' ? file.midi_banks : null;
        const midiRouteAttributes = bankInfo
          ? `data-project-open-midi data-project-category="${escapeHtml(file.category)}" data-project-file="${escapeHtml(file.name)}"`
          : '';
        const fileBadge = isLogFile
          ? `<span class="project-tree-log-count">${Number(file.record_count) || 0}건</span>`
          : (file.active ? '<span class="project-tree-active">현재</span>' : '');
        const bankTree = bankInfo ? (() => {
          if (!bankInfo.stored) {
            return `<button type="button" class="project-tree-midi project-tree-midi-missing" ${midiRouteAttributes}>`
              + '<span class="project-tree-branch">└</span><span>MIDI 뱅크</span><small>미저장</small></button>';
          }
          const banks = (bankInfo.banks || []).map((bank, bankIndex) => (
            '<div class="project-tree-midi-bank">'
            + `<span class="project-tree-branch">${bankIndex === bankInfo.banks.length - 1 ? '└' : '├'}</span>`
            + `<span>${escapeHtml(bank.name)}</span>`
            + `${bank.bank_id === bankInfo.active_bank_id ? '<small class="project-tree-midi-active">현재</small>' : ''}`
            + `<small>${Number(bank.mapping_count) || 0}채널</small></div>`
          )).join('');
          return '<div class="project-tree-midi-group">'
            + `<button type="button" class="project-tree-midi" ${midiRouteAttributes}>`
            + '<span class="project-tree-branch">└</span>'
            + `<span>MIDI 뱅크</span><small>${Number(bankInfo.count) || 0}개</small></button>`
            + `<div class="project-tree-midi-banks">${banks || '<div class="project-tree-midi-bank empty">뱅크 없음</div>'}</div></div>`;
        })() : '';
        return `<div class="project-tree-file-entry"><div class="project-tree-file-row${selected ? ' selected' : ''}" `
          + `data-project-category="${escapeHtml(file.category)}" data-project-file="${escapeHtml(file.name)}">`
          + `<button type="button" class="project-tree-file" ${isLogFile ? 'data-project-log-open' : 'data-project-open'} title="${escapeHtml(file.name)} · ${isLogFile ? '로그 탭에서 보기' : '기능에서 열기'}">`
          + `<span class="project-tree-branch">${fileIndex === folder.children.length - 1 ? '└' : '├'}</span>`
          + `<span class="project-tree-name">${escapeHtml(file.name)}</span>`
          + `${fileBadge}</button>`
          + addButton
          + `${isLogFile ? '' : `<button type="button" class="project-tree-action" data-project-manage title="파일 관리" aria-label="${escapeHtml(file.name)} 관리">⋮</button>`}`
          + `</div>${bankTree}</div>`;
      }).join('') || '<div class="project-tree-empty">파일 없음</div>';
      const childCount = readOnly ? treeFileCount(folder.children) : folder.children.length;
      const defaultOpen = folder.category === 'runtime' || folder.category === 'trash' ? '' : 'open';
      return `<details class="project-tree-folder project-tree-folder-${escapeHtml(folder.category)}" ${defaultOpen}>`
        + `<summary><span class="project-folder-icon">${view.icon}</span>`
        + `<strong>${escapeHtml(folder.name)}</strong>`
        + `<span class="project-folder-count">${childCount}</span></summary>`
        + `<div class="project-tree-children">${children}</div></details>`;
    }).join('');
    el.projectExplorerTree.innerHTML = `<div class="project-tree-root">`
      + `<span class="project-root-icon">▾</span><span class="project-root-folder">▣</span>`
      + `<strong>${escapeHtml(state.project.name)}</strong><span class="project-root-count">${totalFiles}</span></div>`
      + `<div class="project-tree-folders">${folders}</div>`;
  }

  function renderEditor() {
    const file = state.selectedFile;
    const readOnly = Boolean(file?.read_only);
    if (el.projectFileEditorTitle) {
      el.projectFileEditorTitle.textContent = file
        ? (file.relative_path || `${file.category} / ${file.file_name}`)
        : '파일을 선택하세요';
    }
    if (el.projectFileEditor) {
      el.projectFileEditor.value = file?.content || '';
      el.projectFileEditor.disabled = !file || state.busy;
      el.projectFileEditor.readOnly = readOnly;
    }
    if (el.projectFileInfo) {
      el.projectFileInfo.textContent = file
        ? `${readOnly ? '읽기 전용 · ' : ''}${formatBytes(file.size)} · SHA-256 ${String(file.sha256 || '').slice(0, 12)}…`
        : '프로젝트 파일은 편집해도 실제 장비에 자동 적용되지 않습니다.';
    }
    const disabled = !file || state.busy || readOnly;
    for (const button of [
      el.projectFileSaveButton,
      el.projectFileOpenEditorButton,
      el.projectFileRenameButton,
      el.projectFileActivateButton,
      el.projectFileExportButton,
      el.projectFileDeleteButton,
    ]) {
      if (button) button.disabled = disabled;
    }
  }

  function renderSetupProgress() {
    if (!el.projectSetupProgress) return;
    if (!state.project) {
      el.projectSetupProgress.innerHTML = '';
      return;
    }
    const status = state.project.setup_status || {};
    const steps = [
      ['config', '', '모터축 설정', Boolean(status.motor_configured)],
      ['config', '', '실행 설정 적용', Boolean(status.motor_applied)],
      ['manual', '', '조그 확인', Boolean(status.jog_verified)],
      ['motion', 'mapping', '모션축 설정', Boolean(status.motion_axes_configured)],
      ['studio', '', '첫 모션 제작', Number(status.motion_count) > 0],
    ];
    el.projectSetupProgress.innerHTML = '<strong>처음 설정</strong>' + steps.map((step, index) => (
      `<button type="button" data-setup-workspace="${step[0]}" data-setup-motion-tab="${step[1]}">`
      + `<span>${step[3] ? '✓' : index + 1}</span><span>${step[2]}</span>`
      + `<small>${step[3] ? '완료' : '진행 필요'}</small></button>`
    )).join('');
  }

  function renderControls() {
    const hasProject = Boolean(state.project);
    if (el.projectImportFileButton) el.projectImportFileButton.disabled = state.busy || !hasProject;
    if (el.projectDeleteButton) el.projectDeleteButton.disabled = state.busy || !hasProject;
    if (el.projectCopySourceProject) el.projectCopySourceProject.disabled = state.busy || !hasProject;
    if (el.projectCopySourceFile) {
      el.projectCopySourceFile.disabled = state.busy || !hasProject || !state.copySourceProjectId;
    }
    if (el.projectCopyFileButton) {
      el.projectCopyFileButton.disabled = state.busy || !hasProject || !el.projectCopySourceFile?.value;
    }
    if (el.projectExplorerRefreshButton) el.projectExplorerRefreshButton.disabled = state.busy;
    if (el.projectUsbRescanButton) el.projectUsbRescanButton.disabled = state.busy;
    if (el.projectMemoInput) {
      el.projectMemoInput.disabled = state.busy || !hasProject || !memoSupported();
    }
    if (el.projectMemoSaveButton) {
      el.projectMemoSaveButton.disabled = state.busy || !hasProject
        || !memoSupported() || !state.memoDirty;
    }
    if (el.projectUsbHelp && state.projectRoot) {
      el.projectUsbHelp.textContent = `USB 프로젝트 폴더 복사 위치: ${state.projectRoot}`;
    }
  }

  function renderProjectMemo() {
    if (el.projectMemoInput && el.projectMemoInput.value !== state.memoDraft) {
      el.projectMemoInput.value = state.memoDraft;
    }
    if (el.projectMemoCount) el.projectMemoCount.textContent = `${state.memoDraft.length} / 4000`;
    if (el.projectMemoStatus) {
      const status = !state.project
        ? '프로젝트를 선택하면 메모를 입력할 수 있습니다'
        : !memoSupported()
          ? '메모 저장 기능을 사용하려면 프로그램 재시작이 필요합니다'
          : state.memoError
            ? `저장 실패: ${state.memoError}`
            : state.memoDirty
              ? '저장하지 않은 변경이 있습니다'
              : 'project.json에 저장됨';
      el.projectMemoStatus.textContent = status;
      el.projectMemoStatus.classList.toggle('unsaved', state.memoDirty);
      el.projectMemoStatus.classList.toggle('error-text', Boolean(state.memoError)
        || Boolean(state.project && !memoSupported()));
    }
  }

  function render() {
    renderProjectList();
    renderTree();
    renderEditor();
    renderSetupProgress();
    renderControls();
    renderProjectMemo();
  }

  async function loadProject(projectId, select = false) {
    if (!projectId) return;
    const payload = select ? await selectProject(projectId) : await fetchProject(projectId);
    const preserveMemoDraft = state.memoDirty
      && state.project?.project_id === payload.project?.project_id;
    state.project = payload.project || null;
    state.tree = payload.tree || [];
    state.selectedFile = null;
    if (!preserveMemoDraft) loadMemoDraft();
    render();
  }

  async function refresh(silent = false) {
    if (state.busy) return;
    state.busy = true;
    renderControls();
    try {
      const payload = await fetchProjects();
      if (Number.isInteger(Number(payload.project_generation))) {
        state.projectGeneration = Number(payload.project_generation);
      }
      state.projects = payload.projects || [];
      state.runtimeProjectId = payload.runtime_project_id || '';
      state.projectRoot = payload.project_root || '';
      const selectedId = state.project?.project_id || payload.selected_project_id;
      if (selectedId && state.projects.some((item) => item.project_id === selectedId)) {
        await loadProject(selectedId);
      } else {
        state.project = null;
        state.tree = [];
        state.selectedFile = null;
        loadMemoDraft();
      }
      if (!silent || el.projectExplorerMessage?.textContent.includes('불러오는 중')) {
        setMessage(`프로젝트 ${state.projects.length}개 · 변경은 시스템 정보에서만`);
      }
    } catch (error) {
      setMessage(error.message, true);
    } finally {
      state.busy = false;
      render();
    }
  }

  async function run(action, successMessage) {
    if (state.busy) return;
    state.busy = true;
    renderControls();
    try {
      const payload = await action();
      if (Number.isInteger(Number(payload.project_generation))) {
        state.projectGeneration = Number(payload.project_generation);
      }
      if (payload.project) {
        const changedProject = payload.project.project_id !== state.project?.project_id;
        state.project = payload.project;
        if (changedProject) loadMemoDraft();
      }
      if (payload.tree) state.tree = payload.tree;
      setMessage(successMessage);
      const list = await fetchProjects();
      state.projects = list.projects || [];
      if (Number.isInteger(Number(list.project_generation))) {
        state.projectGeneration = Number(list.project_generation);
      }
      state.runtimeProjectId = list.runtime_project_id || '';
    } catch (error) {
      setMessage(error.message, true);
    } finally {
      state.busy = false;
      render();
    }
  }

  async function openFile(category, fileName) {
    if (!state.project || state.busy) return;
    state.busy = true;
    renderControls();
    try {
      state.selectedFile = await fetchProjectFile(state.project.project_id, category, fileName);
      setMessage(`${fileName} 열기 완료`);
    } catch (error) {
      setMessage(error.message, true);
    } finally {
      state.busy = false;
      render();
    }
  }

  async function openReadOnlyFile(relativePath) {
    if (!state.project || state.busy || !relativePath) return;
    state.busy = true;
    renderControls();
    try {
      state.selectedFile = await fetchReadOnlyProjectFile(
        state.project.project_id, relativePath,
      );
      setMessage(`${relativePath} 원문 열기 완료 · 읽기 전용`);
      onManageFile(state.selectedFile);
    } catch (error) {
      setMessage(error.message, true);
    } finally {
      state.busy = false;
      render();
    }
  }

  async function openInFeature(category, fileName, targetWorkspace = '') {
    if (!state.project || state.busy) return false;
    await openFile(category, fileName);
    if (!state.selectedFile) return false;
    state.busy = true;
    renderControls();
    try {
      const result = await openProjectFileEditor(state.project.project_id, category, fileName);
      setMessage(result.message || `${fileName} 기능에서 열기 완료`);
      onOpenEditor(result, targetWorkspace);
      return true;
    } catch (error) {
      setMessage(error.message, true);
      return false;
    } finally {
      state.busy = false;
      render();
    }
  }

  function bindEvents() {
    window.addEventListener('motion-project-files-changed', () => refresh(true));
    el.projectExplorerRefreshButton?.addEventListener('click', () => refresh());
    el.projectUsbRescanButton?.addEventListener('click', () => refresh());
    el.projectMemoInput?.addEventListener('input', () => {
      state.memoDraft = el.projectMemoInput.value.slice(0, 4000);
      state.memoDirty = state.memoDraft !== String(state.project?.memo || '');
      state.memoError = '';
      renderControls();
      renderProjectMemo();
    });
    el.projectMemoSaveButton?.addEventListener('click', async () => {
      if (!state.project || state.busy || !state.memoDirty) return;
      state.busy = true;
      renderControls();
      try {
        const payload = await saveProjectMemo(state.project.project_id, state.memoDraft);
        state.project = payload.project;
        loadMemoDraft();
        const list = await fetchProjects();
        state.projects = list.projects || [];
        state.runtimeProjectId = list.runtime_project_id || '';
        setMessage('프로젝트 메모 저장 완료');
      } catch (error) {
        state.memoError = error.message || String(error);
        setMessage(error.message, true);
      } finally {
        state.busy = false;
        render();
      }
    });
    el.projectExplorerSelect?.addEventListener('change', async () => {
      const projectId = el.projectExplorerSelect.value;
      if (!projectId) return;
      if (state.memoDirty && !window.confirm('저장하지 않은 프로젝트 메모가 있습니다. 변경을 버리고 다른 프로젝트로 이동할까요?')) {
        el.projectExplorerSelect.value = state.project?.project_id || '';
        return;
      }
      await run(() => selectProject(projectId), '프로젝트 선택 완료 · 장비에는 적용되지 않았습니다');
      await onProjectChange(state.project, state.projectGeneration);
    });
    el.projectCreateButton?.addEventListener('click', async () => {
      const name = window.prompt('새 프로젝트 이름을 입력하세요', '새 모션 프로젝트');
      if (!name?.trim()) return;
      await run(() => createProject({ name: name.trim() }), '프로젝트 생성 완료');
      await onProjectChange(state.project, state.projectGeneration);
    });
    el.projectDeleteButton?.addEventListener('click', async () => {
      if (!state.project || state.busy) return;
      const expected = String(state.project.name || '');
      const entered = window.prompt(
        `프로젝트와 관련 파일을 복구할 수 없도록 영구 삭제합니다.\n확인하려면 프로젝트 이름을 입력하세요.\n\n${expected}`,
        '',
      );
      if (entered !== expected) {
        if (entered !== null) setMessage('프로젝트 이름이 일치하지 않아 삭제하지 않았습니다', true);
        return;
      }
      state.busy = true;
      renderControls();
      try {
        const result = await deleteProject(state.project.project_id);
        if (Number.isInteger(Number(result.project_generation))) {
          state.projectGeneration = Number(result.project_generation);
        }
        state.projects = result.projects || [];
        state.project = null;
        state.tree = [];
        state.selectedFile = null;
        state.copySourceProjectId = '';
        state.copySourceTree = [];
        loadMemoDraft();
        setMessage(result.message || '프로젝트와 관련 파일을 영구 삭제했습니다');
        await onProjectChange(null, state.projectGeneration);
      } catch (error) {
        setMessage(error.message, true);
      } finally {
        state.busy = false;
        render();
      }
    });
    el.projectCopySourceProject?.addEventListener('change', async () => {
      state.copySourceProjectId = el.projectCopySourceProject.value;
      state.copySourceTree = [];
      if (!state.copySourceProjectId) {
        render();
        return;
      }
      state.busy = true;
      renderControls();
      try {
        const source = await fetchProject(state.copySourceProjectId);
        state.copySourceTree = source.tree || [];
        setMessage(`원본 프로젝트 선택: ${source.project?.name || state.copySourceProjectId}`);
      } catch (error) {
        state.copySourceProjectId = '';
        setMessage(error.message, true);
      } finally {
        state.busy = false;
        render();
      }
    });
    el.projectCopySourceFile?.addEventListener('change', renderControls);
    el.projectCopyFileButton?.addEventListener('click', async () => {
      if (!state.project || !state.copySourceProjectId || !el.projectCopySourceFile?.value) return;
      let selection;
      try {
        selection = JSON.parse(el.projectCopySourceFile.value);
      } catch (error) {
        setMessage('복사할 파일 선택값이 올바르지 않습니다', true);
        return;
      }
      await run(
        () => copyProjectFile(state.project.project_id, {
          source_project_id: state.copySourceProjectId,
          category: selection[0],
          file_name: selection[1],
        }),
        `${selection[1]} 파일을 현재 프로젝트 폴더로 복사했습니다`,
      );
    });
    el.projectImportFileButton?.addEventListener('click', () => el.projectImportFileInput?.click());
    el.projectImportFileInput?.addEventListener('change', async () => {
      const file = el.projectImportFileInput.files?.[0];
      const category = el.projectImportCategory?.value;
      if (!file || !category || !state.project) return;
      const content = await file.text();
      await run(
        () => importProjectFile(state.project.project_id, {
          category, file_name: file.name, content,
        }),
        `${file.name} 가져오기 완료`,
      );
      el.projectImportFileInput.value = '';
    });
    el.projectExplorerTree?.addEventListener('click', async (event) => {
      const readOnlyButton = event.target.closest('[data-project-readonly-open]');
      if (readOnlyButton) {
        await openReadOnlyFile(readOnlyButton.dataset.projectPath);
        return;
      }
      const row = event.target.closest('[data-project-file]');
      if (!row) return;
      const category = row.dataset.projectCategory;
      const fileName = row.dataset.projectFile;
      if (category === 'logs') {
        onNavigate('log');
        return;
      }
      if (event.target.closest('[data-project-open-midi]')) {
        onNavigate('motion-midi');
        await openInFeature(category, fileName, 'motion-midi');
        return;
      }
      if (event.target.closest('[data-project-add-layer]')) {
        if (!state.project || state.busy) return;
        state.busy = true;
        renderControls();
        try {
          await onAddMotionLayer(fileName);
          setMessage(`${fileName} 파일을 스튜디오 레이어로 추가했습니다`);
        } catch (error) {
          setMessage(error.message || String(error), true);
        } finally {
          state.busy = false;
          render();
        }
        return;
      }
      if (event.target.closest('[data-project-manage]')) {
        await openFile(category, fileName);
        if (state.selectedFile) onManageFile(state.selectedFile);
        return;
      }
      if (event.target.closest('[data-project-open]')) {
        await openInFeature(category, fileName);
      }
    });
    el.projectSetupProgress?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-setup-workspace]');
      if (button) onNavigate(button.dataset.setupWorkspace, button.dataset.setupMotionTab);
    });
    el.projectFileSaveButton?.addEventListener('click', async () => {
      const file = state.selectedFile;
      if (!file || !state.project) return;
      await run(async () => {
        const saved = await saveProjectFile(
          state.project.project_id, file.category, file.file_name, el.projectFileEditor.value,
        );
        state.selectedFile = saved;
        return fetchProject(state.project.project_id);
      }, `${file.file_name} 저장 완료 · 장비에는 적용되지 않았습니다`);
    });
    el.projectFileOpenEditorButton?.addEventListener('click', async () => {
      const file = state.selectedFile;
      if (!file || !state.project || state.busy) return;
      state.busy = true;
      renderControls();
      try {
        const result = await openProjectFileEditor(
          state.project.project_id, file.category, file.file_name,
        );
        setMessage(result.message || '기능 탭에 연결했습니다');
        onOpenEditor(result);
      } catch (error) {
        setMessage(error.message, true);
      } finally {
        state.busy = false;
        render();
      }
    });
    el.projectFileRenameButton?.addEventListener('click', async () => {
      const file = state.selectedFile;
      if (!file || !state.project) return;
      const newName = window.prompt('새 파일명을 입력하세요', file.file_name);
      if (!newName?.trim() || newName.trim() === file.file_name) return;
      await run(
        () => renameProjectFile(state.project.project_id, file.category, file.file_name, newName.trim()),
        '파일 이름 변경 완료',
      );
      state.selectedFile = null;
      render();
    });
    el.projectFileActivateButton?.addEventListener('click', async () => {
      const file = state.selectedFile;
      if (!file || !state.project) return;
      await run(
        () => activateProjectFile(state.project.project_id, file.category, file.file_name),
        '프로젝트의 현재 파일로 선택했습니다 · 실제 장비에는 적용되지 않았습니다',
      );
    });
    el.projectFileExportButton?.addEventListener('click', () => {
      const file = state.selectedFile;
      if (!file || !state.project) return;
      const anchor = document.createElement('a');
      anchor.href = projectFileDownloadUrl(state.project.project_id, file.category, file.file_name);
      anchor.download = file.file_name;
      anchor.click();
    });
    el.projectFileDeleteButton?.addEventListener('click', async () => {
      const file = state.selectedFile;
      if (!file || !state.project) return;
      if (!window.confirm(`${file.file_name} 파일을 프로젝트 휴지통으로 이동할까요?`)) return;
      await run(
        () => deleteProjectFile(state.project.project_id, file.category, file.file_name),
        '파일을 프로젝트 휴지통으로 이동했습니다',
      );
      state.selectedFile = null;
      render();
    });
  }

  return { bindEvents, refresh };
}
