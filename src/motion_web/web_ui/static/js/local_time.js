/** 이 PC 시각으로 읽은 날짜 · §6-144
 *
 * `toISOString()` 은 UTC 를 준다 · 한국은 UTC+9 라서 자정부터 오전 9시까지
 * UTC 로는 아직 어제다 · 그 시간대에 「1회」 스케줄을 만들면 어제 날짜가
 * 박혀서 영영 안 돌았다.
 *
 * 스케줄 엔진은 이미 지역 시각으로 판단한다 (`datetime.now().astimezone()`) ·
 * 화면만 어긋나 있었다 · 같은 기준으로 맞춘다.
 */

export function motionLocalDateText(value = new Date(), timeZone = undefined) {
  const at = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(at.getTime())) return '';
  if (timeZone) {
    // 시험에서 이 PC 시간대와 무관하게 확인하려고 열어 둔다
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(at);
    return parts;
  }
  const year = at.getFullYear();
  const month = String(at.getMonth() + 1).padStart(2, '0');
  const day = String(at.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}
