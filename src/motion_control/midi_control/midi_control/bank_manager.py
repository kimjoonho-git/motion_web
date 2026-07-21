import math
import re
from typing import Any, Dict, List


MIDI_CHANNEL_COUNT = 8
MAX_MIDI_BANKS = 8
MAX_LINKED_MOTION_IDS = 3
MIDI_VALUE_MIN = 0
MIDI_VALUE_MAX = 16383
OUTPUT_PERCENT_MIN = 0.0
OUTPUT_PERCENT_NORMAL_MAX = 100.0
OUTPUT_PERCENT_MAX = 200.0
FILTER_LEVEL_MIN = 0
FILTER_LEVEL_MAX = 13
MOTION_ID_PATTERN = re.compile(r'^[1-9]\d*-[1-9]\d*$')


def mapping_motion_ids(mapping: Dict[str, Any]) -> List[str]:
    """Return the primary and linked Motion IDs in stable order."""
    values = [mapping.get('motion_id')]
    linked = mapping.get('linked_motion_ids')
    if isinstance(linked, list):
        values.extend(linked)
    result = []
    for value in values:
        motion_id = str(value or '').strip()
        if motion_id and motion_id not in result:
            result.append(motion_id)
    return result[:MAX_LINKED_MOTION_IDS]


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
                'linked_motion_ids': [],
                'min_percent': OUTPUT_PERCENT_MIN,
                'max_percent': OUTPUT_PERCENT_NORMAL_MAX,
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
            min_percent = cls._finite_float(
                item.get('min_percent', OUTPUT_PERCENT_MIN),
                'min_percent',
            )
            max_percent = cls._finite_float(
                item.get('max_percent', OUTPUT_PERCENT_NORMAL_MAX),
                'max_percent',
            )
            if not OUTPUT_PERCENT_MIN < max_percent <= OUTPUT_PERCENT_MAX:
                raise ValueError(f'channel {channel + 1}: max_percent must be > 0 and <= 200')
            if max_percent > OUTPUT_PERCENT_NORMAL_MAX:
                min_percent = OUTPUT_PERCENT_MIN
            elif not OUTPUT_PERCENT_MIN <= min_percent < max_percent:
                raise ValueError(
                    f'channel {channel + 1}: min_percent must be >= 0 and less than max_percent'
                )
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
                    f'channel {channel + 1}: motion_id must use '
                    'positive-number-positive-number format'
                )
            linked_value = item.get('linked_motion_ids', [])
            if linked_value is None:
                linked_value = []
            if not isinstance(linked_value, list):
                raise ValueError(
                    f'channel {channel + 1}: linked_motion_ids must be an array'
                )
            linked_motion_ids = [str(value or '').strip() for value in linked_value]
            if len(linked_motion_ids) > MAX_LINKED_MOTION_IDS - 1:
                raise ValueError(
                    f'channel {channel + 1}: no more than '
                    f'{MAX_LINKED_MOTION_IDS} motion IDs may be linked'
                )
            for linked_motion_id in linked_motion_ids:
                if not MOTION_ID_PATTERN.fullmatch(linked_motion_id):
                    raise ValueError(
                        f'channel {channel + 1}: linked motion IDs must use '
                        'positive-number-positive-number format'
                    )
            all_motion_ids = [motion_id, *linked_motion_ids]
            if len(set(all_motion_ids)) != len(all_motion_ids):
                raise ValueError(
                    f'channel {channel + 1}: linked motion IDs must not be duplicated'
                )
            by_channel[channel] = {
                'channel': channel,
                'enabled': bool(item.get('enabled', True)),
                'motion_id': motion_id,
                'linked_motion_ids': linked_motion_ids,
                'min_percent': min_percent,
                'max_percent': max_percent,
                'reversed': bool(item.get('reversed', False)),
                'filter_level': filter_level,
            }
        defaults = cls.default_mappings()
        return [
            dict(by_channel.get(channel, defaults[channel]))
            for channel in range(MIDI_CHANNEL_COUNT)
        ]

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

    def export_state(self) -> Dict[str, Any]:
        """Return settings only; live MIDI values are never included."""
        return {
            'version': 1,
            'active_bank_id': self._active_bank_id,
            'banks': [self._copy_bank(bank) for bank in self._banks.values()],
        }

    def replace_state(self, state: Any) -> Dict[str, Any]:
        if not isinstance(state, dict):
            raise ValueError('midi_banks must be an object')
        banks = state.get('banks')
        if not isinstance(banks, list) or not banks:
            raise ValueError('midi_banks.banks must contain at least one bank')
        if len(banks) > MAX_MIDI_BANKS:
            raise ValueError(f'no more than {MAX_MIDI_BANKS} banks are allowed')

        validated: Dict[str, Dict[str, Any]] = {}
        max_bank_number = 0
        for index, item in enumerate(banks):
            if not isinstance(item, dict):
                raise ValueError(f'midi_banks.banks[{index}] must be an object')
            bank_id = str(item.get('bank_id') or '').strip()
            if not bank_id or len(bank_id) > 64:
                raise ValueError(f'midi_banks.banks[{index}].bank_id is invalid')
            if bank_id in validated:
                raise ValueError(f'duplicated MIDI bank id: {bank_id}')
            match = re.fullmatch(r'bank_(\d+)', bank_id)
            if match:
                max_bank_number = max(max_bank_number, int(match.group(1)))
            validated[bank_id] = {
                'bank_id': bank_id,
                'name': self._validated_name(item.get('name')),
                'mappings': self.validate_mappings(item.get('mappings')),
            }

        active_bank_id = str(state.get('active_bank_id') or '').strip()
        if active_bank_id not in validated:
            raise ValueError(f'unknown active MIDI bank: {active_bank_id}')
        self._banks = validated
        self._active_bank_id = active_bank_id
        self._next_bank_number = max(max_bank_number + 1, len(validated) + 1)
        return self.snapshot()

    def create_bank(self, name: Any = None, *, copy_from_active: bool = True) -> Dict[str, Any]:
        if len(self._banks) >= MAX_MIDI_BANKS:
            raise ValueError(f'no more than {MAX_MIDI_BANKS} banks are allowed')
        bank_number = self._next_bank_number
        self._next_bank_number += 1
        bank_id = f'bank_{bank_number}'
        while bank_id in self._banks:
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
