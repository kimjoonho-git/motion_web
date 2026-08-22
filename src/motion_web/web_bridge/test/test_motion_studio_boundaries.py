import json
import threading
from pathlib import Path

from fastapi import FastAPI
from std_msgs.msg import String

from motion_web_bridge.bridge_node import MotionWebBridge
from motion_web_bridge.motion_studio_bridge import MotionStudioRosBridge
from motion_web_bridge.motion_studio_routes import register_motion_studio_routes
from motion_web_bridge.motion_studio_sync import MotionStudioSync
from motion_common import rpc


def test_motion_studio_routes_are_registered_from_the_route_module():
    app = FastAPI()
    register_motion_studio_routes(
        app,
        object(),
        lambda method, *args: method(*args),
        lambda bridge, method: method(),
    )
    routes = {
        (route.path, method)
        for route in app.routes
        if route.path.startswith('/api/motion-studio')
        for method in route.methods
    }

    assert routes == {
        ('/api/motion-studio', 'GET'),
        ('/api/motion-studio/projects', 'POST'),
        ('/api/motion-studio/projects/load', 'POST'),
        ('/api/motion-studio/import', 'POST'),
        ('/api/motion-studio/project', 'PUT'),
        ('/api/motion-studio/layers', 'PUT'),
        ('/api/motion-studio/layers', 'POST'),
        ('/api/motion-studio/layers/data', 'PUT'),
        ('/api/motion-studio/layers/{layer_id}', 'DELETE'),
        ('/api/motion-studio/layers/{layer_id}/duplicate', 'POST'),
        ('/api/motion-studio/editor/transform', 'POST'),
        ('/api/motion-studio/editor/merge-preview', 'POST'),
        ('/api/motion-studio/layers/merge', 'POST'),
        ('/api/motion-studio/record', 'POST'),
        ('/api/motion-studio/play', 'POST'),
        ('/api/motion-studio/initialize', 'POST'),
        ('/api/motion-studio/stop', 'POST'),
        ('/api/motion-studio/export', 'POST'),
    }


def test_ros_bridge_rejects_a_response_from_an_old_project_generation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_store = rpc.ResultStore()
    bridge._response_matches_current_generation = lambda payload: (
        payload.get('project_generation') == 2
    )
    service = MotionStudioRosBridge(bridge)

    service.response_callback(String(data=json.dumps({
        'request_id': 'old', 'project_generation': 1,
    })))
    service.response_callback(String(data=json.dumps({
        'request_id': 'current', 'project_generation': 2,
    })))

    assert bridge._motion_studio_store.keys() == {'current'}
    assert bridge._motion_studio_store.take('current') == {
        'request_id': 'current', 'project_generation': 2,
    }


def test_sync_service_clears_only_motion_studio_project_memory():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_editor_lock = threading.Lock()
    bridge._motion_studio_store = rpc.ResultStore()
    bridge._motion_studio_store.store('request', {'project_id': 'old'})
    bridge._motion_studio_status = {'project_id': 'old'}
    bridge._motion_studio_workspace_signatures = {'old': {'layers': 'signature'}}
    bridge._motion_studio_editor_store = rpc.ResultStore()
    bridge._motion_studio_editor_store.store('editor', {'project_id': 'old'})
    bridge._motion_run_status = {'project_id': 'keep'}

    MotionStudioSync(bridge).clear_project_memory()

    assert bridge._motion_studio_store.pending_count() == 0
    assert bridge._motion_studio_status == {}
    assert bridge._motion_studio_workspace_signatures == {}
    assert bridge._motion_studio_editor_store.pending_count() == 0
    assert bridge._motion_run_status == {'project_id': 'keep'}


def test_bridge_node_keeps_only_motion_studio_service_delegation():
    package = Path(__file__).parents[1] / 'motion_web_bridge'
    node_source = (package / 'bridge_node.py').read_text(encoding='utf-8')
    route_source = (package / 'motion_studio_routes.py').read_text(encoding='utf-8')
    transport_source = (
        package / 'motion_studio_bridge.py'
    ).read_text(encoding='utf-8')
    sync_source = (package / 'motion_studio_sync.py').read_text(encoding='utf-8')

    assert "@app.get('/api/motion-studio')" not in node_source
    assert 'register_motion_studio_routes(' in node_source
    assert 'return self._motion_studio_transport().request(' in node_source
    assert 'return self._motion_studio_sync().sync_result(result)' in node_source
    assert "@app.get('/api/motion-studio')" in route_source
    assert 'class MotionStudioRosBridge:' in transport_source
    assert 'class MotionStudioSync:' in sync_source
