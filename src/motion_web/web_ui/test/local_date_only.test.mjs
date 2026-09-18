import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

const JS = new URL('../static/js/', import.meta.url);

/**
 * 화면의 날짜는 **이 PC 시각** 기준이다 · §6-144
 *
 * `toISOString()` 은 UTC 를 준다 · 한국은 UTC+9 라서 자정부터 오전 9시까지는
 * UTC 로 아직 어제다.
 *
 *     한국 시각    2026-09-18 08:56
 *     toISOString  2026-09-17T23:56Z
 *
 * 그래서 오전 9시 전에 「1회」 스케줄을 만들면 **어제 날짜**가 박혔다 ·
 * 「1회」는 그 날짜에만 도니까 영영 안 돌았다 · 오후에 만들면 멀쩡해서
 * 한참 몰랐다.
 *
 * 시각은 이미 지역 시각으로 다루고 있었다 (스케줄 엔진의
 * `datetime.now().astimezone()`) · 날짜만 어긋나 있었다.
 */
test('날짜를 UTC 로 만드는 곳이 없다', () => {
  const offenders = [];
  for (const name of readdirSync(JS).filter((file) => file.endsWith('.js'))) {
    const source = readFileSync(new URL(name, JS), 'utf8');
    for (const line of source.split('\n')) {
      if (/toISOString\(\)[\s\S]*split\('T'\)/.test(line)) {
        offenders.push(`${name}: ${line.trim()}`);
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `UTC 날짜를 쓰는 곳:\n  ${offenders.join('\n  ')}\n`
    + '이 PC 시각으로 만드세요 (지역 연·월·일).',
  );
});

test('스케줄 날짜 칸은 이 PC 의 오늘을 채운다', async () => {
  const { motionLocalDateText } = await import(
    new URL('local_time.js', JS)
  );
  // UTC 로는 어제인 시각 · 한국이면 9월 18일 아침
  const morning = new Date('2026-09-17T23:56:12+00:00');

  assert.equal(
    motionLocalDateText(morning, 'Asia/Seoul'),
    '2026-09-18',
    'UTC 를 그대로 쓰면 어제가 된다',
  );
  assert.equal(
    motionLocalDateText(new Date('2026-09-18T05:00:00+00:00'), 'Asia/Seoul'),
    '2026-09-18',
  );
});
