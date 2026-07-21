import pytest
from midi_control.bank_manager import MidiBankManager


def test_default_bank_is_memory_only():
    manager = MidiBankManager()
    snapshot = manager.snapshot()

    assert snapshot['storage'] == 'memory'
    assert snapshot['persistent'] is False
    assert snapshot['active_bank_id'] == 'bank_1'
    assert len(snapshot['banks']) == 1
    assert len(snapshot['active_bank']['mappings']) == 8


def test_new_bank_copies_active_settings_and_can_be_selected():
    manager = MidiBankManager()
    mappings = manager.active_bank()['mappings']
    mappings[0]['motion_id'] = '4-3'
    manager.update_bank('bank_1', mappings=mappings)

    created = manager.create_bank('Show B')
    manager.select_bank(created['bank_id'])

    assert manager.active_bank()['name'] == 'Show B'
    assert manager.active_bank()['mappings'][0]['motion_id'] == '4-3'


def test_bank_update_does_not_store_live_midi_values():
    manager = MidiBankManager()
    mappings = manager.active_bank()['mappings']
    mappings[0]['raw_value'] = 1234
    mappings[0]['touch'] = True
    manager.update_bank('bank_1', mappings=mappings)

    saved = manager.active_bank()['mappings'][0]
    assert 'raw_value' not in saved
    assert 'touch' not in saved


def test_bank_state_round_trip_restores_all_banks():
    source = MidiBankManager()
    mappings = source.active_bank()['mappings']
    mappings[0]['motion_id'] = '4-3'
    mappings[0]['min_percent'] = 25
    mappings[0]['max_percent'] = 75
    source.update_bank('bank_1', name='Main', mappings=mappings)
    second = source.create_bank('Second')
    source.select_bank(second['bank_id'])

    restored = MidiBankManager()
    restored.replace_state(source.export_state())

    assert restored.export_state() == source.export_state()


def test_last_bank_cannot_be_deleted():
    manager = MidiBankManager()
    with pytest.raises(ValueError, match='last bank'):
        manager.delete_bank('bank_1')


def test_deleting_active_bank_selects_remaining_bank():
    manager = MidiBankManager()
    second = manager.create_bank('Bank 2')
    manager.select_bank(second['bank_id'])
    manager.delete_bank(second['bank_id'])

    assert manager.snapshot()['active_bank_id'] == 'bank_1'


def test_bank_count_is_limited_to_eight():
    manager = MidiBankManager()
    for number in range(2, 9):
        manager.create_bank(f'Bank {number}')

    assert len(manager.snapshot()['banks']) == 8
    assert manager.snapshot()['max_banks'] == 8
    with pytest.raises(ValueError, match='no more than 8 banks'):
        manager.create_bank('Bank 9')


@pytest.mark.parametrize('filter_level', range(14))
def test_filter_level_accepts_all_fourteen_integer_steps(filter_level):
    manager = MidiBankManager()
    mappings = manager.active_bank()['mappings']
    mappings[0]['filter_level'] = filter_level

    updated = manager.update_bank('bank_1', mappings=mappings)

    assert updated['mappings'][0]['filter_level'] == filter_level


@pytest.mark.parametrize('motion_id', ['1-1', '4-3', '12-25'])
def test_motion_id_accepts_positive_number_pair(motion_id):
    manager = MidiBankManager()
    mappings = manager.active_bank()['mappings']
    mappings[0]['motion_id'] = motion_id

    updated = manager.update_bank('bank_1', mappings=mappings)

    assert updated['mappings'][0]['motion_id'] == motion_id


@pytest.mark.parametrize('motion_id', ['axis1', '1', '1-A', '0-1', '1-0', '-1-1', '01-1'])
def test_motion_id_rejects_values_outside_positive_number_pair(motion_id):
    manager = MidiBankManager()
    mappings = manager.active_bank()['mappings']
    mappings[0]['motion_id'] = motion_id

    with pytest.raises(ValueError, match='motion_id must use'):
        manager.update_bank('bank_1', mappings=mappings)


def test_one_midi_channel_accepts_up_to_three_unique_motion_ids():
    manager = MidiBankManager()
    mappings = manager.active_bank()['mappings']
    mappings[0]['motion_id'] = '1-1'
    mappings[0]['linked_motion_ids'] = ['1-2', '3-1']

    updated = manager.update_bank('bank_1', mappings=mappings)

    assert updated['mappings'][0]['linked_motion_ids'] == ['1-2', '3-1']
    assert manager.export_state()['banks'][0]['mappings'][0]['linked_motion_ids'] == [
        '1-2', '3-1'
    ]


def test_linked_motion_ids_reject_duplicates_and_more_than_three():
    manager = MidiBankManager()
    mappings = manager.active_bank()['mappings']
    mappings[0]['linked_motion_ids'] = ['1-1']
    with pytest.raises(ValueError, match='must not be duplicated'):
        manager.update_bank('bank_1', mappings=mappings)

    mappings[0]['linked_motion_ids'] = ['1-2', '1-3', '1-4']
    with pytest.raises(ValueError, match='no more than 3'):
        manager.update_bank('bank_1', mappings=mappings)
