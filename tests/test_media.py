import hashlib
import shutil
import subprocess

import pytest
from PIL import Image


def test_extract_frame_preserves_source_and_rejects_out_of_range(tmp_path):
    from dialogue_tool.media import extract_frame, MediaError
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg unavailable')
    source = tmp_path / '带 空格原片.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=red:size=320x180:rate=25', '-t', '1', '-c:v', 'libx264', str(source)], check=True)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / '截图.png'
    extract_frame(source, 0.4, output, ffmpeg, ffprobe)
    with Image.open(output) as im:
        assert im.size == (320, 180)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with pytest.raises(MediaError):
        extract_frame(source, 2, tmp_path / 'bad.png', ffmpeg, ffprobe)
    with pytest.raises(MediaError):
        extract_frame(source, 0.2, source, ffmpeg, ffprobe)


def test_rect_mask_has_exact_bounds_and_rejects_invalid_rect():
    from dialogue_tool.repair import rectangle_mask
    mask = rectangle_mask((100, 80), (10, 20, 30, 40))
    assert mask.getbbox() == (10, 20, 30, 40)
    with pytest.raises(ValueError):
        rectangle_mask((100, 80), (10, 20, 101, 40))


def test_repair_composite_preserves_every_pixel_outside_mask():
    from dialogue_tool.repair import composite_repair, rectangle_mask
    source = Image.new('RGB', (32, 24), 'blue')
    generated = Image.new('RGB', (32, 24), 'green')
    mask = rectangle_mask(source.size, (4, 4, 12, 12))
    result = composite_repair(source, generated, mask)
    assert result.getpixel((2, 2)) == source.getpixel((2, 2))
    assert result.getpixel((8, 8)) == generated.getpixel((8, 8))


def test_failed_extraction_never_publishes_partial_output(tmp_path):
    from dialogue_tool.media import extract_frame, MediaError
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg unavailable')
    source = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=160x90:r=25:d=1',
                    '-c:v', 'libx264', str(source)], check=True)
    broken = tmp_path / 'broken-ffmpeg'
    broken.write_text('#!/usr/bin/env python3\nimport sys,pathlib\npathlib.Path(sys.argv[-1]).write_bytes(b"partial PNG")\nsys.exit(1)\n')
    broken.chmod(0o700)
    output = tmp_path / 'original.png'
    with pytest.raises(MediaError):
        extract_frame(source, .3, output, str(broken), ffprobe)
    assert not output.exists()
