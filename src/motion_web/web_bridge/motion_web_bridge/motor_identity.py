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
