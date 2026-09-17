import assert from 'node:assert/strict';
import test from 'node:test';

/**
 * 서버가 앞서 가면 따라간다 · §6-140
 *
 * 모터축 2개를 지우고 적용하자 「모션 실행」의 재생 등록이 빈칸이 됐다 ·
 * 파일에도 API 응답에도 등록은 멀쩡히 있었다.
 *
 * 모터 적용은 프로젝트 세대를 올린다 · 그 순간 열려 있던 화면은 옛 세대를
 * 들고 있어서, 그다음 요청의 응답이 전부 「이전 프로젝트의 늦은 응답」으로
 * 버려졌다 · 버리는 쪽은 조용히 `return` 만 해서 화면이 빈칸으로 굳었다.
 *
 * 세대가 **뒤진** 응답은 버리는 게 맞다 · **앞선** 응답은 따라가야 한다.
 */

const API = new URL('../static/js/api.js', import.meta.url);

function installFetch({ responseGeneration, previousGeneration = null }) {
  globalThis.window = globalThis.window || {};
  globalThis.window.fetch = async () => ({
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => ({
      success: true,
      project_generation: responseGeneration,
      ...(previousGeneration === null
        ? {}
        : { previous_project_generation: previousGeneration }),
    }),
    text: async () => '',
  });
  globalThis.window.setTimeout = globalThis.setTimeout;
  globalThis.window.clearTimeout = globalThis.clearTimeout;
}

async function freshApi() {
  // 모듈 수준 상태(세대·처리기)를 시험마다 새로 받는다
  return import(`${API.href}?t=${Math.random()}`);
}

test('세대가 앞선 응답은 따라가고 화면에 알린다', async () => {
  installFetch({ responseGeneration: 91 });
  const api = await freshApi();
  api.setProjectGeneration(90);

  const seen = [];
  api.setProjectAheadHandler((generation) => seen.push(generation));

  await assert.rejects(
    () => api.fetchMotionMappings(),
    (error) => {
      assert.equal(error.projectMovedAhead, 91);
      assert.equal(error.staleProjectResponse, true);
      return true;
    },
  );
  assert.deepEqual(seen, [91], '앞서 간 것을 화면에 알리지 않았다');
  assert.equal(api.getProjectGeneration(), 91, '새 세대를 따라가지 않았다');
});

test('세대가 뒤진 응답은 그대로 버린다', async () => {
  // 늦게 도착한 옛 프로젝트의 응답 · 이것까지 따라가면 되돌아간다
  installFetch({ responseGeneration: 89 });
  const api = await freshApi();
  api.setProjectGeneration(90);

  const seen = [];
  api.setProjectAheadHandler((generation) => seen.push(generation));

  await assert.rejects(
    () => api.fetchMotionMappings(),
    (error) => {
      assert.equal(error.staleProjectResponse, true);
      assert.equal(error.projectMovedAhead, undefined);
      return true;
    },
  );
  assert.deepEqual(seen, [], '뒤진 응답을 따라갔다');
  assert.equal(api.getProjectGeneration(), 90, '세대가 뒤로 갔다');
});

test('정상적인 세대 전환은 건드리지 않는다', async () => {
  // 이 화면이 스스로 올린 경우 · previous 가 붙어 온다
  installFetch({ responseGeneration: 91, previousGeneration: 90 });
  const api = await freshApi();
  api.setProjectGeneration(90);

  const payload = await api.fetchMotionMappings();

  assert.equal(payload.success, true);
  assert.equal(api.getProjectGeneration(), 91);
});
