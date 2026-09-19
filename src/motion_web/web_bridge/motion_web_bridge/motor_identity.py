"""Transport-neutral physical motor identity helpers.

Discovery data identifies a bus device.  It must not silently become a
nameplate model or a motion profile: those are separate user/project facts.
"""

from typing import Any, Dict, Optional

from motion_common import values


PHYSICAL_IDENTITY_FIELDS = (
    'vendor_id',
    'product_code',
    'revision_number',
    'serial_number',
)

PHYSICAL_SII_IDENTITY_SOURCES = frozenset({
    'physical_sii',
    # Compatibility with project files saved by the web confirmation flow.
    # The suffix records the user's axis association confirmation; the
    # identity values themselves still came from the physical SII scan.
    'physical_sii_user_confirmed',
})


def optional_int(value: Any) -> Optional[int]:
    """Identity fields are always optional: absent stays absent, never 0."""
    return values.optional_int(value, None)


def missing_ethercat_identity(identity: Dict[str, Any]) -> list[str]:
    """List mandatory direct identity fields absent from an EtherCAT axis."""
    missing = [
        field
        for field in PHYSICAL_IDENTITY_FIELDS
        if optional_int(identity.get(field)) is None
    ]
    if str(identity.get('identity_source') or '') not in PHYSICAL_SII_IDENTITY_SOURCES:
        missing.append('identity_source')
    return missing


#: **「모델을 모른다」는 표식** · 값이 아니다 · §6-210
#:
#: 기본 minas 드라이버가 이 글자를 달고 태어난다 · 서버 검증은 이 글자를 보고
#: 적용을 막는다 · 그런데 화면·레지스트리는 이것을 **모델 이름으로** 받아
#: 「UNVERIFIED_MINAS」를 진짜 값처럼 들고 다녔다 · 그래서 드라이버는
#: `MADLN05BE` 인데 프로젝트 파일은 「모름」이라고 적힌 채로 갈렸다.
UNKNOWN_DRIVER_MODEL = 'UNVERIFIED_MINAS'


def model_is_unknown(value: Any) -> bool:
    """빈 값과 표식을 **한 가지로** 본다 · 읽는 쪽마다 다시 적지 않는다."""
    return str(value or '').strip().upper() in ('', UNKNOWN_DRIVER_MODEL)


def driver_model_from(
    profile: Optional[Dict[str, Any]],
    identity: Optional[Dict[str, Any]],
) -> str:
    """축 하나의 드라이버 모델 · **모르면 빈 글자**를 준다.

    사람이 명판을 보고 다시 입력하던 단계를 걷어냈으므로(§6-205) 모델을
    모를 때는 검색이 SII EEPROM 에서 읽어 온 값을 쓴다 · 화면에 「SII
    참고값」으로 보이던 그 값이다.
    """
    model = str((profile or {}).get('driver_model') or '').strip()
    if not model_is_unknown(model):
        return model
    identity = identity or {}
    return str(
        identity.get('sii_order_number')
        or identity.get('sii_device_name')
        or ''
    ).strip()
