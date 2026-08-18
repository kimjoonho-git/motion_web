# 터미널 없이 실행하기

최초 설치·빌드가 끝난 뒤 사용자 자동실행 서비스를 한 번 등록합니다.

```bash
bash src/motion_web/web_bridge/deploy/install_user_service.sh
```

처음 실행 시 `실시간 우선순위 권한 설정 필요`가 표시되면 정상입니다. 설치 스크립트가
`/etc/security/limits.d/99-motion-control.conf`를 작성하므로 PC를 재부팅한 뒤
같은 명령을 다시 실행합니다.

등록 후에는 컴퓨터 로그인 시 웹과 프로젝트 서비스가 자동으로 시작되고,
프로세스가 비정상 종료돼도 자동으로 복구됩니다. 이후 프로젝트 생성·변경,
설정 저장과 실제 시스템 적용은 웹 UI에서 수행합니다. 웹의 `시스템 정보`에서
`프로그램 실행`이 `자동 실행 · 자동 복구`로 표시되는지 확인할 수 있습니다.

`motion-coordination.service`는 선택한 DDS Domain에서 PC 간 typed 그룹 메시지만
송수신합니다. Web Bridge와는 `127.0.0.1:8011`로만 연결하며 외부 `8010` 포트는
사용하지 않습니다. 그룹 ID와 DDS Domain ID는 각 PC 웹의 `DDS 그룹 연동`에서
저장합니다.

로그인 전 부팅 단계부터 실행해야 하는 장비는 최초 설치 중 한 번만 아래 설정을
추가합니다.

```bash
sudo loginctl enable-linger $(id -un)
```
