import copy
import json
import subprocess
from pathlib import Path

from jsonschema import ValidationError, validate


class ProviderError(RuntimeError):
    def __init__(self, message, code='provider_error'):
        super().__init__(message)
        self.code = code


def codex_failure(message, output, returncode):
    lowered = message.lower()
    if 'usage limit' in lowered or 'insufficient_quota' in lowered or 'quota exceeded' in lowered:
        return ProviderError('Codex 额度不足。已完成批次已保留；额度恢复后点击“继续拆解”，不会重做已完成步骤。'
                             f'详细日志：{output}', 'quota_exhausted')
    if 'rate limit' in lowered or '429' in lowered:
        return ProviderError(f'Codex 暂时限流。已完成批次已保留，请稍后继续拆解。详细日志：{output}', 'rate_limited')
    return ProviderError(f'Codex 分析失败，详见 {output}，退出码 {returncode}')


def run(args: list[str], timeout: int = 60, *, stdin: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([str(x) for x in args], input=stdin, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f'程序运行失败或超时：{Path(args[0]).name}：{exc}') from exc


def task_payload(text: str) -> dict:
    """CLI 可能先输出进度。只接受明确的任务字段，不将进程退出码当生成成功。"""
    decoder = json.JSONDecoder()
    documents = []
    pos = 0
    while pos < len(text):
        start = next((i for i in range(pos, len(text)) if text[i] in '{['), None)
        if start is None:
            break
        try:
            value, end = decoder.raw_decode(text, start)
            documents.append(value)
            pos = end
        except json.JSONDecodeError:
            pos = start + 1

    def find(value):
        if isinstance(value, dict):
            if isinstance(value.get('submit_id'), str) and value['submit_id'].strip():
                return value
            if value.get('gen_status') in ('querying', 'success', 'fail'):
                return value
            for nested in value.values():
                result = find(nested)
                if result:
                    return result
        elif isinstance(value, list):
            for nested in value:
                result = find(nested)
                if result:
                    return result
        return None

    for value in reversed(documents):
        result = find(value)
        if result:
            return result
    raise ProviderError('即梦未返回可确认的任务 JSON；请核对任务历史，不能据此重提。')


ANALYSIS_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'summary': {'type': 'string'},
        'pairs': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'frame_a': {'type': 'integer'}, 'frame_b': {'type': 'integer'},
                           'reason': {'type': 'string'}, 'needs_review': {'type': 'boolean'},
                           'span_start': {'type': ['integer', 'null']}, 'span_end': {'type': ['integer', 'null']},
                           'person_a': {'type': ['string', 'null']}, 'person_b': {'type': ['string', 'null']}},
            'required': ['frame_a', 'frame_b', 'reason', 'needs_review']}}},
    'required': ['summary', 'pairs']}


def validate_analysis(payload: dict, frame_count: int) -> dict:
    try:
        validate(payload, ANALYSIS_SCHEMA)
    except ValidationError as exc:
        raise ProviderError(f'Codex 输出格式不符：{exc.message}') from exc
    for pair in payload['pairs']:
        a, b = pair['frame_a'], pair['frame_b']
        if a == b or not (0 <= a < frame_count and 0 <= b < frame_count):
            raise ProviderError('Codex 配组引用了不存在或相同的图片，需重新检查。')
        if pair.get('span_start') is not None or pair.get('span_end') is not None:
            start, end = pair.get('span_start', -1), pair.get('span_end', -1)
            if type(start) is not int or type(end) is not int or not (0 <= start <= min(a, b) <= max(a, b) <= end < frame_count):
                raise ProviderError('Codex 对话范围未包含所选画面')
    return payload


def cli_schema(definition):
    """Codex 严格输出要求所有键必填，可选信息用 null 保留兼容。"""
    result = copy.deepcopy(definition)

    def visit(node):
        if node.get('type') == 'object':
            required = set(node.get('required', []))
            for key, child in node.get('properties', {}).items():
                if key not in required:
                    child['type'] = list(dict.fromkeys((child['type'] if isinstance(child['type'], list) else [child['type']]) + ['null']))
                visit(child)
            node['required'] = list(node.get('properties', {}))
        if 'items' in node:
            visit(node['items'])
    visit(result)
    return result


def request_images(codex: str, images: list[Path], output: Path, instruction: str, schema_definition: dict) -> dict:
    if len(images) < 2:
        raise ValueError('至少需要两张候选图')
    output.mkdir(parents=True, exist_ok=False)
    for path in images:
        if not path.is_file():
            raise ValueError(f'图片不存在：{path}')
    schema = output / 'schema.json'
    schema.write_text(json.dumps(cli_schema(schema_definition)), encoding='utf-8')
    final = output / 'analysis.json'
    instruction = '只观察所附图片，不调用工具、不读其他文件。附件从0编号。只输出满足schema的JSON。' + instruction
    args = [codex, 'exec', '--ignore-user-config', '--skip-git-repo-check', '--ephemeral',
            '--sandbox', 'read-only', '--json', '--output-schema', str(schema),
            '--output-last-message', str(final)]
    for path in images:
        args += ['--image', str(path.resolve())]
    # 从 stdin 传递提示词，避免图片参数消耗末尾的位置参数。
    args += ['-']
    result = run(args, 600, stdin=instruction)
    (output / 'events.jsonl').write_text(result.stdout, encoding='utf-8')
    (output / 'stderr.txt').write_text(result.stderr, encoding='utf-8')
    if result.returncode or not final.is_file():
        raise codex_failure(result.stdout + result.stderr, output, result.returncode)
    try:
        payload = json.loads(final.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise ProviderError('Codex 未产生有效分析 JSON') from exc
    try:
        validate(payload, schema_definition)
    except ValidationError as exc:
        raise ProviderError(f'Codex 输出格式不符：{exc.message}') from exc
    return payload


def analyze_images(codex: str, images: list[Path], output: Path, context: str = '') -> dict:
    instruction = (
        '找出同一连续场景中两个不同真人的正反打对话候选，每人选一张清晰图。'
        '不是同一人的两个角度，也不是两张多人合照。需要相反视角和交流关系的画面证据，不能因为衣服相似就配对。'
        '不要将机器人、屏幕影像、道具或旁观者当对话人物。缺少另一人独立视角不要假装成功，可标needs_review=true。'
        '同段同两人仅一组；不同地点、不同时间段重新出现应另建组。短暂插入其他镜头不必切碎仍在继续的对话。'
        '选脸部清晰、无遮挡、眼睛睁开、表情自然的中近景，避开闭眼、背影、过渡、严重运动模糊及大字遮脸。'
        'span_start/span_end填写本段对话在当前附件中的首末索引，包含所选两张；person_a/person_b写简短可辨识外貌和服装。'
        'summary与reason用中文，区分明确配对和不确定候选；没有合适配组可返回空pairs。上下文：' + context)
    return validate_analysis(request_images(codex, images, output, instruction, ANALYSIS_SCHEMA), len(images))
