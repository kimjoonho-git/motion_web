# Motion Web

ROS 2 기반 모션 제어 프로그램입니다. 웹 모니터링·설정, 모션 편집·실행과
저수준 모터 제어 계층을 하나의 작업공간에서 빌드합니다.

## 구성

```text
Web UI
  ↓
motion_web_bridge
  ↓
motion_studio / motion_supervisor / motion_state_monitor
  ↓
motion_system motor_manager_node
  ↓
EtherLab·IgH EtherCAT / Dynamixel
```

| 구성요소 | 관리 방식 | 역할 |
|---|---|---|
| Motion Web | 상위 Git 저장소 | 웹 UI, 웹 API, 프로젝트·서비스 관리 |
| Motion Control Studio | 상위 저장소에 통합 | 모션 편집, 실행, 상태·안전 관리 |
| Motion Coordination | 상위 저장소의 독립 ROS 2 패키지 | PC 간 상태 공유·실행 조정 |
| Motion System | Git 서브모듈 | Motor Manager와 저수준 모터 드라이버 |
| EtherLab/IgH EtherCAT | PC에 별도 설치 | AC Servo EtherCAT 통신 |

작업공간 구조:

```text
ros2_ws/
├── config/                         # PC 전역 설정 (프로젝트와 분리)
│   └── motion_coordination.example.yaml
├── scripts/                        # pull·빌드·재시작·커밋 편의 스크립트
├── docs/                           # 운영·DDS 검증 문서
├── src/motion_web
│   ├── web_bridge
│   └── web_ui
├── src/motion_control_studio
│   ├── motion_control
│   └── motion_studio
├── src/motion_coordination         # PC 간 상태 공유·실행 조정
├── src/motion_coordination_interfaces  # DDS 메시지 정의
└── src/motion_system               # Git submodule
```

## 검증 기준 버전

아래 값은 **2026-08-11** 기준 개발 PC에서 확인한 조합입니다. EtherCAT
커널과 NIC 드라이버는 새 PC의 하드웨어에 맞아야 합니다.

| 대상 | 검증 버전 |
|---|---|
| Ubuntu | 22.04.5 LTS |
| Linux kernel | 6.8.0-124-generic |
| ROS 2 | Humble (`/opt/ros/humble`) |
| Python | 3.10.12 |
| Git | 2.34.1 |
| CMake | 3.22.1 |
| colcon-core | 0.20.1 |
| colcon-common-extensions (apt) | 0.3.0 |
| FastAPI | 0.63.0 |
| Uvicorn | 0.15.0 |
| PyYAML | 5.4.1 |
| EtherLab/IgH EtherCAT Master | 1.6.9 (`1.6.9-8-gbeb2bf07`) |
| Motion Web packages | 0.1.0 |
| Motion Control Studio packages | 0.1.0 |
| Motion Coordination packages | 0.1.0 |
| Motion Coordination Interfaces | 0.1.0 |
| Motion System | 서브모듈 커밋 `5ec1909` |

애플리케이션 `package.xml` 버전(0.1.0)만으로는 전체 호환 조합을 식별할 수
없습니다. 실제 설치 조합은 **상위 Git 커밋**과 그 커밋이 기록한 **Motion
System 서브모듈 커밋**을 함께 사용합니다. 위 표 작성 시점의 상위 저장소
기준 커밋은 `2a271d6` (`main`)입니다.

운영 브랜치 · `main`

## Git 저장소

- 전체 설치 저장소: `https://github.com/kimjoonho-git/motion_web.git`
- Motion System 서브모듈: `https://github.com/kimjoonho-git/motion_system_ros2.git`
- Motion System 원본: `https://github.com/SeonilChoi/motion_system.git`

Motion Control Studio는 상위 저장소에 통합되어 있으므로 별도로 복제하지
않습니다. Motion System과 그 내부 의존 저장소는 `--recurse-submodules`로
받습니다.

## 1. 기본 환경 준비

Ubuntu 22.04와 ROS 2 Humble을 먼저 설치합니다. 다음은 현재 작업공간에서
직접 사용하는 기본 도구와 Python 실행 패키지입니다.

```bash
sudo apt update
sudo apt install -y \
  git build-essential cmake \
  python3-rosdep python3-colcon-common-extensions \
  python3-fastapi python3-uvicorn python3-yaml chrony \
  btop ttyd
```

Dynamixel 직렬 통신과 MIDI 장치를 사용하는 계정에는 필요한 그룹 권한을
추가합니다. 변경 후에는 로그아웃하거나 재부팅해야 적용됩니다.

```bash
sudo usermod -aG dialout,audio "$USER"
```

## 2. EtherLab/IgH EtherCAT 준비

EtherLab은 Git 작업공간에 포함되지 않으며 PC마다 별도로 설치합니다. 현재
검증 버전은 1.6.9입니다.

먼저 EtherCAT 전용 NIC 이름과 커널 드라이버를 확인합니다.

```bash
ip -br link
ethtool -i <EtherCAT-NIC>
```

EtherLab 소스 빌드에는 다음 도구가 필요합니다.

```bash
sudo apt install -y autoconf automake libtool pkg-config build-essential git
git clone https://gitlab.com/etherlab.org/ethercat.git
```

빌드할 때는 해당 PC에서 확인한 NIC 드라이버에 맞는 EtherLab 1.6 계열 커널
모듈을 활성화해야 합니다. 자세한 빌드 옵션은
[`EtherCAT 설치 문서`](src/motion_system/lib/motor_manager/communications/ethercat/README.md)를
따릅니다.

`/etc/ethercat.conf`의 장치 이름은 예시를 그대로 복사하지 말고 새 PC에서
확인한 값으로 지정합니다.

```text
MASTER0_DEVICE="<EtherCAT-NIC>"
DEVICE_MODULES="<generic 또는 해당 EtherCAT 드라이버>"
UPDOWN_INTERFACES="<EtherCAT-NIC>"
```

설치·설정 후 확인:

```bash
sudo systemctl enable --now ethercat
ethercat version
ethercat master
ethercat slaves
```

`ethercat slaves`의 실제 장치 표시는 서보 전원, 배선과 Slave 연결 상태에
따라 달라집니다. 일반 LAN과 EtherCAT에는 서로 다른 NIC를 사용합니다.

## 3. 전체 소스 설치

상위 저장소(`main`)와 모든 서브모듈을 한 번에 받습니다.

```bash
cd ~
git clone -b main --recurse-submodules \
  https://github.com/kimjoonho-git/motion_web.git ros2_ws
cd ~/ros2_ws
```

이미 복제한 저장소라면 다음 명령으로 `main`과 기록된 서브모듈 버전을
맞춥니다.

```bash
cd ~/ros2_ws
git fetch origin
git checkout main
git pull origin main
git submodule update --init --recursive
```

DDS 그룹 연동을 처음 켜는 PC는 예시 설정을 복사해 PC별로 편집합니다.
프로젝트 파일과 분리된 전역 설정입니다.

```bash
cp config/motion_coordination.example.yaml config/motion_coordination.yaml
# pc_id, display_name, group_id, dds_domain_id 등을 PC마다 다르게 지정
```

웹 UI의 `장비 연동 상태 → DDS 그룹 연동`에서 저장해도 같은 파일이
갱신됩니다.

## 4. ROS 의존성 설치와 빌드

### 4-1. 최초 설치 (PC에 작업공간을 처음 만든 경우)

`rosdep`을 처음 사용하는 PC에서만 초기화한 뒤 의존성을 설치합니다.

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
sudo rosdep init    # 이미 초기화되어 있으면 생략
rosdep update
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

### 4-2. 코드 갱신 (이미 설치된 PC)

코드를 새로 다운로드받고 빌드 및 재시작하는 가장 쉬운 방법은 **원클릭 자동 업데이트 스크립트**를 사용하는 것입니다. 패키지별 빌드나 서비스 재시작 명령어를 직접 입력할 필요가 없습니다.

```bash
~/ros2_ws/src/motion_web/update.sh
```

동작 · `git pull` + `colcon build` + `motion-control.service` 재시작이 자동으로 연속 진행되며 설치 경로를 스스로 계산하므로 다른 PC에서도 동일하게 사용할 수 있습니다.

(기존 `scripts/sync_branch.sh` 스크립트를 통한 특정 브랜치 동기화 기능도 여전히 지원됩니다.)

`motion-motor.service`는 스크립트에서 재시작하지 않습니다. 모터 설정을 바꾼 뒤에는 별도로 재시작합니다.

```bash
systemctl --user restart motion-motor.service
```

### 4-3. 커밋·푸시 (개발 PC)

```bash
cd ~/ros2_ws
MOTION_WEB_BRANCH=main bash scripts/commit_branch.sh "커밋 메시지" --push
```

`--push` 없이 커밋만 하려면 마지막 `--push`를 빼면 됩니다. 이 스크립트는
`motion_system` submodule 변경이 있으면 중단합니다.

수동으로 커밋할 때는 [8. Git 작업 방법](#8-git-작업-방법)을 따릅니다.

## 5. 자동실행 등록

빌드가 끝난 뒤 사용자 서비스를 한 번 등록합니다.

```bash
cd ~/ros2_ws
bash src/motion_web/web_bridge/deploy/install_user_service.sh
```

로그인 전 부팅 단계부터 실행하려면 PC마다 최초 한 번 다음 설정을 추가합니다.

```bash
sudo loginctl enable-linger "$(id -un)"
```

이후에는 재부팅할 때 다음 서비스가 자동으로 실행됩니다.

- `motion-control.service`: 웹·프로젝트·모션 제어 서비스
- `motion-motor.service`: 검증된 프로젝트 모터 실행 설정이 있을 때 Motor Manager
- `motion-coordination.service`: PC 간 DDS 그룹 상태 공유·실행 조정

웹 UI의 **PC 성능 (btop)** 탭을 사용하기 위해 `btop`을 자동 실행하려면 아래 명령어로 서비스를 등록합니다.

```bash
cd ~/ros2_ws
cp src/motion_web/web_bridge/deploy/motion-btop.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now motion-btop.service
```

새 PC에 검증된 모터 실행 설정이 없으면 웹은 실행되지만 Motor Manager 시작은
보류됩니다. 브라우저 창은 자동으로 열리지 않습니다.

## 6. 실행과 상태 확인

```bash
systemctl --user start motion-control.service
systemctl --user status motion-control.service motion-motor.service motion-coordination.service
```

로그 확인:

```bash
journalctl --user -u motion-control.service -n 100
journalctl --user -u motion-motor.service -n 100
journalctl --user -u motion-coordination.service -n 100
```

웹 접속:

- 현재 PC: `http://localhost:8000`
- 다른 PC: `http://<이-PC의-IP>:8000`

서비스 등록 상태 확인:

```bash
loginctl show-user "$(id -un)" -p Linger
systemctl --user is-enabled motion-control.service motion-motor.service motion-coordination.service
ss -ltnp | grep ':8000'
```

## 7. 네트워크 주의사항

- PC마다 서로 다른 IP 주소와 hostname을 사용합니다.
- 고정 IP 또는 공유기의 DHCP 예약을 권장합니다.
- 서로 다른 PC는 같은 `8000` 포트를 사용해도 IP가 다르면 충돌하지 않습니다.
- 같은 PC에서 다른 프로그램이 `8000` 포트를 사용하면 웹 서비스가 시작되지
  않습니다.
- 웹은 `0.0.0.0:8000`에 바인딩되므로 신뢰할 수 있는 내부망에서만 사용합니다.
- 방화벽은 운영 PC가 있는 내부 대역만 허용하고 인터넷에 직접 노출하지 않습니다.
- `motion-control`과 `motion-motor`는 `ROS_LOCALHOST_ONLY=1`로 PC 내부에
  격리합니다.
- `motion-coordination`만 `ROS_LOCALHOST_ONLY=0`으로 실행해 같은 Wi-Fi의
  다른 PC와 typed ROS 2 DDS 메시지를 주고받습니다.
- PC 간에는 그룹 참가 상태·고수준 실행 트리거·완료·오류만 전송합니다. 프로젝트
  파일, 모션 데이터와 모터 목표값은 전송하지 않습니다.
- 각 PC 웹의 `장비 연동 상태 → DDS 그룹 연동`에서 서로 다른 `이 PC ID`, 같은
  `그룹 ID`와 `DDS Domain ID`를 저장한 뒤 사용자가 직접 `그룹 참가`를 누릅니다.
- 고정 마스터는 없으며 `그룹 모션 시작`을 누른 PC가 해당 실행의 임시 진행 PC가
  됩니다. 그룹 실행은 참가자 목록이 모든 PC에서 일치할 때만 시작합니다.
- 그룹 실행 동기화에 시스템 UTC·NTP는 사용하지 않습니다. 실행마다 DDS 왕복
  측정으로 각 PC의 monotonic 트리거를 맞춥니다. (`chrony` 패키지는 OS
  시간 유지용이며 그룹 트리거 동기화와는 무관합니다.)
- 같은 Wi-Fi 구간에서 ROS 2 DDS UDP 통신을 허용해야 하며, 무선 공유기의 AP
  isolation 기능은 꺼야 합니다.
- 동기 실행 코드는 포함되지만 실제 여러 PC와 모터의 시작 오차는 실물 검증 전입니다.

## 8. Git 작업 방법

Motion Web, Motion Control Studio, Motion Coordination과 관련 문서·설정
예시는 상위 저장소(`main`)에서 함께 커밋합니다.

```bash
cd ~/ros2_ws
git add \
  README.md docs scripts config \
  src/motion_web \
  src/motion_control_studio \
  src/motion_coordination \
  src/motion_coordination_interfaces
git commit -m "변경 내용"
git push origin main
```

Motion System을 수정하지 않았다면 서브모듈 커밋은 필요하지 않습니다. Motion
System을 명시적으로 수정할 때만 하위 저장소에서 먼저 커밋·푸시하고, 상위
저장소에서 변경된 서브모듈 커밋 위치를 기록합니다.

관련 문서:

- [`docs/SYSTEM_OPERATION_FLOW.md`](docs/SYSTEM_OPERATION_FLOW.md) — 실행·DDS
  그룹 연동 흐름
- [`docs/DDS_MULTI_PC_VALIDATION.md`](docs/DDS_MULTI_PC_VALIDATION.md) — 2PC
  실물 검증 절차

## 제외 대상

다음 실행 데이터와 생성 파일은 Git에 포함하지 않습니다.

```text
build/
install/
log/
backups/
motion_projects/
motion_data/
```
