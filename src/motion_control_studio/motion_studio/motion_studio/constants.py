"""Shared Motion Studio timing constants."""
from motion_common.timing import CONTROL_PERIOD_SEC

#: 공용 커널이 단일 정의 · 기존 이름은 호환을 위해 남긴다
DEFAULT_PERIOD_SEC = CONTROL_PERIOD_SEC
DEFAULT_PERIOD_MS = int(DEFAULT_PERIOD_SEC * 1000)
