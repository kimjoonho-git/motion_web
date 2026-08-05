# Motion Control Studio

ROS 2 기반 상위 모션 제어와 모션 편집 패키지 모음입니다.

## 패키지

- `motion_control/midi_control`
- `motion_control/midi_input_bridge`
- `motion_control/motion_runtime`
- `motion_control/motion_state_monitor`
- `motion_control/motion_supervisor`
- `motion_studio`

이 저장소는 별도 `motion_system` 저장소의 모터 제어 인터페이스 위에서
동작하며, 웹 계층은 별도 `motion_web` 저장소에서 관리합니다.

PC 간 상태 공유·실행 조정은 작업공간의 독립 패키지
`src/motion_coordination`에서 담당합니다.
