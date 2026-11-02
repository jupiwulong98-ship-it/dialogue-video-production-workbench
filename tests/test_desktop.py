import pytest

from dialogue_tool.desktop import DesktopAPI
from dialogue_tool.service import Service


def make_api(tmp_path):
    service = Service({'data': str(tmp_path / 'data')})
    source = tmp_path / 'sample.mp4'
    source.write_bytes(b'sample')
    first = service.projects.create(source)
    second = service.projects.create(source)
    second['groups'] = [{'id': 'group', 'frame_a': 1, 'frame_b': 2, 'prompt': 'keep'}]
    service.projects.save(second)
    api = DesktopAPI(service)
    api.current = second['id']
    return api, service, first, second


def test_save_rejects_draft_from_another_project(tmp_path):
    api, service, first, second = make_api(tmp_path)
    with pytest.raises(ValueError, match='项目'):
        api.save_groups([], first['id'])
    assert service.projects.get(second['id'])['groups'][0]['prompt'] == 'keep'


def test_repair_rejects_mask_for_a_replaced_frame(tmp_path):
    api, _, _, second = make_api(tmp_path)
    with pytest.raises(ValueError, match='截图'):
        api.start_repair('group', 'a', [0, 0, 8, 8], False,
                         expected_frame=99, project_id=second['id'])
    assert api.busy is False
