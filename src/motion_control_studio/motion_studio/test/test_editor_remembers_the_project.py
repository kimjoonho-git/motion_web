"""편집 노드가 **프로젝트를 한 번만 받는다** · §6-293

편집 노드가 프로젝트를 쓰는 곳은 **충돌 판정 한 군데**다 · 그런데 화면이
편집할 때마다 통째로 올렸다 · 실측으로 2.98 MB 였고, 레이어까지 합쳐 한 번에
4.2 MB 가 오갔다 · 포인트 하나를 끌 때마다 그랬다.

프로젝트가 바뀌면 `project_generation` 이 올라간다 · 같은 세대면 기억한 것을
쓰고, 다르면 「보내 주세요」라고 답한다.

**고른 축까지 같아야 쓴다** · 화면은 그 편집에 걸린 축의 프레임만 남겨
보내므로, 다른 축 편집에 그대로 쓰면 없는 축은 충돌이 없다고 보게 된다.
"""

from motion_studio.editor_node import MotionStudioEditorNode, _project_axes


def _node():
    node = MotionStudioEditorNode.__new__(MotionStudioEditorNode)
    node._layer_motion_ids = staticmethod(
        lambda layer: {
            str(motion_id)
            for frame in layer.get('frames') or []
            for motion_id in (frame.get('values') or {})
        }
    ).__func__
    return node


def _project(axes, generation=7):
    return {
        'period_sec': 0.02,
        'layers': [{
            'layer_id': 'other', 'name': '다른 레이어', 'enabled': True,
            'frames': [{'time_sec': 0.02, 'values': {a: 1.0 for a in axes}}],
        }],
    }


def _layer(axes):
    return {'frames': [{'time_sec': 0.02, 'values': {a: 0.0 for a in axes}}]}


def test_the_first_edit_must_bring_the_project():
    node = _node()

    assert node._project_for({'project_generation': 7, 'layer': _layer(['1-1'])}) is None


def test_the_next_edit_reuses_it():
    node = _node()
    project = _project(['1-1'])

    node._project_for({'project_generation': 7, 'project': project, 'layer': _layer(['1-1'])})

    assert node._project_for({
        'project_generation': 7, 'layer': _layer(['1-1']),
    }) is project


def test_a_new_generation_asks_again():
    """프로젝트가 바뀌었다 · 옛 사본으로 판정하면 이미 지운 레이어와 충돌한다."""
    node = _node()
    node._project_for({'project_generation': 7, 'project': _project(['1-1']), 'layer': _layer(['1-1'])})

    assert node._project_for({'project_generation': 8, 'layer': _layer(['1-1'])}) is None


def test_another_axis_asks_again():
    """1-1 로 걸러 받은 사본으로 1-2 를 판정하면 충돌을 놓친다."""
    node = _node()
    node._project_for({'project_generation': 7, 'project': _project(['1-1']), 'layer': _layer(['1-1'])})

    assert node._project_for({'project_generation': 7, 'layer': _layer(['1-2'])}) is None


def test_fewer_axes_can_reuse_a_wider_copy():
    """두 축으로 받아 둔 사본은 한 축 편집에 그대로 쓸 수 있다."""
    node = _node()
    wide = _project(['1-1', '1-2'])
    node._project_for({'project_generation': 7, 'project': wide, 'layer': _layer(['1-1', '1-2'])})

    assert node._project_for({'project_generation': 7, 'layer': _layer(['1-2'])}) is wide


def test_what_axes_the_copy_carries():
    assert _project_axes(_project(['1-1', '1-4'])) == {'1-1', '1-4'}
    assert _project_axes({'layers': []}) == set()
