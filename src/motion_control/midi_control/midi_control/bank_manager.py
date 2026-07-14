import math
import re
from typing import Any, Dict, List


MIDI_CHANNEL_COUNT = 8
MAX_MIDI_BANKS = 8
FILTER_LEVEL_MIN = 0
FILTER_LEVEL_MAX = 13
MOTION_ID_PATTERN = re.compile(r'^[1-9]\d*-[1-9]\d*$')


class MidiBankManager:
    """Manage MIDI bank settings in memory without file persistence."""

    def __init__(self) -> None:
        self._banks: Dict[str, Dict[str, Any]] = {}
        self._next_bank_number = 1
        first = self.create_bank('Bank 1', copy_from_active=False)
        self._active_bank_id = first['bank_id']

    @staticmethod
    def default_mappings() -> List[Dict[str, Any]]:
        return [
            {
                'channel': channel,
                'enabled': True,
                'motion_id': f'1-{channel + 1}',
                'min_deg': -180.0,
                'max_deg': 180.0,
                'reversed': False,
                'filter_level': 0,
            }
            for channel in range(MIDI_CHANNEL_COUNT)
        ]

    @staticmethod
    def _finite_float(value: Any, field: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{field} must be a number') from exc
        if not math.isfinite(result):
            raise ValueError(f'{field} must be finite')
        return result

    @staticmethod
    def _validated_name(value: Any) -> str:
        name = str(value or '').strip()
        if not name:
            raise ValueError('bank name is required')
        if len(name) > 40:
            raise ValueError('bank name must be 40 characters or fewer')
        return name

    @classmethod
    def validate_mappings(cls, mappings: Any) -> List[Dict[str, Any]]:
        if not isinstance(mappings, list):
            raise ValueError('mappings must be an array')
        by_channel: Dict[int, Dict[str, Any]] = {}
        for index, item in enumerate(mappings):
            if not isinstance(item, dict):
                raise ValueError(f'mappings[{index}] must be an object')
            try:
                channel = int(item.get('channel', index))
            except (TypeError, ValueError) as exc:
                raise ValueError(f'mappings[{index}].channel must be an integer') from exc
            if channel < 0 or channel >= MIDI_CHANNEL_COUNT:
                raise ValueError(f'mappings[{index}].channel must be 0..7')
            min_deg = cls._finite_float(item.get('min_deg', -180.0), 'min_deg')
            max_deg = cls._finite_float(item.get('max_deg', 180.0), 'max_deg')
            if abs(max_deg - min_deg) < 1e-9:
                raise ValueError(f'channel {channel + 1}: min_deg and max_deg must differ')
            filter_value = cls._finite_float(item.get('filter_level', 0), 'filter_level')
            filter_level = int(filter_value)
            if (
                filter_value != filter_level
                or filter_level < FILTER_LEVEL_MIN
                or filter_level > FILTER_LEVEL_MAX
            ):
                raise ValueError(f'channel {channel + 1}: filter_level must be an integer 0..13')
            motion_id = str(item.get('motion_id') or '').strip()
            if not MOTION_ID_PATTERN.fullmatch(motion_id):
                raise ValueError(
                    f'channel {channel + 1}: motion_id must use positive-number-positive-number format'
                )
            by_channel[channel] = {
                'channel': channel,
                'enabled': bool(item.get('enabled', True)),
                'motion_id': motion_id,
                'min_deg': min_deg,
                'max_deg': max_deg,
                'reversed': bool(item.get('reversed', False)),
                'filter_level': filter_level,
            }
        defaults = cls.default_mappings()
        return [dict(by_channel.get(channel, defaults[channel])) for channel in range(MIDI_CHANNEL_COUNT)]

    @staticmethod
    def _copy_bank(bank: Dict[str, Any], *, include_mappings: bool = True) -> Dict[str, Any]:
        result = {
            'bank_id': bank['bank_id'],
            'name': bank['name'],
        }
        if include_mappings:
            result['mappings'] = [dict(item) for item in bank['mappings']]
        return result

    def _bank(self, bank_id: Any) -> Dict[str, Any]:
        key = str(bank_id or '').strip()
        bank = self._banks.get(key)
        if bank is None:
            raise ValueError(f'unknown bank: {key}')
        return bank

    def active_bank(self) -> Dict[str, Any]:
        return self._copy_bank(self._bank(self._active_bank_id))

    def snapshot(self) -> Dict[str, Any]:
        active = self.active_bank()
        return {
            'storage': 'memory',
            'persistent': False,
            'max_banks': MAX_MIDI_BANKS,
            'active_bank_id': self._active_bank_id,
            'active_bank': active,
            'banks': [
                self._copy_bank(bank, include_mappings=False)
                for bank in self._banks.values()
            ],
        }

    def create_bank(self, name: Any = None, *, copy_from_active: bool = True) -> Dict[str, Any]:
        if len(self._banks) >= MAX_MIDI_BANKS:
            raise ValueError(f'no more than {MAX_MIDI_BANKS} banks are allowed')
        bank_number = self._next_bank_number
        self._next_bank_number += 1
        bank_id = f'bank_{bank_number}'
        bank_name = self._validated_name(name or f'Bank {bank_number}')
        if copy_from_active and self._banks:
            mappings = self.active_bank()['mappings']
        else:
            mappings = self.default_mappings()
        bank = {
            'bank_id': bank_id,
            'name': bank_name,
            'mappings': mappings,
        }
        self._banks[bank_id] = bank
        if not hasattr(self, '_active_bank_id'):
            self._active_bank_id = bank_id
        return self._copy_bank(bank)

    def select_bank(self, bank_id: Any) -> Dict[str, Any]:
        bank = self._bank(bank_id)
        self._active_bank_id = bank['bank_id']
        return self._copy_bank(bank)

    def update_bank(
        self,
        bank_id: Any,
        *,
        name: Any = None,
        mappings: Any = None,
    ) -> Dict[str, Any]:
        bank = self._bank(bank_id)
        if name is not None:
            bank['name'] = self._validated_name(name)
        if mappings is not None:
            bank['mappings'] = self.validate_mappings(mappings)
        return self._copy_bank(bank)

    def delete_bank(self, bank_id: Any) -> Dict[str, Any]:
        bank = self._bank(bank_id)
        if len(self._banks) <= 1:
            raise ValueError('the last bank cannot be deleted')
        del self._banks[bank['bank_id']]
        if self._active_bank_id == bank['bank_id']:
            self._active_bank_id = next(iter(self._banks))
        return self.snapshot()
