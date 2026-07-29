from motion_web_bridge.bridge_node import MotionWebBridge


def test_motion_file_detail_contains_complete_original_content(tmp_path):
    content = f'header\n{"x" * 15000}\nlast-frame'
    path = tmp_path / 'motion.json'
    path.write_text(content, encoding='utf-8')
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._analyze_motion_json = lambda _content, *, include_records: {
        'valid': True,
        'include_records': include_records,
    }

    detail = bridge._motion_file_entry(path, include_detail=True)
    summary = bridge._motion_file_entry(path, include_detail=False)

    assert detail['content'] == content
    assert detail['content'].endswith('last-frame')
    assert len(detail['content']) > 12000
    assert 'content' not in summary
