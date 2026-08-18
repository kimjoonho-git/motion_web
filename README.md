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

## Ubuntu만 설치된 새 PC 설치

새 PC는 아래 명령만 먼저 실행합니다. 웹 화면과 DDS 그룹 연동 테스트는 이 절차로
준비됩니다. 실제 모터 제어 PC는 설치 후 [2. EtherLab/IgH EtherCAT 준비](#2-etherlabigh-ethercat-준비)를
추가로 진행합니다.

### A. 코드 받기

```bash
cd ~
git clone -b main --recurse-submodules https://github.com/kimjoonho-git/motion_web.git ros2_ws
cd ~/ros2_ws
```

### B. 설치 실행

```bash
bash src/motion_web/install.sh
```

설치 스크립트가 처리하는 항목:

- ROS 2 Humble 저장소 등록
- Git 최신 코드 수신
- 필수 프로그램 설치
- 사용자 권한 설정
- rosdep 설치·갱신
- 전체 colcon 빌드
- 자동실행 서비스 등록
- 실시간 우선순위 권한 설정 확인
- ROS 2 daemon 초기화
- 서비스 적용

`실시간 우선순위 권한 설정 필요` 또는 `재부팅 후 다시 실행` 안내가 나오면 재부팅합니다.

```bash
sudo reboot
```

재부팅 후 같은 설치 명령을 다시 실행합니다.

```bash
cd ~/ros2_ws
bash src/motion_web/install.sh
```

### C. 실행 확인

```bash
systemctl --user status --no-pager motion-control.service motion-coordination.service
```

```text
http://localhost:8000
```

### D. DDS 그룹 연동 첫 설정

연동할 모든 PC에서 웹 화면을 열고 같은 순서로 설정합니다.

1. `장비 연동 상태` 열기
2. `DDS 그룹 연동` 열기
3. `이 PC ID` 입력
4. `표시 이름` 입력
5. `그룹 ID` 입력
6. `DDS Domain ID` 입력
7. `저장`
8. `그룹 참가`

입력 예시:

```text
1번 PC
이 PC ID: pc-a
표시 이름: PC A
그룹 ID: stage-a
DDS Domain ID: 23

2번 PC
이 PC ID: pc-b
표시 이름: PC B
그룹 ID: stage-a
DDS Domain ID: 23
```

규칙:

```text
이 PC ID: PC마다 다르게 입력
표시 이름: PC마다 다르게 입력
그룹 ID: 연동할 PC끼리 같게 입력
DDS Domain ID: 연동할 PC끼리 같게 입력
```

성공 기준:

```text
각 PC에서 그룹 참가 상태 표시
상대 PC가 peer 목록에 표시
오류 없음
```

### G. 기존 PC 업데이트

작업공간 경로를 모르면 먼저 찾습니다.

```bash
find ~ -maxdepth 3 -type d -path '*/src/motion_web' 2>/dev/null
```

출력이 `/home/user/ros2_ws/src/motion_web`이면 작업공간은
`/home/user/ros2_ws`입니다.

```bash
cd <ros2_ws_경로>
git fetch origin
git checkout main
git pull origin main
git submodule update --init --recursive
./src/motion_web/update.sh
systemctl --user status --no-pager motion-control.service motion-coordination.service
```

### H. Codex에게 맡기는 문장

다른 PC의 Codex에게 맡길 때는 아래 문장을 그대로 전달합니다.

```text
Ubuntu만 설치된 새 PC 기준으로 이 README의 설치 절차를 진행해줘.
웹 화면과 DDS 그룹 연동 테스트가 가능할 때까지 설치해줘.
모터 제어용 EtherCAT 설정은 NIC 이름, MAC 주소, 드라이버를 확인한 뒤 멈추고 사용자 확인을 받아줘.
기존 설치가 있으면 작업공간 경로를 찾아서 업데이트 절차로 진행해줘.
src/motion_system은 명시 요청 없으면 수정하지 마.
실행 검증과 실물 검증을 구분해서 보고해줘.
```

## 0. ROS 2 Humble 설치

Ubuntu 22.04 데스크톱 환경에 ROS 2 Humble을 설치합니다. 이미 설치된 PC라면 이 단계를 건너뜁니다.

```bash
# 1. 로케일 설정 (UTF-8)
sudo apt update && sudo apt install locales
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
export LANG=en_US.UTF-8

# 2. Ubuntu Universe 저장소 활성화
sudo apt install software-properties-common
sudo add-apt-repository universe

# 3. ROS 2 GPG 키 및 저장소 추가
sudo apt update && sudo apt install curl -y
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

# 4. ROS 2 패키지 설치
sudo apt update
sudo apt install -y ros-humble-desktop
```

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

## 1-1. Ubuntu 원격 접속 (GNOME RDP) 설정

여러 대의 PC에 RDP 접속 환경을 일관되게 구성하려면 xrdp 대신 Ubuntu 기본 GNOME RDP를 사용합니다.

1. **자동 로그인 켜기**: `설정 → 사용자`에서 Automatic Login: ON
2. **화면 잠금 끄기**: `설정 → Privacy → Screen Lock`에서 Automatic Screen Lock: OFF, Suspend 후 화면 잠금: OFF
3. **절전 모드 끄기**: `설정 → Power`에서 Screen Blank: Never, Automatic Suspend: Off
4. **원격 데스크톱 켜기**: `설정 → Sharing → Remote Desktop`에서 Remote Desktop: ON, Remote Control: ON
5. **Keyring 암호 해제**: `seahorse` 실행 → Default keyring 자체의 Change Password 실행 → 기존 암호 입력 후 새 암호는 빈칸으로 저장 (경고 허용)

**부팅 시 RDP 자동 실행 서비스 등록**

```bash
mkdir -p ~/.local/bin ~/.config/systemd/user

# 1. RDP 자격증명 스크립트 생성 (<사용자명>과 <RDP비밀번호> 변경)
cat << 'EOF' > ~/.local/bin/setup-rdp-after-login.sh
#!/bin/bash
sleep 5
/usr/bin/grdctl rdp set-credentials <사용자명> '<RDP비밀번호>'
/usr/bin/grdctl rdp enable
/bin/systemctl --user restart gnome-remote-desktop.service
EOF
chmod 700 ~/.local/bin/setup-rdp-after-login.sh

# 2. 사용자 Systemd 서비스 등록
cat << 'EOF' > ~/.config/systemd/user/rdp-after-login.service
[Unit]
Description=Configure GNOME RDP after automatic login
After=gnome-remote-desktop.service
Wants=gnome-remote-desktop.service

[Service]
Type=oneshot
ExecStart=%h/.local/bin/setup-rdp-after-login.sh
RemainAfterExit=yes

[Install]
WantedBy=default.target
EOF

# 3. 서비스 활성화
systemctl --user daemon-reload
systemctl --user enable rdp-after-login.service
```

설정 후 재부팅하면 사용자 조작 없이 백그라운드에서 GNOME RDP가 자동 실행됩니다.

## 2. EtherLab/IgH EtherCAT 준비

EtherLab은 Git 작업공간에 포함되지 않으며 PC마다 별도로 설치합니다. 현재
검증 버전은 1.6.9입니다.

먼저 EtherCAT 전용 랜카드의 **MAC 주소**와 커널 드라이버를 확인해야 합니다. (랜카드 이름은 재부팅 시 수시로 변경될 수 있으므로, 설정 파일에는 반드시 고유한 MAC 주소를 사용하는 것이 꼬임 방지에 필수적입니다.)

**1. 랜카드 이름 및 MAC 주소 찾기**
```bash
ip -br link
```
*(출력 예시: `enp2s0    UP    00:11:22:33:44:55 ...` 에서 3번째 항목인 `00:11:22:33:44:55`가 MAC 주소입니다)*

**2. 커널 드라이버 이름 찾기**
```bash
# 위에서 찾은 랜카드 이름(예: enp2s0)을 대입합니다.
ethtool -i enp2s0
```
*(출력 중 `driver: r8169` 또는 `driver: e1000e`, `driver: igc` 등의 값을 확인합니다)*

EtherLab 소스 빌드에는 다음 도구가 필요합니다.

```bash
sudo apt install -y autoconf automake libtool pkg-config build-essential git
git clone https://gitlab.com/etherlab.org/ethercat.git
```

빌드할 때는 해당 PC에서 확인한 커널 드라이버에 맞춰 모듈을 활성화해야 합니다. (상세 원본 문서는 로컬의 [`EtherCAT 설치 문서`](src/motion_system/lib/motor_manager/communications/ethercat/README.md) 파일을 직접 열어보시거나, [웹 링크](https://github.com/SeonilChoi/motor_manager/blob/main/communications/ethercat/README.md)를 참조하세요.)
**빠른 한글 요약 빌드 과정**은 다음과 같습니다.

```bash
cd ethercat
./bootstrap

# 위에서 확인한 드라이버가 r8169 인 경우 (해당 드라이버만 yes로 변경):
./configure --disable-8139too --enable-generic=no --enable-r8169=yes

# 만약 전용 드라이버 빌드가 실패하거나 호환되지 않을 경우 범용(generic) 모드로 빌드:
# ./configure --disable-8139too --enable-generic=yes

make all modules
sudo make modules_install install
sudo depmod
```

`/etc/ethercat.conf` 설정 시, 장치 이름(enp~ 등) 대신 **위에서 확인한 MAC 주소**를 기입해야 재부팅 시 꼬임 현상을 방지할 수 있습니다.

```text
MASTER0_DEVICE="<랜카드의 MAC 주소 예: 00:11:22:33:44:55>"
DEVICE_MODULES="<generic 또는 해당 EtherCAT 드라이버>"
UPDOWN_INTERFACES="<EtherCAT-NIC-이름>"
```

일반 계정(`ros2` 등)에서 `sudo` 없이 모터에 접근하려면 udev 권한 설정이 필수입니다. 다음 명령어로 권한을 추가합니다.

```bash
echo 'KERNEL=="EtherCAT[0-9]*", MODE="0666"' | sudo tee /etc/udev/rules.d/99-ethercat.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

설치·설정 후 서비스 시작 및 확인:

```bash
sudo systemctl enable --now ethercat
ethercat version
ethercat master
ethercat slaves
```

`ethercat slaves`의 실제 장치 표시는 서보 전원, 배선과 Slave 연결 상태에
따라 달라집니다. 일반 LAN과 EtherCAT에는 절대로 동일한 랜카드를 혼용하지 마십시오.

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

이미 설치된 PC도 설치 때와 같은 명령 하나만 사용합니다.

```bash
cd ~/ros2_ws
bash src/motion_web/install.sh
```

동작:

```text
Git 최신 코드 수신
서브모듈 갱신
필수 프로그램 확인
전체 colcon 빌드
ROS 2 daemon stop/start
자동실행 서비스 등록
motion-control.service 재시작
motion-coordination.service 재시작
```

로컬 수정 파일이 있으면 자동 Git 수신만 건너뛰고 나머지 설치·빌드는 계속
진행합니다. Git 수신을 일부러 막으려면 아래처럼 실행합니다.

```bash
MOTION_WEB_SKIP_GIT_PULL=1 bash src/motion_web/install.sh
```

`GroupCommand` 같은 DDS 메시지 정의가 바뀐 경우에는
통합 설치 스크립트가 메시지 인터페이스 빌드와 `ros2 daemon` 초기화를 함께
수행합니다.

(기존 `scripts/sync_branch.sh` 스크립트를 통한 특정 브랜치 동기화 기능도 여전히 지원됩니다.)

`update.sh`는 호환용 별칭입니다. 내부에서 같은 `install.sh`를 실행합니다.

### 4-3. 커밋·푸시 (개발 PC)

```bash
cd ~/ros2_ws
MOTION_WEB_BRANCH=main bash scripts/commit_branch.sh "커밋 메시지" --push
```

`--push` 없이 커밋만 하려면 마지막 `--push`를 빼면 됩니다. 이 스크립트는
`motion_system` submodule 변경이 있으면 중단합니다.

수동으로 커밋할 때는 [8. Git 작업 방법](#8-git-작업-방법)을 따릅니다.

### 4-4. Codex 자동 설치·업데이트 지시문

다른 PC에서 Codex에게 작업을 맡길 때는 아래 문장을 그대로 전달합니다.

최초 설치:

```text
README의 최초 설치 절차대로 이 PC에 설치해줘.
src/motion_system은 명시 요청 없으면 수정하지 마.
실행 검증과 실물 검증을 구분해서 보고해줘.
```

기존 PC 업데이트:

```text
README의 코드 갱신 절차대로 bash src/motion_web/install.sh를 실행해줘.
Git 수신, 전체 빌드, ros2 daemon 초기화, 서비스 재시작 여부를 확인해줘.
src/motion_system은 명시 요청 없으면 수정하지 마.
실행 검증과 실물 검증을 구분해서 보고해줘.
```

Codex가 수정이나 설치를 수행한 뒤에는 변경 파일, 실행한 명령, 성공·실패
결과와 실물 검증 여부를 분리해서 확인합니다.

## 5. 자동실행 등록

최초 설치는 통합 설치 스크립트가 자동실행 서비스까지 등록합니다.

```bash
cd ~/ros2_ws
bash src/motion_web/install.sh
```

서비스만 다시 등록해야 하는 특수 상황에서는 아래 명령을 사용할 수 있습니다.
일반 사용자는 위 `install.sh` 하나만 사용합니다.

```bash
cd ~/ros2_ws
bash src/motion_web/web_bridge/deploy/install_user_service.sh
```

`실시간 우선순위 권한 설정 필요`가 표시되면 설치 스크립트가
`/etc/security/limits.d/99-motion-control.conf`를 작성합니다. PC를 재부팅한 뒤
같은 명령을 다시 실행합니다.

로그인 전 부팅 단계부터 실행하려면 PC마다 최초 한 번 다음 설정을 추가합니다.

```bash
sudo loginctl enable-linger "$(id -un)"
```

이후에는 재부팅할 때 다음 서비스가 자동으로 실행됩니다.

- `motion-control.service`: 웹·프로젝트·모션 제어 서비스 (`LimitRTPRIO=99`, `LimitMEMLOCK=infinity` 적용 - 하위 모터 재시작 스크립트 및 런타임 RT 권한 보장)
- `motion-motor.service`: 검증된 프로젝트 모터 실행 설정이 있을 때 Motor Manager (`LimitRTPRIO=99`, `LimitMEMLOCK=infinity` 적용)
- `motion-coordination.service`: PC 간 DDS 그룹 상태 공유·실행 조정

### 무인 연동 구동 설정 절차 (부팅 시 자동 재생)

1. **1단계: 수동 연동 방 구성 및 입장 (모든 PC)**
   - 웹 UI `장비 연동 상태` 메뉴 이동
   - `이 PC 연동 설정` ➔ 동일한 `그룹 ID` 및 `DDS Domain ID` 선택
   - `설정 저장·연동 재시작` 버튼 클릭
   - 화면 하단 `그룹 참가` 버튼 클릭 (모든 PC가 연동 방에 수동 접속)

2. **2단계: 현재 접속 명단 자동 확정 및 저장 (마스터 PC 1대)**
   - 마스터 PC 화면 하단 `그룹 참가 PC` 표에 상대 PC들이 나타난 것 확인
   - `현재 표의 인원으로 명단 확정` 버튼 1회 클릭 (수동 ID 입력 없이 현재 방에 접속된 모든 PC ID가 시스템에 자동 저장됨)

3. **3단계: 부팅 자동 재생 예약 활성화 (마스터 PC 1대)**
   - 화면 중단 `자동 재생 (마스터)` 영역 이동
   - `부팅 시 자동 재생 예약` 체크박스 체크 (자동 저장 반영)

4. **4단계: 부팅 테스트 및 완전 자동 구동**
   - PC 전원 재부팅 시 사용자 조작 없이 확정 저장된 명단 자동 감지 ➔ 수동 조작 없는 무인 연동 구동 완료

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

### 6-1. PC 연동 업데이트 문제 해결

`GROUP_START_REJECTED`와 함께 다음 형태의 오류가 나오면 다른 PC의 DDS 메시지
산출물이 구버전일 가능성이 큽니다.

```text
'GroupCommand' object has no attribute 'target_cycle_count'
```

조치 순서:

```bash
cd ~/ros2_ws
git fetch origin
git checkout main
git pull origin main
git submodule update --init --recursive
./src/motion_web/update.sh
systemctl --user status motion-coordination.service motion-control.service
journalctl --user -u motion-coordination.service -n 80
```

확인 기준:

```text
motion_coordination_interfaces 빌드 완료
ros2 daemon stop/start 완료
motion-coordination.service 재시작 완료
motion-control.service 재시작 완료
브라우저 Ctrl+F5 후 DDS 그룹 참가 상태 확인
```

여러 PC 중 한 대만 업데이트해도 메시지 구조가 맞지 않으면 연동이 실패할 수
있습니다. PC 연동에 참가하는 모든 PC에서 같은 Git 커밋과 같은 메시지 빌드
상태를 맞춥니다.

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
