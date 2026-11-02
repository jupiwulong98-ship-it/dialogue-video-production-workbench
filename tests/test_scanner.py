import hashlib
import shutil
import subprocess

import pytest


def test_scanner_detects_cut_without_dumping_all_frames(tmp_path):
    from dialogue_tool.scanner import scan_video
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg missing')
    source = tmp_path / '镜头测试.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=320x180:r=25:d=1',
                    '-f', 'lavfi', '-i', 'color=blue:s=320x180:r=25:d=1',
                    '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264', str(source)], check=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = scan_video(source, tmp_path / 'scan', ffmpeg, ffprobe)
    assert len(report['shots']) == 2
    assert 2 <= len(report['frames']) <= 8
    assert [f['id'] for f in report['frames']] == list(range(len(report['frames'])))
    assert report['read_mode'] == 'all_frames'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert all((tmp_path / 'scan' / f['file']).is_file() for f in report['frames'])


def test_batch_pair_merge_keeps_separate_scene_occurrences():
    from dialogue_tool.scanner import merge_pair_proposals
    frames = [{'id': i, 'seconds': i * 2} for i in range(8)]
    pair = lambda a,b: {'frame_a': a, 'frame_b': b, 'reason': '连续场景', 'needs_review': False}
    # 重叠批次相同配组去重；后面相似但时间不同的组合不得仅因衣服相似被合并。
    result = merge_pair_proposals([pair(0, 1), pair(1, 0), pair(6, 7)], frames)
    assert len(result) == 2
    assert result[0]['frame_a'] == 0
    assert result[1]['frame_a'] == 6


def test_integer_range_is_seconds_not_frame_numbers(tmp_path):
    from dialogue_tool.scanner import scan_video
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg missing')
    source = tmp_path / 'range.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=green:s=320x180:r=25:d=3',
                    '-c:v', 'libx264', str(source)], check=True)
    report = scan_video(source, tmp_path / 'range-scan', ffmpeg, ffprobe, start=1, end=2)
    assert report['decoded_frames'] == 25
    assert len(report['shots']) == 1
    assert all(1 <= frame['seconds'] < 2 for frame in report['frames'])
