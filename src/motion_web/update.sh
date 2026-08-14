#!/bin/bash
# 테스트 단계용 원클릭 자동 업데이트 스크립트 (모든 PC 호환)

# 스크립트가 위치한 폴더(src/motion_web)와 최상위 워크스페이스 폴더 자동 계산
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "========================================="
echo "1. Git 최신 코드 가져오는 중... ($SCRIPT_DIR)"
echo "========================================="
cd "$SCRIPT_DIR" || exit
git pull origin main

echo ""
echo "========================================="
echo "2. ROS 2 패키지 자동 빌드 중... ($WORKSPACE_DIR)"
echo "========================================="
cd "$WORKSPACE_DIR" || exit
colcon build --packages-select motion_web_ui motion_web_bridge motion_coordination motion_runtime

echo ""
echo "========================================="
echo "3. 서비스 재시작 및 적용 중..."
echo "========================================="
systemctl --user daemon-reload
systemctl --user restart motion-control.service

echo ""
echo "========================================="
echo "모든 업데이트가 완료되었습니다!"
echo "웹 브라우저에서 강력 새로고침(Ctrl+F5)을 해주세요."
echo "========================================="
