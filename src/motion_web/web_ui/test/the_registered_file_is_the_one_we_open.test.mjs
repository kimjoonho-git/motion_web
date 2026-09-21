// 화면은 **프로젝트가 등록한** 모션축 설정 파일을 연다 · §6-238
//
// 전에는 목록의 첫 번째를 골랐다 · 파일이 하나뿐인 프로젝트에서는 우연히
// 맞았고, 그래서 오래 들키지 않았다 · 여기서는 글자를 훑지 않고 **판단을
// 직접 돌려** 본다.

import assert from 'node:assert/strict';
import test from 'node:test';
import { mappingFileToOpen } from '../static/js/motion_data.js';

const files = [
  { id: 'first.yaml' },
  { id: 'motion_axis.yaml' },
  { id: 'old.yaml' },
];

test('등록된 파일을 연다 · 첫 번째가 아니다', () => {
  assert.equal(
    mappingFileToOpen({ files, activeFileId: 'motion_axis.yaml' }),
    'motion_axis.yaml',
  );
});

test('등록된 것이 없으면 첫 번째로 물러선다', () => {
  assert.equal(mappingFileToOpen({ files, activeFileId: '' }), 'first.yaml');
});

test('등록된 것이 목록에 없으면 첫 번째로 물러선다', () => {
  assert.equal(
    mappingFileToOpen({ files, activeFileId: 'deleted.yaml' }),
    'first.yaml',
  );
});

test('목록이 비면 열 것이 없다 · 그때는 새로 만들어 저장하는 자리다', () => {
  assert.equal(mappingFileToOpen({ files: [], activeFileId: '' }), '');
  assert.equal(mappingFileToOpen({ files: [], activeFileId: 'motion_axis.yaml' }), '');
});

test('빈 값·이상한 값이 와도 터지지 않는다', () => {
  assert.equal(mappingFileToOpen(), '');
  assert.equal(mappingFileToOpen({}), '');
  assert.equal(mappingFileToOpen({ files: null, activeFileId: null }), '');
  assert.equal(mappingFileToOpen({ files: [{}, { id: '  ' }] }), '');
  assert.equal(mappingFileToOpen({ files: [{ id: ' a.yaml ' }] }), 'a.yaml');
});

test('앞뒤 공백은 같은 파일로 본다', () => {
  assert.equal(
    mappingFileToOpen({ files: [{ id: 'motion_axis.yaml' }], activeFileId: ' motion_axis.yaml ' }),
    'motion_axis.yaml',
  );
});
