import json
from pathlib import Path

import pytest
from PIL import Image

from dialogue_tool.projects import ProjectRepository


def test_frame_batches_do_not_repeat_a_tail_already_covered():
    from dialogue_tool.service import frame_batches
    assert [len(b) for b in frame_batches(list(range(23)))] == [23]
    assert [len(b) for b in frame_batches(list(range(24)))] == [24]
    assert [len(b) for b in frame_batches(list(range(26)))] == [24, 6]


def test_existing_groups_are_never_discarded_by_scan_only(tmp_path):
    from dialogue_tool.service import Service
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    service = Service({'data': str(tmp_path / 'data')})
    project = service.projects.create(source)
    project['groups'] = [{'id': 'existing', 'prompt': '用户确认文案'}]
    service.projects.save(project)
    with pytest.raises(ValueError, match='已有配组'):
        service.analyze(project['id'], scan_only=True)
    assert service.projects.get(project['id'])['groups'][0]['prompt'] == '用户确认文案'


def test_resume_does_not_repeat_successful_codex_batches(tmp_path):
    from dialogue_tool.service import Service
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    counter = tmp_path / 'calls.txt'
    executable = tmp_path / 'fake-codex'
    executable.write_text('#!/usr/bin/env python3\n' + f'''import sys,json,pathlib
counter=pathlib.Path({str(counter)!r})
n=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(n))
if n==2: sys.exit(1)
path=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
path.write_text(json.dumps({{"summary":"测试批次","pairs":[]}}))
''')
    executable.chmod(0o700)
    config = {'data': str(tmp_path / 'data'), 'codex': str(executable)}
    service = Service(config)
    project = service.projects.create(source)
    folder = Path(project['directory']) / 'scan'
    folder.mkdir()
    frames = []
    for i in range(26):
        Image.new('RGB', (16, 16), 'red').save(folder / f'{i}.jpg')
        frames.append({'id': i, 'shot_id': i, 'seconds': i, 'file': f'{i}.jpg'})
    project.update(scan={'frames': frames}, scan_directory=str(folder), phase='scanned')
    service.projects.save(project)
    with pytest.raises(Exception):
        service.analyze(project['id'])
    assert counter.read_text() == '2'
    service.analyze(project['id'])
    assert counter.read_text() == '3'


def test_verification_resume_keeps_dedupe_and_prepares_full_images(tmp_path, monkeypatch):
    from dialogue_tool.service import Service
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    counter = tmp_path / 'calls.txt'
    executable = tmp_path / 'fake-codex'
    executable.write_text('#!/usr/bin/env python3\n' + f'''import sys,json,pathlib
counter=pathlib.Path({str(counter)!r})
n=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(n))
prompt=sys.stdin.read()
pair=lambda a,b:dict(frame_a=a,frame_b=b,reason='测试对话',needs_review=False)
if '去重任务' in prompt:
 result={{'clusters':[{{'members':[0,1],'representative':0}}]}}
elif '复核任务' in prompt:
 if n==3: sys.exit(1)
 result={{'summary':'复核清晰画面','pairs':[pair(2,3)]}}
else:
 result={{'summary':'两个重复候选','pairs':[pair(0,1),pair(2,3)]}}
path=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
path.write_text(json.dumps(result))
''')
    executable.chmod(0o700)
    service = Service({'data': str(tmp_path / 'data'), 'codex': str(executable)})
    project = service.projects.create(source)
    folder = Path(project['directory']) / 'scan'
    folder.mkdir()
    frames = []
    for i in range(4):
        Image.new('RGB', (40, 24), 'red').save(folder / f'{i}.jpg')
        frames.append({'id': i, 'shot_id': i % 2, 'seconds': i + 10, 'file': f'{i}.jpg', 'sharpness': i})
    project.update(scan={'frames': frames}, scan_directory=str(folder), phase='scanned')
    service.projects.save(project)
    full = tmp_path / 'full.png'
    Image.new('RGB', (400, 240), 'blue').save(full)
    monkeypatch.setattr('dialogue_tool.service.original_frame', lambda *args: full)
    with pytest.raises(Exception):
        service.analyze(project['id'])
    assert counter.read_text() == '3'
    result = service.analyze(project['id'])
    assert counter.read_text() == '4'
    assert len(result['groups']) == 1
    assert (result['groups'][0]['frame_a'], result['groups'][0]['frame_b']) == (2, 3)
    assert result['groups'][0]['selected'] is True
    assert result['analysis_report']['prepared_images'] == 2


def test_invalid_dedupe_is_not_cached_as_a_successful_stage(tmp_path):
    from dialogue_tool.service import Service
    from dialogue_tool.providers import ProviderError
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    executable = tmp_path / 'fake-codex'
    executable.write_text('''#!/usr/bin/env python3
import json,pathlib,sys
path=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
path.write_text(json.dumps({'clusters':[{'members':[0],'representative':0}]}))
''')
    executable.chmod(0o700)
    service = Service({'data': str(tmp_path / 'data'), 'codex': str(executable)})
    project = service.projects.create(source)
    folder = Path(project['directory']) / 'scan'
    folder.mkdir()
    frames = []
    for i in range(4):
        Image.new('RGB', (10, 10), 'red').save(folder / f'{i}.jpg')
        frames.append({'id': i, 'seconds': i, 'file': f'{i}.jpg'})
    project['scan_directory'] = str(folder)
    with pytest.raises(ProviderError):
        service._cached_request(project, 'dedupe', frames, '去重任务')
    assert not project.get('analysis_cache')


def test_default_discovery_covers_all_candidate_frames(tmp_path):
    from dialogue_tool.service import Service
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    seen = tmp_path / 'seen.jsonl'
    executable = tmp_path / 'fake-codex'
    executable.write_text('#!/usr/bin/env python3\n' + f'''import json,pathlib,sys
images=[sys.argv[i+1] for i,arg in enumerate(sys.argv[:-1]) if arg=='--image']
with pathlib.Path({str(seen)!r}).open('a') as stream:stream.write(json.dumps(images)+'\\n')
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({{'summary':'没有对话','pairs':[]}}))
''')
    executable.chmod(0o700)
    service = Service({'data': str(tmp_path / 'data'), 'codex': str(executable)})
    project = service.projects.create(source)
    folder = Path(project['directory']) / 'scan'
    folder.mkdir()
    frames = []
    for i in range(30):
        Image.new('RGB', (10, 10), 'red').save(folder / f'{i}.jpg')
        frames.append({'id': i, 'seconds': i, 'shot_id': i//15, 'file': f'{i}.jpg'})
    project.update(scan={'frames':frames},scan_directory=str(folder))
    service.projects.save(project)
    service.analyze(project['id'])
    supplied = {Path(image).name for line in seen.read_text().splitlines() for image in json.loads(line)}
    assert supplied == {f'{i}.jpg' for i in range(30)}
