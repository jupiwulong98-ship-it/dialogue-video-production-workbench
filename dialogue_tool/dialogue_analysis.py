"""有界的对话去重和逐组二次选图，不将整部长片图片塞入一次请求。"""

import uuid

from .providers import ProviderError


CLUSTER_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'clusters': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'properties': {'members': {'type': 'array', 'items': {'type': 'integer'}},
                       'representative': {'type': 'integer'}},
        'required': ['members', 'representative']}}},
    'required': ['clusters']}


def pair_span(pair, frames):
    known = {f['id']: f for f in frames}
    times = [known[pair[k]]['seconds'] for k in ('frame_a', 'frame_b')]
    return (min(pair.get('scene_start_seconds', min(times)), min(times)),
            max(pair.get('scene_end_seconds', max(times)), max(times)))


def temporal_components(pairs, frames, gap=45):
    """只限制需要比较的时间邻域，语义重复仍由模型判断。"""
    result, end = [], None
    for index in sorted(range(len(pairs)), key=lambda i: pair_span(pairs[i], frames)[0]):
        start, stop = pair_span(pairs[index], frames)
        if end is None or start > end + gap:
            result.append([])
            end = stop
        result[-1].append(index)
        end = max(end, stop)
    return result


def dedupe_windows(items, size=12, overlap=4):
    result, start = [], 0
    while start < len(items):
        result.append(items[start:start + size])
        if start + size >= len(items):
            break
        start += size - overlap
    return result


def validate_clusters(result, count):
    seen = []
    for cluster in result.get('clusters', []):
        members = cluster.get('members', [])
        if not members or cluster.get('representative') not in members:
            raise ProviderError('去重结果缺少有效代表组')
        if any(type(i) is not int or not 0 <= i < count for i in members):
            raise ProviderError('去重结果引用不存在的组')
        seen.extend(members)
    if sorted(seen) != list(range(count)):
        raise ProviderError('去重结果遗漏或重复分配了原始组')
    return result


def consolidate_dialogues(proposals, frames, compare):
    parent = list(range(len(proposals)))
    votes = [0] * len(proposals)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def representatives(indices):
        groups = {}
        for i in indices:
            groups.setdefault(find(i), []).append(i)
        return [max(members, key=lambda i: (votes[i], -i)) for members in groups.values()]

    for component in temporal_components(proposals, frames):
        processed = []
        for new in dedupe_windows(component, size=4, overlap=0):
            kept = representatives(processed)
            windows = [carry + new for carry in dedupe_windows(kept, size=8, overlap=0)] if kept else [new]
            for window in windows:
                if len(window) < 2:
                    continue
                result = validate_clusters(compare(window), len(window))
                for cluster in result['clusters']:
                    representative = window[cluster['representative']]
                    votes[representative] += 1
                    for member in cluster['members']:
                        parent[find(window[member])] = find(representative)
            processed.extend(new)
    merged = {}
    for i in range(len(proposals)):
        merged.setdefault(find(i), []).append(i)
    result = []
    for members in merged.values():
        representative = max(members, key=lambda i: (votes[i], -i))
        spans = [pair_span(proposals[i], frames) for i in members]
        result.append(dict(proposals[representative], scene_start_seconds=min(s[0] for s in spans),
                           scene_end_seconds=max(s[1] for s in spans), duplicate_count=len(members)))
    return sorted(result, key=lambda p: p['scene_start_seconds'])


def refinement_frames(pair, frames):
    known = {f['id']: f for f in frames}
    anchors = [known[pair[k]] for k in ('frame_a', 'frame_b')]
    result = list(anchors)
    used = {f['id'] for f in anchors}
    for anchor in anchors:
        candidates = [f for f in frames if f['shot_id'] == anchor['shot_id'] and f['id'] not in used]
        candidates.sort(key=lambda f: f.get('sharpness', 0), reverse=True)
        for frame in candidates[:5]:
            result.append(frame)
            used.add(frame['id'])
    return result[:12]


def verified_group(proposal, verification, choices):
    group = dict(proposal, id=uuid.uuid4().hex, prompt='', repaired_a=None, repaired_b=None)
    results = verification.get('pairs', [])
    uncertain = len(results) != 1
    if len(results) == 1:
        result = results[0]
        group.update(frame_a=choices[result['frame_a']]['id'], frame_b=choices[result['frame_b']]['id'])
        uncertain = bool(result['needs_review'])
        group['person_a'] = result.get('person_a', group.get('person_a', ''))
        group['person_b'] = result.get('person_b', group.get('person_b', ''))
        group['verification_reason'] = result['reason']
    else:
        group['verification_reason'] = verification['summary']
    known = {f['id']: f for f in choices}
    a, b = known[group['frame_a']], known[group['frame_b']]
    # 两张同镜头图可能是同一个人的连续动作，无法代表已确认的正反打。
    if a['shot_id'] == b['shot_id'] or abs(a['seconds'] - b['seconds']) > 90:
        uncertain = True
    group.update(needs_review=uncertain, selected=not uncertain,
                 validation_state='uncertain' if uncertain else 'ready')
    return group
