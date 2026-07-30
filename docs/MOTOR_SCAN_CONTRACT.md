# Motor Scan Contract

Contract version: `2`

이 문서는 실제 장치 검색의 영구 불변조건만 정의한다. 프로젝트 저장,
RuntimeSession 생성, 서비스 재시작 및 모터 제어 허용은
`MOTOR_SETUP_ARCHITECTURE.md`에서 정의한다.

## 1. 검색의 책임

- 검색은 현재 물리적으로 응답한 장치만 보고한다.
- 검색은 프로젝트 파일, 이전 검색 결과, 브라우저 상태 및 실행 상태를
  물리 응답의 대체값으로 사용하지 않는다.
- 검색 결과는 프로젝트 파일을 자동으로 변경하지 않는다.
- 검색 결과는 Motor Manager의 실행 설정을 자동으로 변경하지 않는다.
- 장치 개수와 축 번호를 특정 장비 구성이나 5축에 고정하지 않는다.

## 2. 전체 모터 검색

- 한 번의 요청에서 EtherCAT AC Servo와 Dynamixel을 각각 새로 검색한다.
- 두 종류가 모두 완료되면 `complete`이다.
- 한 종류만 완료되면 `partial`이다.
- 모두 실패하면 `failed`이다.
- `partial`과 `failed`를 전체 성공으로 반환하지 않는다.
- 장치 종류별 `available`, `complete`, 장치 수 및 오류를 따로 기록한다.

## 3. EtherCAT AC Servo

1. 모터 동작과 다른 검색·적용 작업이 없는지 확인한다.
2. 활성 Motor Manager가 EtherCAT Master를 사용 중이면 먼저 Motor Manager를
   정지하고 Master가 비활성 상태인지 확인한다.
3. 선택 프로젝트와 실행 프로젝트가 같으면 최신 모터 피드백으로 정지 상태를
   확인한 경우에만 Motor Manager를 정지한다.
4. 선택 프로젝트와 실행 프로젝트가 다르면 이전 프로젝트 Motor Manager를
   정지하고 검색 후 다시 시작하지 않는다. 현재 프로젝트 설정을 저장·적용한
   경우에만 새 RuntimeSession으로 시작한다.
5. `ethercat rescan`으로 기존 열거정보를 폐기한다.
6. Slave 목록이 안정화될 때까지 기다린다.
7. 각 Slave의 SII EEPROM에서 다음 값을 직접 읽는다.
   - Vendor ID
   - Product Code
   - Revision Number
   - Serial Number
   - EEPROM Alias
   - SII Order Number
   - SII Device Name
8. 각 Slave의 Alias 레지스터 `0x0012`와 Slave Position을 읽는다.
9. Master 열거정보와 SII 식별정보가 불일치하면 해당 Slave를 완료로 처리하지 않는다.

### EtherCAT 장치 식별

- EEPROM Alias가 `0`이 아니고 검색 결과에서 유일하면 우선 장치 후보로 사용한다.
- EEPROM Alias가 `0`이거나 중복이면 Serial Number를 장치 후보로 사용한다.
- Slave Position은 연결 순서 변경을 확인하는 값이며 단독 고유 식별값이 아니다.
- Serial Number가 없거나 중복되어 장치를 구분할 수 없으면 `comparison_blocked`로 표시한다.
- Vendor ID, Product Code 및 Revision Number는 지원 모델 카탈로그 조회에 사용한다.
- SII 문자열을 사용자가 입력한 실제 명판 모델로 간주하지 않는다.

## 4. Dynamixel

1. `/dev/serial/by-id`, `/dev/ttyUSB*`, `/dev/ttyACM*`와 현재 프로젝트에
   명시된 실제 포트 후보를 찾는다.
2. 실제 직렬 포트를 열지 못하면 Ping 성공으로 처리하지 않는다.
3. Protocol 2.0, 1,000,000bps Broadcast Ping을 실행한다.
4. ID `0~252`에 개별 보조 Ping을 실행한다.
5. 실제 Ping 응답 장치만 결과에 포함한다.
6. 장치별 포트 식별자, ID, Model Number 및 Firmware Version을 기록한다.

### Dynamixel 장치 식별

- `/dev/serial/by-id` 기반 포트 식별자, Bus ID 및 Model Number 조합을 사용한다.
- `/dev/ttyUSB*`처럼 재부팅 후 달라질 수 있는 경로만으로 장치를 확정하지 않는다.
- 프로젝트 설정이나 이전 런타임 장치를 검색 결과에 추가하지 않는다.

## 5. 모델과 운전 프로필

- 물리 검색 결과와 모터 제어용 운전 프로필은 별도 데이터다.
- 검증된 장치 카탈로그가 있으면 물리 식별정보로 지원 모델과 운전 프로필을
  자동 결정한다.
- 카탈로그에 없는 장치는 `지원 모델 미확인`으로 표시한다.
- 사용자가 임의 모델 문자열을 입력해 지원 모델로 확정하지 않는다.
- 지원 모델 미확인 장치는 프로젝트 초안에 보관할 수 있지만 RuntimeSession
  적용 대상에는 포함할 수 없다.
- 기본 화면에는 모델 표시명, 고유 식별값, 연결 위치 및 비교 상태만 표시한다.
  Vendor/Product/Revision 등 상세값은 상세 보기에서 읽기 전용으로 표시한다.

## 6. 검색 결과 증거

각 검색은 다음 정보를 포함하고 보존한다.

- 고유 `scan_id`
- `scan_contract.version`
- 시작 프로젝트 ID와 프로젝트 세대
- 시작·종료 시각과 총 소요시간
- 장치 종류별 물리 검색 명령과 출처
- 단계별 진행 이벤트
- 장치별 원본 물리 식별정보
- 재시도 횟수와 오류
- 최종 상태 `complete`, `partial`, `failed`

비동기 검색 결과는 검색을 시작한 프로젝트에서만 검토할 수 있다. 검색 도중
프로젝트가 바뀌면 결과를 현재 프로젝트에 적용하지 않는다.

## 7. 필수 자동 테스트

- EtherCAT `rescan`이 SII 읽기보다 먼저 실행되는지
- 활성 Motor Manager 또는 모터 동작 중 위험한 재열거를 차단하는지
- 프로젝트 전환 후 이전 RuntimeSession 피드백이 없어도 이전 Motor Manager를
  정지하고 검색하며, 검색 후 이전 RuntimeSession을 복구하지 않는지
- EtherCAT Alias 레지스터와 SII Alias를 각각 읽는지
- 이전 프로젝트와 런타임 장치를 검색 결과에 주입하지 않는지
- Dynamixel ID `0~252` 전체를 대상으로 하는지
- 한 장치 종류만 성공했을 때 전체 성공이 되지 않는지
- 진행 이벤트가 실제 백엔드 단계에서 생성되는지
- 프로젝트 전환 시 검색 결과가 다른 프로젝트에 적용되지 않는지
- 장치 수가 0개, 1개, 5개 및 5개 초과인 경우 동일 규칙으로 처리되는지

## 8. 현재 검증 상태

- EtherCAT AC Servo · 실제 연결 5축 재열거 및 SII/Alias 응답 확인됨
- Dynamixel · 자동 테스트 완료, 실제 장비 미연결로 실물 미검증
- 위 결과는 장치 종류별 기록이며 다른 장치 종류의 검증으로 확대하지 않는다.
