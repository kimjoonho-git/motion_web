import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const source = readFileSync(
  new URL('../static/js/motion_file_manager.js', import.meta.url),
  'utf8',
);

test('늦은 응답을 버린 뒤 한 번은 다시 읽는다', () => {
  // 버리기만 하면 목록이 옛 상태로 굳는다 · 내보내기 직후 새 파일이 보이지
  // 않던 원인이다 · §6-54
  assert.match(source, /staleProjectResponse/);
  assert.match(source, /staleRetry = !retried/);
  assert.match(source, /loadFiles\(targetFileId, \{ retried: true \}\)/);
});

test('다시 읽어도 실패하면 사용자에게 알린다', () => {
  // 조용히 옛 목록을 보여주면 사용자는 파일이 사라진 줄 안다
  assert.match(source, /파일 목록을 다시 읽지 못했습니다/);
});

test('재시도는 한 번뿐이다', () => {
  // retried 를 넘기므로 두 번째 실패에서는 staleRetry 가 false 가 된다
  assert.match(source, /async function loadFiles\(targetFileId = selectedFileId, \{ retried = false \} = \{\}\)/);
  const retries = source.match(/retried: true/g) || [];
  assert.equal(retries.length, 1);
});
