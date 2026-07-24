# Motion Web

ROS 2 기반 모션 제어 웹 브리지와 웹 UI 패키지입니다.

이 저장소는 별도 `motion_control_studio`와 `motion_system` 저장소 위에서
동작하며, 웹 API, 웹 UI, 공통 설정과 실행 스크립트를 관리합니다.

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
src/motion_web
├── web_bridge
└── web_ui

config
scripts
docs
```

## 의존 관계

이 저장소는 아래 두 저장소의 제어 계층 위에서 동작합니다.

```text
src/motion_control_studio
src/motion_system
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

처음 실행용 빈 모터 설정은 아래 파일을 사용합니다.

```text
config/bootstrap_motor_config.yaml
```

사용자가 생성한 모터축 설정·모션축 설정·모션·레이어는 모두
`motion_projects/<project_id>/` 아래에 프로젝트별로 분리됩니다. 실제 모터에
적용하는 설정은 프로젝트의 `runtime/applied_motor_config.yaml`에 생성됩니다.

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
motion_projects/
motion_data/
```

프로젝트는 Git 소스와 분리하고, 필요할 때 USB·파일 가져오기·다른
프로젝트에서 복사 기능으로 이동합니다. `motion_data/`는 구형 데이터 보관용이며
현재 실행 기능은 이 폴더를 읽지 않습니다.

## 주의 사항

이 프로젝트는 실제 모터를 제어합니다.

실제 모터 구동 전에는 반드시 기구물 안전 상태, 서보 상태, 위치 제한값,
초기 위치 설정, 모션축 매핑을 확인해야 합니다.

특히 노드 재시작, Servo ON / OFF, Fault Reset, 초기 위치 이동, 모션 실행은
기구물 하중과 작업자 안전을 확인한 뒤 수행해야 합니다.
