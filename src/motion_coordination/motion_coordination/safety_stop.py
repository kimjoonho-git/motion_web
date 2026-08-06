"""Common local-first safety stop sequencing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional


@dataclass(frozen=True)
class SafetyStopOutcome:
    local_result: Dict[str, Any]
    dds_stop_published: bool

    @property
    def local_success(self) -> bool:
        return bool(self.local_result.get('success'))


class SafetyStopController:
    """Attempt the local stop first and always propagate the DDS stop next."""

    def stop_now(
        self,
        local_stop: Callable[[], Mapping[str, Any]],
        publish_dds_stop: Optional[Callable[[], None]] = None,
    ) -> SafetyStopOutcome:
        try:
            local = dict(local_stop())
        except Exception as exc:  # local safety boundary
            local = {'success': False, 'message': str(exc)}
        published = False
        if publish_dds_stop is not None:
            try:
                publish_dds_stop()
                published = True
            except Exception as exc:  # transport safety boundary
                local.setdefault('dds_message', str(exc))
        return SafetyStopOutcome(local, published)
