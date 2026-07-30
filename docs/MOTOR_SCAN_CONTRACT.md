# Motor Scan Contract

Contract version: `1`

이 문서는 모터 검색 기능의 영구 완료 조건이다. UI 문구나 내부 구현이 바뀌어도 아래 의미는 바뀌지 않는다.

## 전체 검색

- 한 번의 요청에서 EtherCAT과 Dynamixel 검색을 모두 실행한다.
- 두 검색이 모두 완료되어야 `complete`이다.
- 한 검색만 완료되면 `partial`, 모두 실패하면 `failed`이다.
- `partial`과 `failed`는 API의 전체 성공으로 반환하지 않는다.

## EtherCAT AC Servo

1. 운전 중 재열거가 안전한지 확인한다.
2. `ethercat rescan`을 실행해 기존 Slave 열거정보를 폐기한다.
3. 새 Slave 목록과 식별정보가 안정화될 때까지 기다린다.
4. 각 Slave의 SII EEPROM Alias, Vendor ID, Product Code, Revision, Serial을 읽는다.
5. 각 Slave의 Alias 레지스터 `0x0012`를 읽는다.
6. Master 식별정보와 SII 식별정보가 불일치하면 완료로 처리하지 않는다.

## Dynamixel

1. `/dev/serial/by-id`, `/dev/ttyUSB*`, `/dev/ttyACM*`와 현재 프로젝트에 명시된 실제 포트를 찾는다.
2. 실제 포트를 열지 못하면 Ping 성공으로 처리하지 않는다.
3. Protocol 2.0, 1,000,000bps Broadcast Ping을 실행한다.
4. 유효 ID 전체 `0~252`에 개별 보조 Ping을 실행한다.
5. 실제 Ping 응답 장치만 결과에 포함한다.
6. 프로젝트 설정이나 런타임 상태에만 존재하는 장치를 결과에 추가하지 않는다.

## 결과 증거

각 검색 결과는 다음 정보를 포함해야 한다.

- 고유 `scan_id`
- `scan_contract.version`
- 물리 검색 출처
- transport별 `available`, `complete`, 오류
- 장치별 물리 식별정보
- 단계별 진행 이벤트
- 총 검색 시간

## 장치명과 제어 프로필 분리

- `Vendor ID`, `Product Code`, `Revision Number`, `Serial Number`는 실제
  Slave의 SII EEPROM에서 읽은 물리 식별정보로 저장한다.
- `Order number`와 `Device name`은 각각 `SII Order Number`,
  `SII Device Name`으로 표시하고 실제 명판 모델로 자동 확정하지 않는다.
- 물리 식별정보는 `web_axis_identities`에 저장하며 화면에서 수정할 수 없다.
- 모델과 운전 프로필 연결정보는 `web_axis_profiles`에 별도로 저장한다.
- 검증된 `Vendor ID + Product Code + Revision Number` 카탈로그 항목이 있으면
  모델을 자동 확인할 수 있다. 카탈로그 항목이 없으면 `모델 미확인`으로
  표시하고 사용자가 필요한 축을 선택해 `모델·운전 프로필 설정`에서
  명판 모델을 한 번 확인한다.
- 과거 프로젝트에 저장된 모델값도 명판 재확인 전에는 실행 적용 근거로
  사용하지 않는다.
- SII 문자열이나 검색 순서를 근거로 정격전류·정격토크·속도 프로필을
  자동 선택하지 않는다.
- 검색 결과와 프로젝트에는 현재 응답한 장치 수만 저장하며 AC Servo 5축,
  Dynamixel 특정 개수 같은 고정 조건을 두지 않는다.
- EEPROM Alias가 0인 장치를 Slave Position이나 Control Index만으로 기존
  프로젝트 축에 자동 연결하지 않는다. 케이블 순서 변경으로 Slave Position이
  바뀔 수 있으므로 최초 연결은 사용자가 확인하고 이후에는 직접 읽은
  Serial Number 또는 고유 Alias로 매칭한다.
- 프로젝트의 물리 식별정보가 불완전하면 실행 설정 적용을 중단하고 누락
  필드를 정확히 표시한다.

## 검증 상태

- EtherCAT AC Servo: 현재 실제 연결된 5축을 재열거하고 각 장치의 SII/Alias 응답을 확인하여 실물 검증됨.
- Dynamixel: 자동 테스트만 완료됨. 현재 실제 직렬 포트와 장비가 연결되어 있지 않아 실물 미검증 상태다.
- 위 상태는 장치 종류별 현재 검증 기록이며, 한 장치의 결과를 다른 장치에 적용하지 않는다.
- 모든 하드웨어 종류는 동일하게 실제 장치 응답을 확인해야 해당 종류의 실물 검증이 완료된다.

## 필수 자동 테스트

- EtherCAT `rescan`이 SII 읽기보다 먼저 실행되는지
- SAFEOP/OP 또는 활성 런타임에서 위험한 재열거를 차단하는지
- Dynamixel 결과에 런타임 장치를 주입하지 않는지
- Dynamixel ID `0~252` 전체를 대상으로 하는지
- 한 transport만 성공했을 때 전체 성공이 되지 않는지
- 진행 이벤트가 실제 백엔드 단계에서 생성되는지
- 프로젝트 전환 시 이전 진행 상태가 삭제되는지
