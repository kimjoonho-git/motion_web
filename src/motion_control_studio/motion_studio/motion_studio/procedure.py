"""단계를 줄 세워 돌린다 · §6-82

녹화·미리보기·초기 위치 이동은 모두 "여러 단계를 차례로 밟되, 사용자가 언제든
멈출 수 있고, 중간에 실패하면 되감아야 하는" 절차다. 그런데 넷이 각자 손으로
짜여 있었다 · 91줄짜리 하나를 포함해, 폴링 방식 세 가지와 손으로 적은 세대 검사
열두 곳이 흩어져 있었다.

단계를 하나 끼우려면 그 전부를 건드려야 했고, 빠뜨려도 아무도 안 알려줬다 ·
추가 녹화에 재생을 얹을 때 실제로 그랬다 · §6-76 §6-78

여기서는 절차가 **무엇을 하는지만** 적는다 · 언제 멈추는지, 어떻게 기다리는지,
실패하면 무엇을 되감는지는 이 실행기가 맡는다.

    절차 = StudioProcedure(studio, token)
    절차.unwind(lambda: studio._request_midi('studio_recording_ready', {}, 2.0))
    절차.run([
        ('MIDI 녹화 준비', 준비하기),
        ('페이더 0 복귀', 페이더_기다리기),
        ('초기 위치 이동', 이동하기),
    ])
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Optional, Sequence, Tuple

Step = Tuple[str, Callable[[], Any]]


class ProcedureStopped(Exception):
    """사용자가 멈췄다 · 오류가 아니다 · 조용히 끝낸다."""


class StudioProcedure:
    def __init__(
        self,
        studio: Any,
        token: int,
        *,
        expected_state: str = 'initializing',
    ) -> None:
        self.studio = studio
        self.token = token
        self.expected_state = expected_state
        self._unwind: list = []

    # ----------------------------------------------------------------- #
    # 절차를 돌린다
    # ----------------------------------------------------------------- #

    def unwind(self, action: Callable[[], Any]) -> None:
        """실패하든 성공하든 되감을 일 · 등록한 역순으로 부른다."""
        self._unwind.append(action)

    def cancel_unwind(self, action: Callable[[], Any]) -> None:
        """이제 되감을 필요가 없어졌다 · 예: MIDI 잠금을 정상적으로 풀었다."""
        if action in self._unwind:
            self._unwind.remove(action)

    def run(self, steps: Sequence[Step]) -> bool:
        """단계를 차례로 밟는다 · 끝까지 갔으면 참.

        단계마다 **먼저** 이 작업이 아직 살아 있는지 본다 · 사용자가 멈췄으면
        그 자리에서 조용히 끝낸다. 실패하면 테이크를 오류로 닫고 거짓을 준다 ·
        어느 단계에서 실패했는지 이름이 메시지에 남는다.
        """
        try:
            for name, action in steps:
                self.guard()
                action()
            return True
        except ProcedureStopped:
            return False
        except RuntimeError:
            # `require_active` 가 내는 정지 · 오류가 아니다
            return False
        except Exception as exc:
            self._fail(f'{name}: {exc}' if str(exc) else name)
            return False
        finally:
            for action in reversed(self._unwind):
                try:
                    action()
                except Exception:
                    pass
            self._unwind.clear()

    def guard(self) -> None:
        """이 작업이 아직 사용자가 시킨 그 작업인가."""
        self.studio._require_active_operation(self.token, self.expected_state)

    # ----------------------------------------------------------------- #
    # 기다림 · 한 가지 방식만 쓴다
    # ----------------------------------------------------------------- #

    def wait_until(
        self,
        predicate: Callable[[], bool],
        *,
        timeout: float,
        timeout_message: str,
        failure: Optional[Callable[[], Optional[str]]] = None,
        interval: float = 0.02,
    ) -> None:
        """조건이 참이 될 때까지 기다린다.

        `failure` 가 문구를 내면 그 자리에서 실패한다 · 실행 노드가 오류를
        보고했는데 제한 시간까지 기다리고 있으면 사용자는 영문을 모른다.
        """
        deadline = time.monotonic() + max(0.05, float(timeout))
        while time.monotonic() < deadline:
            if not self.alive():
                raise ProcedureStopped()
            if failure is not None:
                message = failure()
                if message:
                    raise ValueError(message)
            if predicate():
                return
            time.sleep(interval)
        raise ValueError(f'{timeout_message} · 제한 시간 초과')

    def alive(self) -> bool:
        with self.studio._lock:
            return self.token == self.studio._operation_generation

    def run_status(self) -> dict:
        with self.studio._lock:
            return dict(self.studio._motion_run_status)

    def wait_for_run_state(
        self,
        states: Iterable[str],
        *,
        timeout: float,
        timeout_message: str,
    ) -> None:
        """실행 노드가 이 상태들 중 하나가 될 때까지 · 오류면 즉시 실패."""
        wanted = set(states)

        def failed() -> Optional[str]:
            status = self.run_status()
            if status.get('state') == 'error':
                return str(status.get('message') or timeout_message)
            return None

        self.wait_until(
            lambda: self.run_status().get('state') in wanted,
            timeout=timeout,
            timeout_message=timeout_message,
            failure=failed,
        )

    # ----------------------------------------------------------------- #
    # 내부
    # ----------------------------------------------------------------- #

    def _fail(self, message: str) -> None:
        with self.studio._lock:
            if self.token == self.studio._operation_generation:
                self.studio._takes().fail(message)
