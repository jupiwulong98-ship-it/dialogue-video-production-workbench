"""本地扫描全部帧，Codex 只看候选图；语义判断不冒充说话人识别。"""

import json
import math
import time
from pathlib import Path

from PIL import Image, ImageDraw

from .media import probe


def scan_video(source: Path, output: Path, ffmpeg: str, ffprobe: str,
               start: float = 0, end: float | None = None, progress=None) -> dict:
    import cv2
    from scenedetect import ContentDetector, FrameTimecode, SceneManager, open_video

    info = probe(source, ffprobe)
    duration = float(info['format']['duration'])
    start = float(start)
    end = float(min(end if end is not None else duration, duration))
    if not math.isfinite(start) or not math.isfinite(end) or not (0 <= start < end):
        raise ValueError('分析起止时间无效')
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    video = open_video(str(source))
    fps = float(video.frame_rate)
    video.seek(FrameTimecode(start, fps))
    manager = SceneManager()
    manager.auto_downscale = True
    manager.add_detector(ContentDetector(threshold=27, min_scene_len=max(1, round(fps * .35))))
    if progress:
        progress(.02, '逐帧扫描画面切换')
    decoded = manager.detect_scenes(video=video, end_time=FrameTimecode(end, fps), frame_skip=0, show_progress=False)
    scenes = manager.get_scene_list(start_in_scene=True)
    if not scenes:
        scenes = [(FrameTimecode(start, fps), FrameTimecode(end, fps))]
    frames, shots = [], []
    capture = cv2.VideoCapture(str(source))
    try:
        for index, (a, b) in enumerate(scenes):
            t0, t1 = max(start, a.seconds), min(end, b.seconds)
            if t1 <= t0:
                continue
            margin = min(.15, (t1 - t0) / 5)
            points = [t0 + margin, (t0 + t1) / 2, t1 - margin]
            if t1 - t0 > 4:
                points += [t0 + x for x in range(2, math.ceil(t1 - t0), 2) if t0 + x < t1 - margin]
            numbers = sorted(set(round(t * fps) for t in points))
            shot_frames = []
            for number in numbers:
                capture.set(cv2.CAP_PROP_POS_FRAMES, number)
                ok, frame = capture.read()
                if not ok:
                    raise ValueError(f'候选帧读取失败：第 {number} 帧')
                number = max(0, round(capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                identifier = len(frames)
                filename = f'frame-{identifier:05d}.jpg'
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                image.thumbnail((640, 640))
                image.save(output / filename, quality=88)
                pts_seconds = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000
                if pts_seconds <= 0 and number > 0:
                    pts_seconds = number / fps
                frames.append({'id': identifier, 'shot_id': len(shots), 'seconds': pts_seconds,
                               'frame_number': number, 'file': filename,
                               'sharpness': round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
                               'mean_brightness': round(float(gray.mean()), 2)})
                shot_frames.append(identifier)
            shots.append({'id': len(shots), 'start': t0, 'end': t1, 'frame_ids': shot_frames})
            if progress:
                progress(.45 + .5 * (index + 1) / len(scenes), f'候选镜头 {index + 1}/{len(scenes)}')
    finally:
        capture.release()
    report = {'source': str(source.resolve()), 'source_duration': duration, 'start': start, 'end': end,
              'fps': fps, 'read_mode': 'all_frames', 'decoded_frames': decoded,
              'shots': shots, 'frames': frames, 'elapsed_seconds': round(time.monotonic() - started, 3)}
    (output / 'scan.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def merge_pair_proposals(proposals: list[dict], frames: list[dict]) -> list[dict]:
    known = {f['id']: f for f in frames}
    result, seen = [], set()
    for pair in proposals:
        a, b = pair['frame_a'], pair['frame_b']
        if a not in known or b not in known or a == b:
            raise ValueError('配组引用无效')
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(pair))
    return sorted(result, key=lambda p: min(known[p['frame_a']]['seconds'], known[p['frame_b']]['seconds']))


def contact_sheet(frames: list[dict], root: Path, output: Path) -> Path:
    rows = math.ceil(len(frames) / 4)
    sheet = Image.new('RGB', (1280, max(1, rows) * 170), '#edf0f5')
    draw = ImageDraw.Draw(sheet)
    for index, frame in enumerate(frames):
        with Image.open(root / frame['file']) as original:
            image = original.convert('RGB')
        image.thumbnail((316, 138))
        x, y = index % 4 * 320, index // 4 * 170
        sheet.paste(image, (x + 2, y))
        draw.text((x + 4, y + 142), f"ID {frame['id']} | shot {frame['shot_id']} | {frame['seconds']:.2f}s", fill='#111')
    sheet.save(output, quality=90)
    return output
