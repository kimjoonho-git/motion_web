# EtherCAT 2개 포트 설정 체크리스트

## 현재 상태

- 서보 드라이버 전원 · 꺼짐
- EtherCAT Master 0 · `enp1s0` 등록됨
- EtherCAT Master 1 · `enp2s0` 미등록
- 현재 `/etc/ethercat.conf`
  - `MASTER0_DEVICE="enp1s0"`
  - `#MASTER1_DEVICE=""`
  - `UPDOWN_INTERFACES="enp1s0"`

## PC 설정

터미널에서 다음 파일을 연다.

```bash
sudo nano /etc/ethercat.conf
```

다음과 같이 수정한다.

```text
MASTER0_DEVICE="enp1s0"
MASTER1_DEVICE="enp2s0"

UPDOWN_INTERFACES="enp1s0 enp2s0"
```

저장 순서:

1. `Ctrl+O`
2. `Enter`
3. `Ctrl+X`

## NetworkManager 격리

EtherCAT 전용 포트에서 DHCP 또는 일반 유선 연결을 시도하지 않도록
`enp1s0`, `enp2s0`을 NetworkManager 관리 대상에서 제외한다.

```bash
sudo nano /etc/NetworkManager/conf.d/10-ethercat-unmanaged.conf
```

다음 내용을 저장한다.

```ini
[keyfile]
unmanaged-devices=interface-name:enp1s0;interface-name:enp2s0
```

재부팅 후 다음 명령으로 두 포트가 모두 `unmanaged`인지 확인한다.

```bash
nmcli device status
```

## 재부팅

```bash
sudo reboot
```

## 재부팅 후 확인

서보 전원이 꺼져 있어도 다음 명령에서 `Master0`, `Master1`이 표시되어야 한다.

```bash
ethercat master
```

## 서보 하드웨어 연결

1. 서보 드라이버 전원을 끈 상태에서 배선한다.
2. PC `enp1s0` → 첫 번째 체인의 첫 서보 `EtherCAT IN`
3. 첫 번째 체인의 서보 5대를 `OUT → IN`으로 직렬 연결한다.
4. PC `enp2s0` → 두 번째 체인의 첫 서보 `EtherCAT IN`
5. 두 번째 체인의 서보 5대를 `OUT → IN`으로 직렬 연결한다.
6. 일반 공유기나 네트워크 스위치를 EtherCAT 선 사이에 연결하지 않는다.
7. 두 EtherCAT 체인을 서로 연결하지 않는다.

## 서보 전원 투입 후 확인

```bash
ethercat slaves -m 0
ethercat slaves -m 1
```

예상 결과:

- Master 0 · 서보 5축
- Master 1 · 서보 5축
- 각 서보 상태 · `OP`

## 새 프로젝트 등록

1. 웹 → `프로젝트·장비`
2. `시스템 정보`
3. `새 프로젝트`
4. 프로젝트 이름 입력
5. `프로젝트·장비` → `모터 관리`
6. `전체 모터 검색`
7. 검색된 축 선택
8. `선택 축 추가`
9. 축 번호와 축 이름 확인
10. `변경 내용 검증 후 저장`
11. `설정 적용 및 재시작`

권장 축 번호:

- Master 0 · `0~4`
- Master 1 · `5~9`

## Codex에서 이어서 진행할 때

다음과 같이 요청한다.

```text
docs/DUAL_ETHERCAT_SETUP_CHECKLIST.md를 확인하고 EtherCAT 2개 포트 설정부터 이어서 진행해줘.
```
