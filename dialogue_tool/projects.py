from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image

from .media import extract_frame
from .project_run import analysis_lock


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class ProjectRepository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'projects.sqlite'
        self.lock = threading.RLock()
        with self._connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, document TEXT NOT NULL, updated TEXT DEFAULT CURRENT_TIMESTAMP)')

    def _connection(self):
        return sqlite3.connect(self.path, timeout=15)

    def create(self, source: Path) -> dict:
        source = source.resolve(strict=True)
        if not source.is_file():
            raise ValueError('请选择本地视频文件')
        identifier = uuid.uuid4().hex
        directory = self.root / identifier
        directory.mkdir()
        stat = source.stat()
        project = {'id': identifier, 'source': str(source), 'name': source.stem,
                   'source_sha256': file_digest(source), 'source_size': stat.st_size,
                   'source_mtime_ns': stat.st_mtime_ns, 'directory': str(directory),
                   'phase': 'created', 'scan': None, 'scan_directory': None, 'groups': [],
                   'error': None, 'exports': [], 'created_at': datetime.now().isoformat(timespec='seconds')}
        self.save(project)
        return project

    def save(self, project: dict):
        with self.lock, self._connection() as db:
            db.execute('INSERT INTO projects(id,document) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document,updated=CURRENT_TIMESTAMP',
                       (project['id'], json.dumps(project, ensure_ascii=False)))

    def get(self, identifier: str) -> dict:
        with self.lock, self._connection() as db:
            row = db.execute('SELECT document FROM projects WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('项目不存在')
        return json.loads(row[0])

    def list(self) -> list[dict]:
        with self.lock, self._connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT document FROM projects ORDER BY updated DESC,rowid DESC')]

    def update_groups(self, identifier: str, incoming: list[dict]) -> dict:
        directory = Path(self.get(identifier)['directory'])
        with analysis_lock(directory), self.lock:
            project = self.get(identifier)
            valid = {f['id'] for f in project['scan']['frames']} if project['scan'] else set()
            old = {group['id']: group for group in project['groups']}
            result, identifiers = [], set()
            for group in incoming:
                a, b = group['frame_a'], group['frame_b']
                group_id = group.get('id')
                if not isinstance(group_id, str) or not group_id or group_id in identifiers:
                    raise ValueError('配组编号为空或重复')
                identifiers.add(group_id)
                if type(a) is not int or type(b) is not int or a not in valid or b not in valid or a == b:
                    raise ValueError('每组必须选择两张不同的有效候选图')
                previous = old.get(group_id, {})
                clean = {'id': group_id, 'frame_a': a, 'frame_b': b,
                         'prompt': str(group.get('prompt', '')), 'reason': str(previous.get('reason', '手动配组')),
                         'needs_review': bool(group.get('needs_review', previous.get('needs_review', True))),
                         'selected': bool(group.get('selected', True))}
                for key in ('scene_start_seconds', 'scene_end_seconds', 'verification_reason', 'person_a',
                            'person_b', 'duplicate_count', 'validation_state'):
                    if key in previous:
                        clean[key] = previous[key]
                if not clean['needs_review'] and previous.get('needs_review', True):
                    clean['validation_state'] = 'confirmed'
                if previous and (a != previous['frame_a'] or b != previous['frame_b']):
                    clean.update(needs_review=True, selected=False, validation_state='uncertain',
                                 verification_reason='截图已手动替换，请重新确认人物与对话关系。')
                    times = [f['seconds'] for f in project['scan']['frames'] if f['id'] in (a, b)]
                    clean['scene_start_seconds'] = min(times)
                    clean['scene_end_seconds'] = max(times)
                    for side in ('a', 'b'):
                        if clean[f'frame_{side}'] != previous[f'frame_{side}']:
                            clean[f'person_{side}'] = ''
                for side in ('a', 'b'):
                    clean[f'repaired_{side}'] = previous.get(f'repaired_{side}') if previous.get(f'frame_{side}') == clean[f'frame_{side}'] else None
                    clean[f'mask_{side}'] = previous.get(f'mask_{side}') if clean[f'repaired_{side}'] else None
                result.append(clean)
            project['groups'] = result
            self.save(project)
            return project


def assert_source_unchanged(project: dict):
    source = Path(project['source'])
    stat = source.stat()
    if stat.st_size != project['source_size'] or stat.st_mtime_ns != project['source_mtime_ns']:
        raise ValueError('原视频已变化，请重新导入，避免时间点与截图错位')


def original_frame(project: dict, identifier: int, ffmpeg: str, ffprobe: str) -> Path:
    assert_source_unchanged(project)
    frame = next((f for f in project['scan']['frames'] if f['id'] == identifier), None)
    if frame is None:
        raise ValueError('候选帧不存在')
    fingerprint = hashlib.sha256(f"{project['source_sha256']}|{frame['seconds']:.9f}".encode()).hexdigest()[:16]
    output = Path(project['directory']) / 'originals' / f'frame-{identifier:06d}-{fingerprint}.png'
    if output.exists():
        try:
            with Image.open(output) as image:
                image.load()
        except (OSError, ValueError):
            # 保留异常缓存用于排查，不让一次损坏阻断后续截图，也不覆盖其内容。
            output.rename(output.with_name(output.stem + '.invalid-' + uuid.uuid4().hex + output.suffix))
    if not output.exists():
        extract_frame(Path(project['source']), frame['seconds'], output, ffmpeg, ffprobe)
    return output


def project_asset(project: dict, path: str) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to(Path(project['directory']).resolve()) or not resolved.is_file():
        raise ValueError('图片不属于当前项目')
    return resolved


def export_groups(project: dict, destination: Path, ffmpeg: str | None = None, ffprobe: str | None = None) -> Path:
    ffmpeg, ffprobe = ffmpeg or shutil.which('ffmpeg'), ffprobe or shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('缺少 FFmpeg 或 FFprobe')
    groups = [g for g in project['groups'] if g.get('selected')]
    if not groups:
        raise ValueError('请至少勾选一组图片')
    assert_source_unchanged(project)
    output = destination / f'对话截图_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}'
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'source': project['source'], 'source_sha256': project['source_sha256'],
                'project_id': project['id'], 'groups': [], 'attribution': project.get('attribution', '')}
    for index, group in enumerate(groups, 1):
        directory = output / f'组{index:03d}'
        directory.mkdir()
        item = {key: group[key] for key in ('id', 'frame_a', 'frame_b', 'reason', 'needs_review')}
        for key in ('scene_start_seconds', 'scene_end_seconds', 'verification_reason', 'person_a', 'person_b', 'validation_state'):
            if key in group:
                item[key] = group[key]
        for side in ('a', 'b'):
            original = original_frame(project, group[f'frame_{side}'], ffmpeg, ffprobe)
            shutil.copy2(original, directory / f'{side.upper()}_原图.png')
            repaired = group.get(f'repaired_{side}')
            chosen = project_asset(project, repaired) if repaired else original
            if repaired:
                shutil.copy2(chosen, directory / f'{side.upper()}_修复.png')
            shutil.copy2(chosen, directory / f'{side.upper()}_用于生成.png')
            frame = next(f for f in project['scan']['frames'] if f['id'] == group[f'frame_{side}'])
            item[f'image_{side}_variant'] = 'repaired' if repaired else 'original'
            item[f'image_{side}_source_seconds'] = frame['seconds']
            if group.get(f'mask_{side}'):
                shutil.copy2(project_asset(project, group[f'mask_{side}']), directory / f'{side.upper()}_遮罩.png')
        (directory / '提示词.txt').write_text(group.get('prompt', ''), encoding='utf-8')
        manifest['groups'].append(item)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if project.get('attribution'):
        (output / '素材来源.txt').write_text(project['attribution'], encoding='utf-8')
    return output
