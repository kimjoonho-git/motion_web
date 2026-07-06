# Motion Control Web

ROS2 기반 상위 모션 제어 및 웹 UI 패키지입니다.

이 저장소는 `motion_system_ros2`의 motor manager / driver 계층 위에서 동작하며,
모터 상태 모니터링, 모터축 설정, 모션파일 관리, 모션축 매핑, 초기 위치 이동,
모션 실행 기능을 제공합니다.

## 주요 기능

- 모터 상태 모니터링
- AC Servo / Dynamixel 상태 표시
- Servo ON / OFF, Fault Reset
- 조그 모드 및 동작 모드 테스트
- 모터축 설정 파일 관리
- 모션파일 업로드, 저장, 검사, 그래프 표시
- 모션축과 모터축 매핑
- 반전, 스케일, 오프셋, 감속비 설정
- 기준점 및 초기 위치 설정
- 초기 위치 S-curve 이동
- 20ms 기준 모션 데이터 실행
- 모션 진행 상태 및 그래프 표시
- 모터 에러 발생 시 웹 팝업 알림

## 패키지 구성

```text
src/motion_control
├── motion_state_monitor
└── motion_supervisor

src/motion_web
├── web_bridge
└── web_ui

config
scripts
docs
```

## 의존 관계

이 저장소는 아래 저장소의 모터 시스템 위에서 동작합니다.

```text
motion_system_ros2
```

계층 구조:

```text
Web UI
  ↓
motion_web_bridge
  ↓
motion_supervisor / motion_state_monitor
  ↓
motion_system_ros2 motor_manager_node
  ↓
Motor drivers / EtherCAT / Dynamixel
```

## 설정 파일

기본 모터 설정 파일은 아래 경로를 사용합니다.

```text
config/active_motor_config.yaml
```

Dynamixel 모델별 참고 설정은 아래 파일에 둡니다.

```text
config/dynamixel_xm540_w150.yaml
config/dynamixel_xm540_w270.yaml
```

## 실행 스크립트

웹 UI에서 모터축 설정을 적용할 때 아래 스크립트를 통해 관련 노드를 재시작합니다.

```text
scripts/restart_motion_monitor.sh
```

## 제외 대상

아래 파일과 폴더는 저장소에 포함하지 않습니다.

```text
build/
install/
log/
backups/
*.bak-*
motion_data/files/
motion_data/mappings/
```

운영 중 생성되는 모션 데이터와 매핑 파일은 필요 시 별도 백업하거나,
예제 파일만 별도 디렉터리에서 관리합니다.

## 주의 사항

이 프로젝트는 실제 모터를 제어합니다.

실제 모터 구동 전에는 반드시 기구물 안전 상태, 서보 상태, 위치 제한값,
초기 위치 설정, 모션축 매핑을 확인해야 합니다.

특히 노드 재시작, Servo ON / OFF, Fault Reset, 초기 위치 이동, 모션 실행은
기구물 하중과 작업자 안전을 확인한 뒤 수행해야 합니다.
