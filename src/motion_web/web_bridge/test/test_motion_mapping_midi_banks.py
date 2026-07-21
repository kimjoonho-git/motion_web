from motion_web_bridge.bridge_node import MotionWebBridge


MIDI_STATE = {
    'version': 1,
    'active_bank_id': 'bank_1',
    'banks': [{'bank_id': 'bank_1', 'name': 'Bank 1', 'mappings': []}],
}


class StartupTimer:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class SelectedProjectRepository:
    @staticmethod
    def selected_project_id():
        return 'project-1'

    @staticmethod
    def get_project(_project_id):
        return {
            'project': {
                'active_files': {'motion_axis_matching': 'mapping.yaml'}
            }
        }


def test_startup_project_context_delegates_to_central_reconciler():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    calls = []
    bridge._reconcile_execution_context = lambda: calls.append(True)

    bridge._initialize_selected_project_context()

    assert calls == [True]


def test_loading_motion_mapping_reads_banks_from_mapping_owner_and_applies_node(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    mapping_calls = []
    midi_calls = []

    def mapping_request(command, payload, timeout_sec=2.0):
        mapping_calls.append((command, payload, timeout_sec))
        if command == 'load':
            return {'success': True, 'file': {'id': 'show.yaml'}, 'mapping': {}}
        return {
            'success': True,
            'file': {'id': 'show.yaml'},
            'midi_banks': MIDI_STATE,
        }

    monkeypatch.setattr(bridge, '_request_motion_mapping', mapping_request)
    monkeypatch.setattr(
        bridge,
        '_request_midi_monitor',
        lambda command, payload, timeout_sec: (
            midi_calls.append((command, payload, timeout_sec))
            or {'success': True}
        ),
    )

    result = bridge.load_motion_mapping('show.yaml')

    assert result['midi_banks']['success'] is True
    assert mapping_calls == [
        ('load', {'file_id': 'show.yaml'}, 2.0),
        ('load_midi_banks', {'file_id': 'show.yaml'}, 3.0),
    ]
    assert midi_calls == [
        ('apply_banks', {'mapping_file_id': 'show.yaml', 'midi_banks': MIDI_STATE}, 3.0)
    ]


def test_saving_motion_mapping_preserves_file_banks_then_applies_verified_state(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    mapping_calls = []
    midi_calls = []

    def mapping_request(command, payload, timeout_sec=2.0):
        mapping_calls.append((command, payload, timeout_sec))
        if command == 'save':
            return {'success': True, 'file': {'id': 'show.yaml'}, 'mapping': {}}
        return {
            'success': True,
            'file': {'id': 'show.yaml'},
            'midi_banks': MIDI_STATE,
        }

    monkeypatch.setattr(bridge, '_request_motion_mapping', mapping_request)
    monkeypatch.setattr(
        bridge,
        '_request_midi_monitor',
        lambda command, payload, timeout_sec: (
            midi_calls.append((command, payload, timeout_sec))
            or {'success': True}
        ),
    )

    result = bridge.save_motion_mapping({'file_id': 'show.yaml', 'mapping': {}})

    assert result['midi_banks']['success'] is True
    assert mapping_calls[0][0] == 'save'
    assert mapping_calls[1] == ('load_midi_banks', {'file_id': 'show.yaml'}, 3.0)
    assert midi_calls[0][0] == 'apply_banks'


def test_first_mapping_save_succeeds_before_first_midi_bank_save(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)

    def mapping_request(command, payload, timeout_sec=2.0):
        if command == 'save':
            return {'success': True, 'file': {'id': 'new.yaml'}, 'mapping': {}}
        return {
            'success': False,
            'missing': True,
            'message': '아직 저장된 MIDI 뱅크가 없습니다',
        }

    monkeypatch.setattr(bridge, '_request_motion_mapping', mapping_request)
    monkeypatch.setattr(bridge, 'project_repository', None, raising=False)

    result = bridge.save_motion_mapping({'mapping': {'name': 'new'}})

    assert result['success'] is True
    assert result['midi_banks']['missing'] is True
    assert '처음 저장' in result['message']


def test_updating_bank_saves_through_mapping_owner_then_applies_verified_state(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_status = {'state': 'running'}
    bridge._motion_studio_status = {'state': 'recording'}
    mapping_calls = []
    midi_calls = []

    def midi_request(command, payload, timeout_sec):
        midi_calls.append((command, payload, timeout_sec))
        if command == 'update_bank':
            return {
                'success': True,
                'motion_mapping_file_id': 'show.yaml',
                'bank_state': MIDI_STATE,
            }
        return {'success': True, 'bank_state': MIDI_STATE}

    def mapping_request(command, payload, timeout_sec=2.0):
        mapping_calls.append((command, payload, timeout_sec))
        return {
            'success': True,
            'file': {'id': 'show.yaml'},
            'midi_banks': MIDI_STATE,
            'backup_file': 'show.yaml.bak',
        }

    monkeypatch.setattr(bridge, '_request_midi_monitor', midi_request)
    monkeypatch.setattr(bridge, '_request_motion_mapping', mapping_request)

    result = bridge.update_midi_bank('bank_1', {'name': 'Bank 1', 'mappings': []})

    assert result['success'] is True
    assert mapping_calls == [(
        'save_midi_banks',
        {'file_id': 'show.yaml', 'midi_banks': MIDI_STATE},
        3.0,
    )]
    assert [call[0] for call in midi_calls] == ['update_bank', 'apply_banks']
