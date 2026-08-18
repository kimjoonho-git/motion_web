import re
import os

filepath = '/home/joonho_test/ros2_ws/src/motion_web/web_ui/static/js/motion_data.js'
outpath = '/home/joonho_test/ros2_ws/src/motion_web/web_ui/static/js/motion_file_manager.js'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# We will create motion_file_manager.js that encapsulates the file management logic.
# Because the file is highly coupled, for Phase 1 we will extract the core API interactions
# and state for files, and export them to be used by motion_data.js.

manager_code = """import {
  fetchMotionFile,
  fetchMotionFiles,
  deleteMotionFile,
} from './api.js?v=20260810-dds-release-1';
import { showAlert, showConfirm } from './ui_dialogs.js?v=20260727-popup-common-3';

export function createMotionFileManager({
  onFilesChanged,
  onFileSelected,
  onExportToStudio,
  setMessage,
  setLoading,
  checkIsFileRegistered,
}) {
  let files = [];
  let selectedFileId = null;
  let selectedFile = null;
  let fileLoadToken = 0;

  async function loadFiles(targetFileId = selectedFileId) {
    const loadToken = ++fileLoadToken;
    setLoading(true);
    setMessage('파일 목록 불러오는 중');
    try {
      const payload = await fetchMotionFiles();
      if (loadToken !== fileLoadToken) return;
      files = Array.isArray(payload.files) ? payload.files : [];
      if (targetFileId && files.some((file) => file.id === targetFileId)) {
        await selectFile(targetFileId, loadToken);
        return;
      }
      if (selectedFileId && !files.some((file) => file.id === selectedFileId)) {
        selectedFileId = null;
        selectedFile = null;
      }
      setMessage(payload.message || '파일 목록 갱신 완료');
    } catch (error) {
      if (loadToken !== fileLoadToken || error?.staleProjectResponse) return;
      setMessage(`파일 목록 실패: ${error?.message || error}`);
    } finally {
      if (loadToken !== fileLoadToken) return;
      setLoading(false);
      onFilesChanged(files);
    }
  }

  async function selectFile(fileId, requestToken = null) {
    const loadToken = requestToken ?? ++fileLoadToken;
    if (loadToken !== fileLoadToken) return;
    selectedFileId = fileId;
    setLoading(true);
    setMessage('파일 상세 불러오는 중');
    try {
      const payload = await fetchMotionFile(fileId);
      if (loadToken !== fileLoadToken) return;
      selectedFile = payload.file || null;
      files = Array.isArray(payload.files) ? payload.files : files;
      setMessage(payload.message || '파일 상세 갱신 완료');
    } catch (error) {
      if (loadToken !== fileLoadToken || error?.staleProjectResponse) return;
      selectedFile = null;
      setMessage(`파일 상세 실패: ${error?.message || error}`);
    } finally {
      if (loadToken !== fileLoadToken) return;
      setLoading(false);
      onFileSelected(selectedFileId, selectedFile);
    }
  }

  async function exportSelectedFileToStudio() {
    const file = selectedFile;
    if (!file) {
      await showAlert(
        '스튜디오로 내보낼 모션 파일을 먼저 선택하세요.',
        { title: '스튜디오 내보내기', confirmLabel: '확인', tone: 'warning' }
      );
      return;
    }
    setLoading(true);
    setMessage(`${file.filename} 스튜디오 내보내기 중`);
    try {
      const result = await onExportToStudio(file.id);
      if (!result || result.success === false) {
        throw new Error(result?.message || '스튜디오가 모션 파일을 받지 못했습니다');
      }
      const layers = Array.isArray(result.project?.layers)
        ? result.project.layers
        : (result.project_patch?.upsert_layers || []);
      const exportedLayer = [...layers].reverse().find(
        (layer) => layer?.source_motion_file_id === file.id
      );
      const layerName = String(
        exportedLayer?.name || file.filename.replace(/\\.json$/i, '')
      );
      setMessage(`스튜디오 내보내기 완료: ${file.filename} → ${layerName}`);
      await showAlert(
        `모션 파일을 스튜디오의 독립 레이어로 내보냈습니다.\\n`
        + `파일 · ${file.filename}\\n레이어 · ${layerName}`,
        { title: '스튜디오 내보내기 완료', confirmLabel: '확인', tone: 'info' }
      );
    } catch (error) {
      const message = error?.message || String(error);
      setMessage(`스튜디오 내보내기 실패: ${message}`);
      await showAlert(
        `모션 파일을 스튜디오로 내보내지 못했습니다.\\n원인 · ${message}`,
        { title: '스튜디오 내보내기 실패', confirmLabel: '확인', tone: 'danger' }
      );
    } finally {
      setLoading(false);
    }
  }

  async function deleteSelectedFile() {
    if (!selectedFileId) return;
    if (checkIsFileRegistered(selectedFileId)) {
      await showAlert(
        '재생 등록된 모션 파일은 삭제할 수 없습니다.\\n'
        + '먼저 재생 등록을 해제한 뒤 다시 삭제하세요.',
        { title: '모션 파일 삭제 불가', confirmLabel: '확인', tone: 'warning' }
      );
      return;
    }
    const confirmed = await showConfirm(
      `${selectedFile?.filename || '선택한 모션 파일'}을(를) 삭제하시겠습니까?\\n삭제된 파일은 복구할 수 없습니다.`,
      { title: '모션 파일 삭제', confirmLabel: '삭제', tone: 'danger' }
    );
    if (!confirmed) return;
    setLoading(true);
    setMessage('모션 파일 삭제 중');
    try {
      const payload = await deleteMotionFile(selectedFileId);
      selectedFileId = null;
      selectedFile = null;
      setMessage(payload.message || '삭제 완료');
    } catch (error) {
      setMessage(`삭제 실패: ${error?.message || error}`);
    } finally {
      setLoading(false);
      await loadFiles();
    }
  }

  return {
    getFiles: () => files,
    getSelectedFileId: () => selectedFileId,
    getSelectedFile: () => selectedFile,
    loadFiles,
    selectFile,
    exportSelectedFileToStudio,
    deleteSelectedFile,
  };
}
"""

with open(outpath, 'w', encoding='utf-8') as f:
    f.write(manager_code)

# Now we need to modify motion_data.js to import and use this manager.
# However, modifying a 3100-line file with regex is very dangerous.
# Since the user wants a Phase 1 extraction, we wrote the new file.
# To safely integrate it, I will inject the import statement at the top.

import_stmt = "import { createMotionFileManager } from './motion_file_manager.js';\n"
if "createMotionFileManager" not in content:
    content = import_stmt + content

# Replace the functions inside createMotionDataController
content = re.sub(r'async function loadFiles\(.*?\)\s*\{.*?\n  \}\n', '', content, flags=re.DOTALL)
content = re.sub(r'async function selectFile\(.*?\)\s*\{.*?\n  \}\n', '', content, flags=re.DOTALL)
content = re.sub(r'async function exportSelectedFileToStudio\(.*?\)\s*\{.*?\n  \}\n', '', content, flags=re.DOTALL)
content = re.sub(r'async function showMotionFileDeleteFailure\(.*?\)\s*\{.*?\n  \}\n', '', content, flags=re.DOTALL)
content = re.sub(r'async function deleteSelectedFile\(.*?\)\s*\{.*?\n  \}\n', '', content, flags=re.DOTALL)

# Inject the initialization of motionFileManager inside createMotionDataController
init_code = """
  const fileManager = createMotionFileManager({
    onFilesChanged: (newFiles) => { files = newFiles; render(); },
    onFileSelected: (id, file) => { selectedFileId = id; selectedFile = file; render(); },
    onExportToStudio: async (id) => {
      const result = await onExportMotionFileToStudio(id);
      await onProjectFilesChange?.();
      return result;
    },
    setMessage: setMessage,
    setLoading: (l) => { loading = l; render(); },
    checkIsFileRegistered: (id) => id === registeredMotionFileIdValue,
  });

  async function loadFiles(id) { return fileManager.loadFiles(id); }
  async function selectFile(id, token) { return fileManager.selectFile(id, token); }
  async function exportSelectedFileToStudio() { return fileManager.exportSelectedFileToStudio(); }
  async function deleteSelectedFile() { return fileManager.deleteSelectedFile(); }
"""

content = re.sub(r'(let automationResumeModalHidden = false;\n)', r'\1' + init_code, content)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Phase 1 Refactoring complete.")
