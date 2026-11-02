"""同一段交流只出一组；无合格反打自动排除，不产生人工待确认任务。"""
from pathlib import Path
from PIL import Image

from dialogue_tool.dialogue_analysis import refinement_frames, consolidate_dialogues
from dialogue_tool.service import Service


def test_refinement_can_replace_first_two_shots_with_later_clear_frames():
    frames = [
        {'id': 0, 'shot_id': 0, 'seconds': 10.0, 'sharpness': 1},
        {'id': 1, 'shot_id': 1, 'seconds': 11.0, 'sharpness': 1},
        {'id': 2, 'shot_id': 2, 'seconds': 30.0, 'sharpness': 100},
        {'id': 3, 'shot_id': 3, 'seconds': 40.0, 'sharpness': 120},
        {'id': 4, 'shot_id': 4, 'seconds': 100.0, 'sharpness': 500},
    ]
    proposal = {'frame_a': 0, 'frame_b': 1, 'scene_start_seconds': 10,
                'scene_end_seconds': 50}
    result = refinement_frames(proposal, frames)
    assert [f['id'] for f in result[:2]] == [0, 1]
    assert {2, 3}.issubset({f['id'] for f in result})
    assert 4 not in {f['id'] for f in result}


def test_repeated_reverses_are_one_episode_but_later_dialogue_separate():
    frames = [{'id': i, 'shot_id': i, 'seconds': t} for i, t in
              enumerate([0, 3, 7, 11, 200, 203])]
    proposals = [
        {'frame_a': 0, 'frame_b': 1, 'reason': 'A/B'},
        {'frame_a': 2, 'frame_b': 3, 'reason': 'A/B又切回来'},
        {'frame_a': 4, 'frame_b': 5, 'reason': '另一场会话'},
    ]
    def compare(window):
        assert window == [0, 1]
        return {'clusters': [{'members': [0, 1], 'representative': 0}]}
    result = consolidate_dialogues(proposals, frames, compare)
    assert len(result) == 2
    assert result[0]['scene_start_seconds'] == 0
    assert result[0]['scene_end_seconds'] == 11


def test_failed_ab_is_skipped_instead_of_asking_for_manual_repair(tmp_path, monkeypatch):
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'source')
    service = Service({'data': str(tmp_path / 'data'), 'codex': 'fake'})
    project = service.projects.create(source)
    scan_dir = Path(project['directory']) / 'scan'
    scan_dir.mkdir()
    frames = []
    for i in range(2):
        Image.new('RGB', (16, 16), 'red').save(scan_dir / f'{i}.jpg')
        frames.append({'id': i, 'seconds': i + 1, 'shot_id': i,
                       'file': f'{i}.jpg', 'sharpness': i})
    project.update(scan={'frames': frames}, scan_directory=str(scan_dir))
    service.projects.save(project)
    def fake_request(project, kind, choices, context):
        if kind == 'discovery':
            return {'summary': '发现一段对话', 'pairs': [
                {'frame_a': 0, 'frame_b': 1, 'reason': '有人对话', 'needs_review': False}
            ]}
        if kind == 'verify':
            return {'summary': '没有清晰独立反打', 'pairs': []}
        raise AssertionError(kind)
    monkeypatch.setattr(service, '_cached_request', fake_request)
    result = service.analyze(project['id'])
    assert result['groups'] == []
    assert result['analysis_report']['review_groups'] == 0
    assert result['analysis_report']['excluded_segments'] == 1
    assert result['dialogue_segments'][0]['status'] == 'excluded'
    assert '没有清晰独立反打' in result['excluded_dialogues'][0]['reason']
