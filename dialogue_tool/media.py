import json
import math
import os
import tempfile
from pathlib import Path

from PIL import Image

from .providers import ProviderError, run


class MediaError(ValueError):
    pass


def probe(source: Path, ffprobe: str) -> dict:
    result = run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(source)])
    if result.returncode:
        raise MediaError(result.stderr[-2000:])
    return json.loads(result.stdout)


def safe_output(source: Path, output: Path):
    if source.resolve() == output.resolve() or output.exists():
        raise MediaError('输出不能覆盖原素材或已有文件')
    output.parent.mkdir(parents=True, exist_ok=True)


def extract_frame(source: Path, seconds: float, output: Path, ffmpeg: str, ffprobe: str) -> Path:
    safe_output(source, output)
    info = probe(source, ffprobe)
    duration = float(info['format']['duration'])
    if not math.isfinite(seconds) or seconds < 0 or seconds >= duration:
        raise MediaError(f'截图时间必须在 0 至 {duration} 秒之间')
    with tempfile.TemporaryDirectory(prefix='.frame-', dir=output.parent) as directory:
        temporary = Path(directory) / ('frame' + output.suffix)
        result = run([ffmpeg, '-v', 'error', '-nostdin', '-n', '-ss', str(seconds), '-i', str(source),
                      '-map', '0:v:0', '-frames:v', '1', '-update', '1', str(temporary)], 180)
        if result.returncode or not temporary.is_file():
            raise MediaError(f'截图失败：{result.stderr[-2000:]}')
        with Image.open(temporary) as image:
            image.load()
        # 相同文件系统内原子发布，若已有文件则拒绝覆盖；失败不留下半截最终文件。
        os.link(temporary, output)
    return output


def normalize_video(source: Path, output: Path, ffmpeg: str, ffprobe: str) -> dict:
    safe_output(source, output)
    info = probe(source, ffprobe)
    videos = [s for s in info['streams'] if s['codec_type'] == 'video']
    audios = [s for s in info['streams'] if s['codec_type'] == 'audio']
    if len(videos) != 1:
        raise MediaError('要求一个视频轨道')
    args = [ffmpeg, '-v', 'error', '-nostdin', '-n', '-i', str(source), '-map', '0:v:0', '-map', '0:a?']
    args += ['-c:v', 'copy'] if videos[0]['codec_name'] == 'h264' else ['-c:v', 'libx264', '-crf', '18', '-preset', 'medium']
    args += ['-c:a', 'copy'] if all(s['codec_name'] == 'aac' for s in audios) else ['-c:a', 'aac', '-b:a', '192k']
    args += ['-movflags', '+faststart', str(output)]
    result = run(args, 600)
    if result.returncode:
        raise MediaError(f'整理输出失败：{result.stderr[-2000:]}')
    exported = probe(output, ffprobe)
    decode = run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(output), '-f', 'null', '-'], 600)
    if decode.returncode:
        raise MediaError(f'成片解码检查失败：{decode.stderr[-2000:]}')
    return exported
