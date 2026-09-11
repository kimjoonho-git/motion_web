import {
  fetchMotionFile,
  fetchMotionFiles,
  deleteMotionFile,
} from './api.js';
import { showAlert, showConfirm } from './ui_dialogs.js';

export function createMotionFileManager({
  onFilesChanged,
  onFileSelected,
  onExportToStudio,
  onProjectFilesChange,
  setMessage,
  setLoading,
  checkIsFileRegistered,
}) {
  let files = [];
  let selectedFileId = null;
  let selectedFile = null;
  let fileLoadToken = 0;

  async function loadFiles(targetFileId = selectedFileId, { retried = false } = {}) {
    const loadToken = ++fileLoadToken;
    setLoading(true);
    setMessage('파일 목록 불러오는 중');
    let staleRetry = false;
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
      if (loadToken !== fileLoadToken) return;
      if (error?.staleProjectResponse) {
        // 세대가 바뀌는 순간의 응답은 버린다. 그런데 버리기만 하면 목록이 옛
        // 상태로 굳는다 · 내보내기 직후 새 파일이 안 보이던 원인이다 · §6-54
        // 세대가 정해진 뒤 한 번만 다시 읽는다.
        staleRetry = !retried;
        if (!staleRetry) setMessage('파일 목록을 다시 읽지 못했습니다. 새로고침하세요');
        return;
      }
      setMessage(`파일 목록 실패: ${error?.message || error}`);
    } finally {
      if (loadToken === fileLoadToken) {
        setLoading(false);
        onFilesChanged(files);
        if (staleRetry) void loadFiles(targetFileId, { retried: true });
      }
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
        exportedLayer?.name || file.filename.replace(/\.json$/i, '')
      );
      setMessage(`스튜디오 내보내기 완료: ${file.filename} → ${layerName}`);
      await showAlert(
        `모션 파일을 스튜디오의 독립 레이어로 내보냈습니다.\n`
        + `파일 · ${file.filename}\n레이어 · ${layerName}`,
        { title: '스튜디오 내보내기 완료', confirmLabel: '확인', tone: 'info' }
      );
    } catch (error) {
      const message = error?.message || String(error);
      setMessage(`스튜디오 내보내기 실패: ${message}`);
      await showAlert(
        `모션 파일을 스튜디오로 내보내지 못했습니다.\n원인 · ${message}`,
        { title: '스튜디오 내보내기 실패', confirmLabel: '확인', tone: 'danger' }
      );
    } finally {
      setLoading(false);
    }
  }

  async function showMotionFileDeleteFailure(message) {
    await showAlert(
      message,
      { title: '모션 파일 삭제 불가', confirmLabel: '확인', tone: 'warning' },
    );
  }

  async function deleteSelectedFile() {
    if (!selectedFileId) return;
    if (checkIsFileRegistered(selectedFileId)) {
      await showMotionFileDeleteFailure(
        '재생 등록된 모션 파일은 삭제할 수 없습니다.\n'
        + '먼저 재생 등록을 해제한 뒤 다시 삭제하세요.',
      );
      return;
    }
    const confirmed = await showConfirm(
      `${selectedFile?.filename || '선택한 모션 파일'}을(를) 삭제하시겠습니까?\n삭제된 파일은 복구할 수 없습니다.`,
      { title: '모션 파일 삭제', confirmLabel: '삭제', tone: 'danger' }
    );
    if (!confirmed) return;
    setLoading(true);
    setMessage('모션 파일 삭제 중');
    try {
      const payload = await deleteMotionFile(selectedFileId);
      // 서버도 등록 여부를 검사한다 · 다른 브라우저가 방금 등록했으면 여기서
      // 막힌다. HTTP 200 에 success:false 로 오므로 예외가 아니다 · 이 검사를
      // 빼면 삭제되지 않았는데 선택이 풀린다.
      if (payload.success === false) {
        setMessage(payload.message || '모션 파일을 삭제하지 못했습니다');
        await showMotionFileDeleteFailure(
          payload.message || '재생 등록된 모션 파일은 삭제할 수 없습니다.',
        );
        return;
      }
      selectedFileId = null;
      selectedFile = null;
      setMessage(payload.message || '삭제 완료');
      await onProjectFilesChange?.();
    } catch (error) {
      const message = `삭제 실패: ${error?.message || error}`;
      setMessage(message);
      await showMotionFileDeleteFailure(message);
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
