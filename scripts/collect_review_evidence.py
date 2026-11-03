"""收集已有测试证据；不运行识别，不修改任务数据库，不读取账号配置。"""

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'review'
BUILD = ROOT / '.review-build'
TEXT = {'.json', '.jsonl', '.txt', '.log', '.md', '.py', '.js', '.html'}
KEYS = re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{20,}')


def clean(data, suffix):
    if suffix not in TEXT:
        return data
    text = data.decode('utf-8')
    text = text.replace(str(ROOT), 'PROJECT_ROOT')
    text = re.sub(r'/Users/[^/\s\"\x27]+', '/Users/USER', text)
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[EMAIL_REDACTED]', text)
    text = re.sub(r'("thread_id"\s*:\s*")[^"]+', r'\1REDACTED', text)
    return KEYS.sub('[TOKEN_REDACTED]', text).encode('utf-8')


def save(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(clean(source.read_bytes(), source.suffix))


def main():
    OUT.mkdir(exist_ok=True)
    BUILD.mkdir(exist_ok=True)
    # SQLite 在线只读快照，保留用户当前人工确认状态，另存自动结果用于对照。
    with sqlite3.connect(f'file:{ROOT / "workspaces/projects.sqlite"}?mode=ro', uri=True) as db:
        projects = [json.loads(row[0]) for row in db.execute('SELECT document FROM projects')]
    for project in projects:
        target = OUT / 'projects' / f'{project["id"]}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(clean(json.dumps(project, ensure_ascii=False, indent=2).encode(), '.json'))

    selected = {
        'validation/batch-dialogue/accuracy-result.json': 'tos/automatic-result.json',
        'validation/batch-dialogue/accuracy-pairs.jpg': 'tos/automatic-pairs.jpg',
        'validation/hgf-scenes/result.json': 'hgf-scenes/automatic-result.json',
        'validation/hgf-scenes/hgf-pairs.jpg': 'hgf-scenes/automatic-pairs.jpg',
        'validation/hgf-scenes/fixture-manifest.json': 'hgf-scenes/fixture-manifest.json',
        'validation/hgf-scenes/long-scan-metrics.json': 'hgf-full/scan-metrics.json',
        'validation/hgf-scenes/long-scan/scan.json': 'hgf-full/scan.json',
        'validation/hgf-full/scope.json': 'hgf-full/scope.json',
        'validation/hgf-full/shots-index.json': 'hgf-full/shots-index.json',
        'validation/hgf-full/全部189镜头预览.html': 'hgf-full/all-189-shots.html',
        'validation/hgf-full/tests.txt': 'checks/historical-tests.txt',
        'validation/repair-check/comparison.jpg': 'repair/comparison.jpg',
        'validation/repair-check/original.png': 'repair/original.png',
        'validation/repair-check/with-test-logo.png': 'repair/with-test-logo.png',
        'validation/repair-check/mask.png': 'repair/mask.png',
        'validation/repair-check/repaired.png': 'repair/repaired.png',
    }
    for source, target in selected.items():
        save(ROOT / source, OUT / target)

    for label, project_id in [('tos', '5d74bb6e185745f3983f56afa9828fbf'),
                              ('hgf-scenes', 'dd47130c0cec447088887a41b80e1889')]:
        folder = ROOT / 'workspaces' / project_id / 'originals'
        for file in folder.glob('*.png'):
            save(file, OUT / label / 'originals' / file.name)
        # 模型原始结构化输出可直接审阅；其他批次及事件日志在完整下载包中。
        for file in (ROOT / 'workspaces' / project_id).glob('*/analysis.json'):
            save(file, OUT / label / 'model-results' / file.parent.name / file.name)

    sources = []
    for name, url in [
        ('tears_of_steel_720p.mov', 'https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov'),
        ('His-Girl-Friday.mp4', 'https://archive.org/download/his_girl_friday/his_girl_friday.mp4'),
        ('HGF-multiple-dialogues-test.mp4', None),
    ]:
        path = ROOT / 'validation/source' / name
        sources.append({'file': name, 'bytes': path.stat().st_size,
                        'sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest(),
                        'source_url': url, 'included_in_archive': url is None})
    (OUT / 'sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2) + '\n')

    archive = BUILD / 'review-evidence-20261103.zip'
    entries = []
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=4) as bundle:
        for folder in [ROOT / 'validation', *(ROOT / 'workspaces' / p['id'] for p in projects)]:
            for file in sorted(folder.rglob('*')):
                if not file.is_file() or file.is_symlink():
                    continue
                rel = file.relative_to(ROOT).as_posix()
                # 原片用来源与 SHA256 复现，避免把完整电影嵌入安装源码。
                if rel.startswith('validation/source/') and file.name != 'HGF-multiple-dialogues-test.mp4':
                    continue
                # 两处扫描是工作区候选的副本；保留清单，图片在 workspaces 内。
                if (rel.startswith('validation/full-movie-scan/') or rel.startswith('validation/hgf-scenes/long-scan/')) and file.suffix != '.json':
                    continue
                if file.suffix in {'.lock', '.pyc', '.sqlite', '.db'} or '__pycache__' in file.parts:
                    continue
                data = clean(file.read_bytes(), file.suffix)
                bundle.writestr(rel, data)
                entries.append({'path': rel, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        for file in sorted((OUT / 'projects').glob('*.json')):
            data = file.read_bytes()
            rel = file.relative_to(ROOT).as_posix()
            bundle.writestr(rel, data)
            entries.append({'path': rel, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        bundle.writestr('ARCHIVE-MANIFEST.json', json.dumps(entries, ensure_ascii=False, indent=2))
    digest = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
    (OUT / 'archive.json').write_text(json.dumps({
        'filename': archive.name, 'bytes': archive.stat().st_size, 'sha256': digest,
        'files': len(entries), 'created_date': '2026-11-03',
        'omitted': ['两部完整源电影（见 sources.json）', '重复扫描候选副本', '账号配置、数据库和锁文件'],
        'sanitization': ['本机绝对项目路径替换为 PROJECT_ROOT', '用户目录、邮箱、模型会话编号及令牌匹配脱敏'],
        'notice': '历史脚本仅作证据，含脱敏路径；不会自动恢复任务或运行识别。',
    }, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size, 'files': len(entries)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
