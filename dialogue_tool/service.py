import hashlib
import json
import time
import uuid
from pathlib import Path

from .projects import ProjectRepository, assert_source_unchanged, export_groups, original_frame
from .providers import ProviderError, analyze_images, request_images, validate_analysis
from .dialogue_analysis import (CLUSTER_SCHEMA, consolidate_dialogues, pair_span, refinement_frames,
                                validate_clusters, verified_group)
from .repair import rectangle_mask, repair_image
from .scanner import merge_pair_proposals, scan_video
from .project_run import analysis_lock


def frame_batches(frames: list) -> list[list]:
    batches, start = [], 0
    while start < len(frames):
        batches.append(frames[start:start + 24])
        if start + 24 >= len(frames):
            break
        start += 20
    return batches


class Service:
    def __init__(self, config: dict):
        self.config = config
        self.projects = ProjectRepository(Path(config['data']))

    def analyze(self, identifier: str, start: float = 0, end=None, *, scan_only=False, progress=None) -> dict:
        directory = Path(self.projects.get(identifier)['directory'])
        with analysis_lock(directory):
            return self._analyze(identifier, start, end, scan_only=scan_only, progress=progress)

    def _analyze(self, identifier: str, start: float = 0, end=None, *, scan_only=False, progress=None) -> dict:
        project = self.projects.get(identifier)
        assert_source_unchanged(project)
        if project['groups']:
            raise ValueError('已有配组，先保留现有结果；如需重新分析，请将视频导入为新项目')
        started = time.monotonic()
        user_progress = progress

        def progress(value, message):
            project['run_status'] = {'progress': value, 'message': message}
            self.projects.save(project)
            if user_progress:
                user_progress(value, message)

        try:
            if project['scan'] is None or scan_only:
                scan_dir = Path(project['directory']) / f'scan-{uuid.uuid4().hex[:8]}'
                project['phase'], project['error'] = 'scanning', None
                self.projects.save(project)
                report = scan_video(Path(project['source']), scan_dir, self.config['ffmpeg'], self.config['ffprobe'],
                                    start, end, (lambda v, m: progress(v * .15, m)) if progress else None)
                project.update(scan=report, scan_directory=str(scan_dir), phase='scanned', groups=[])
                self.projects.save(project)
            if scan_only:
                return project
            frames = project['scan']['frames']
            if len(frames) < 2:
                raise ValueError('有效候选图少于两张，请更换时间范围')
            if not self.config.get('codex'):
                raise ValueError('未找到 Codex CLI，请先配置其路径并登录')
            project['phase'] = 'analyzing'
            project['error'], project['error_code'] = None, None
            self.projects.save(project)
            proposals, previous_summary = [], ''
            discovery = frames
            batches = frame_batches(discovery)
            project['analysis_used_keys'] = []
            requests_before = project.get('analysis_requests_started', 0)
            hits_before = project.get('analysis_cache_hits', 0)
            for index, batch in enumerate(batches):
                if len(batch) < 2:
                    continue
                if progress:
                    progress(.16 + .49 * index / len(batches), f'识别对话 {index + 1}/{len(batches)}，已发现 {len(proposals)} 组候选')
                context = ('先按连续交流划分对话片段，再为每段寻找标准正反打的 A/B 身份锚点。'
                           '同一场交流反复切 A/B 仍只提议一组，不要按每次切镜重复提议；'
                           '同场不同人物组合的独立交流可以分开。跨场景不得合并。'
                           '必须有两个不同真人的独立镜头和明确交流证据，不能用同框两张图凑对；'
                           '缺少反打的片段只在summary说明，不要编造A/B。'
                           '用span_start/span_end覆盖本批次可见的整段交流，以便后续在全段选最佳帧。'
                           '人物描述必须结合脸部和服装，不能只靠同色衣服。时间点（秒）、镜头编号：'
                           + json.dumps([{'time': round(f['seconds'], 3), 'shot': f['shot_id']} for f in batch])
                           + '。上一批场景摘要（只辅助，不可虚构当前图片）：' + previous_summary)
                result = self._cached_request(project, 'discovery', batch, context)
                previous_summary = result['summary'][-1200:]
                for pair in result['pairs']:
                    first = pair.get('span_start') if pair.get('span_start') is not None else min(pair['frame_a'], pair['frame_b'])
                    last = pair.get('span_end') if pair.get('span_end') is not None else max(pair['frame_a'], pair['frame_b'])
                    proposals.append(dict(pair, frame_a=batch[pair['frame_a']]['id'],
                                          frame_b=batch[pair['frame_b']]['id'],
                                          scene_start_seconds=batch[first]['seconds'],
                                          scene_end_seconds=batch[last]['seconds']))
                project['analysis_progress'] = {'step': 'discovery', 'completed': index + 1,
                                                'total': len(batches), 'proposals': len(proposals)}
                self.projects.save(project)
            proposals = merge_pair_proposals(proposals, frames)
            project['analysis_proposals'] = proposals
            self.projects.save(project)
            proposals = self._deduplicate(project, proposals, frames, progress)
            # 第一阶段：先确认独立的连续对话片段（同段反复切镜已经去重）。
            # 第二阶段：在每段的所有候选镜头中选择一组最适合图生视频的 A/B。
            groups, excluded, episodes = [], [], []
            for index, proposal in enumerate(proposals):
                if progress:
                    progress(.75 + .18 * index / max(1, len(proposals)), f'二次检查与选图 {index + 1}/{len(proposals)}')
                choices = refinement_frames(proposal, frames)
                context = ('这是已识别的一段连续人物交流；只从本段里选最佳的一组 A/B，不拆成多组。'
                           '附件0和1是初步识别的人物身份锚点，其余来自同段的不同镜头/时刻。'
                           '必须为两个不同真人、同一连续交流的标准正反打，两张图分别是清楚的单人主体，'
                           '脸部可见、无遮挡或少遮挡、接近正脸、画面不模糊、表情自然、无字幕压脸。'
                           '允许替换到本段更清晰的其他镜头，但人物身份不得改变。'
                           '同一人的不同表情、多人合照、背影、严重侧脸/遮挡、机器人、屏幕人物、'
                           '没有独立反打或不能证明交流关系，均不得进入成功结果。'
                           '如果任一侧没有可用于图生视频的清晰正脸，返回空pairs并解释原因；'
                           '不请求人工补选。若仍有疑虑，needs_review=true，系统将自动排除。'
                           '仅返回一组；span_start/span_end填null。'
                           '原识别说明：' + proposal['reason'] + '；时间和镜头：'
                           + json.dumps([{'time': round(f['seconds'], 3), 'shot': f['shot_id']} for f in choices]))
                verified = self._cached_request(project, 'verify', choices, context)
                group = verified_group(proposal, verified, choices)
                episode = {'index': index + 1, 'start_seconds': proposal['scene_start_seconds'],
                           'end_seconds': proposal['scene_end_seconds'],
                           'anchor_a': proposal['frame_a'], 'anchor_b': proposal['frame_b']}
                if group['needs_review']:
                    episode.update(status='excluded', reason=group.get('verification_reason') or '缺少合格的正反打')
                    excluded.append(episode)
                else:
                    episode.update(status='ready', group_id=group['id'])
                    groups.append(group)
                episodes.append(episode)
            # 无合格独立反打的片段不放入待确认，也不会要求用户手动筛选。
            project['dialogue_segments'] = episodes
            project['excluded_dialogues'] = excluded
            self.projects.save(project)
            prepared, sizes = 0, set()
            from PIL import Image
            for index, group in enumerate(groups):
                if progress:
                    progress(.94 + .05 * index / max(1, len(groups)), f'准备原尺寸截图 {index + 1}/{len(groups)}')
                for side in ('a', 'b'):
                    image_path = original_frame(project, group[f'frame_{side}'], self.config.get('ffmpeg'), self.config.get('ffprobe'))
                    with Image.open(image_path) as image:
                        image.load()
                        sizes.add(image.size)
                    prepared += 1
            project['groups'] = groups
            ready = len(groups)
            usage = [project.get('analysis_usage', {})[key] for key in project['analysis_used_keys']
                     if key in project.get('analysis_usage', {})]
            project['analysis_report'] = {
                'protocol_version': 5, 'sampling': 'complete', 'discovery_batches': len(batches), 'discovery_images': len(discovery),
                'dialogue_segments': len(episodes), 'excluded_segments': len(excluded),
                'candidate_images': len(frames), 'raw_proposals': len(project['analysis_proposals']),
                'ready_groups': ready, 'review_groups': 0, 'prepared_images': prepared,
                'image_sizes': sorted(sizes), 'codex_requests_started': project.get('analysis_requests_started', 0),
                'cache_hits_this_run': project.get('analysis_cache_hits', 0) - hits_before,
                'new_requests_this_run': project.get('analysis_requests_started', 0) - requests_before,
                'usage': {key: sum(u.get(key, 0) for u in usage) for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')},
                'usage_records': len(usage)}
            project.update(phase='review', error=None, analysis_elapsed_seconds=round(time.monotonic() - started, 3))
            project['analysis_progress'] = {'step': 'complete', 'completed': len(groups), 'total': len(groups)}
            self.projects.save(project)
            if progress:
                progress(1, f'拆解完成：{ready} 组可生成正反打，{len(excluded)} 段不合格已自动排除')
            return project
        except Exception as exc:
            project.update(phase='error', error=str(exc), error_code=getattr(exc, 'code', 'processing_error'))
            self.projects.save(project)
            raise

    def _cached_request(self, project, kind, frames, context):
        key = hashlib.sha256(json.dumps({'kind': kind, 'frames': [(f['id'], f['seconds']) for f in frames],
                                         'source': project['source_sha256'], 'context': context,
                                         'version': 3}, sort_keys=True).encode()).hexdigest()
        cache = project.setdefault('analysis_cache', {})
        used = project.setdefault('analysis_used_keys', [])
        if key not in used:
            used.append(key)
        if key in cache:
            try:
                if kind == 'dedupe':
                    validate_clusters(cache[key], len(frames) // 2)
                else:
                    validate_analysis(cache[key], len(frames))
            except ProviderError:
                project.setdefault('invalid_analysis_cache', {})[key] = cache.pop(key)
                self.projects.save(project)
            else:
                project['analysis_cache_hits'] = project.get('analysis_cache_hits', 0) + 1
                return cache[key]
        folder = Path(project['directory']) / f'{kind}-{uuid.uuid4().hex[:10]}'
        project['analysis_requests_started'] = project.get('analysis_requests_started', 0) + 1
        self.projects.save(project)
        images = [Path(project['scan_directory']) / f['file'] for f in frames]
        if kind == 'dedupe':
            result = validate_clusters(request_images(self.config['codex'], images, folder, context, CLUSTER_SCHEMA), len(frames) // 2)
        else:
            result = analyze_images(self.config['codex'], images, folder, context)
        cache[key] = result
        for line in (folder / 'events.jsonl').read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'turn.completed' and isinstance(event.get('usage'), dict):
                project.setdefault('analysis_usage', {})[key] = event['usage']
        self.projects.save(project)
        return result

    def _deduplicate(self, project, proposals, frames, progress):
        if len(proposals) < 2:
            return proposals
        known = {f['id']: f for f in frames}
        calls = 0

        def compare(window):
            nonlocal calls
            calls += 1
            if progress:
                progress(.7, f'合并同段重复对话，第 {calls} 组比较')
            chosen = [known[proposals[i][side]] for i in window for side in ('frame_a', 'frame_b')]
            descriptions = [{'group': n, 'images': [n * 2, n * 2 + 1], 'range': pair_span(proposals[i], frames),
                             'reason': proposals[i]['reason']} for n, i in enumerate(window)]
            context = ('去重任务：每组恰好两张图片，不生成新配对，只将同一连续对话、同两个真人的重复组归并。'
                       '不同人物组合、不同地点或时间段再次出现不能因服装相同就合并。短暂插入无关镜头仍在继续的对话可以合并。'
                       '不确定是否重复时分开保留。每个输入组必须且只属于一个cluster，members使用组索引，'
                       'representative从members选画面较好的一组。组信息：' + json.dumps(descriptions, ensure_ascii=False))
            return self._cached_request(project, 'dedupe', chosen, context)
        return consolidate_dialogues(proposals, frames, compare)

    def repair(self, identifier: str, group_id: str, side: str, rect: list[int], *, apply_all=False, progress=None) -> dict:
        if side not in ('a', 'b'):
            raise ValueError('图片位置无效')
        project = self.projects.get(identifier)
        group = next((g for g in project['groups'] if g['id'] == group_id), None)
        if group is None:
            raise ValueError('配组不存在')
        from PIL import Image
        anchor = original_frame(project, group[f'frame_{side}'], self.config['ffmpeg'], self.config['ffprobe'])
        with Image.open(anchor) as image:
            w, h = image.size
            rectangle_mask(image.size, tuple(rect))
        targets = [(g, s) for g in project['groups'] if g['selected'] for s in ('a', 'b')] if apply_all else [(group, side)]
        folder = Path(project['directory']) / f'repair-{uuid.uuid4().hex[:10]}'
        folder.mkdir()
        for index, (target, position) in enumerate(targets):
            if progress:
                progress(index / len(targets), f'本地修复 {index + 1}/{len(targets)}，CPU 处理中')
            original = original_frame(project, target[f'frame_{position}'], self.config['ffmpeg'], self.config['ffprobe'])
            with Image.open(original) as image:
                tw, th = image.size
            scaled = (round(rect[0] / w * tw), round(rect[1] / h * th), round(rect[2] / w * tw), round(rect[3] / h * th))
            mask = folder / f'{target["id"]}-{position}-mask.png'
            rectangle_mask((tw, th), scaled).save(mask)
            output = folder / f'{target["id"]}-{position}.png'
            report = repair_image(original, mask, output, Path(self.config['model']))
            (folder / f'{target["id"]}-{position}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            target[f'repaired_{position}'], target[f'mask_{position}'] = str(output), str(mask)
            self.projects.save(project)
        project['phase'] = 'review'
        self.projects.save(project)
        return project

    def export(self, identifier: str, destination: Path) -> Path:
        project = self.projects.get(identifier)
        result = export_groups(project, destination, self.config['ffmpeg'], self.config['ffprobe'])
        project['exports'].append(str(result))
        project['phase'] = 'exported'
        self.projects.save(project)
        return result
