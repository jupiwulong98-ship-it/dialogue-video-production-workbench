from dialogue_tool import dialogue_analysis as da


def test_temporal_components_keep_later_reappearance_separate():
    frames = [{'id': i, 'seconds': t, 'shot_id': i} for i, t in enumerate([10, 12, 14, 16, 100, 104])]
    pairs = [dict(frame_a=0, frame_b=1), dict(frame_a=2, frame_b=3), dict(frame_a=4, frame_b=5)]
    assert [len(c) for c in da.temporal_components(pairs, frames)] == [2, 1]


def test_dedupe_windows_are_bounded_and_cover_every_proposal():
    windows = da.dedupe_windows(list(range(103)))
    assert all(len(w) <= 12 for w in windows)
    assert set(i for w in windows for i in w) == set(range(103))
    assert all(set(a) & set(b) for a, b in zip(windows, windows[1:]))


def test_dedupe_rejects_silent_omission_or_invented_members():
    import pytest
    from dialogue_tool.providers import ProviderError
    for clusters in [[{'members': [0], 'representative': 0}],
                     [{'members': [0, 2], 'representative': 0}],
                     [{'members': [0, 1], 'representative': 2}]]:
        with pytest.raises(ProviderError):
            da.validate_clusters({'clusters': clusters}, 2)


def test_refinement_keeps_anchor_identities_and_bounds_image_input():
    frames = [{'id': i, 'shot_id': i // 20, 'seconds': i / 2, 'sharpness': i} for i in range(60)]
    chosen = da.refinement_frames({'frame_a': 1, 'frame_b': 21}, frames)
    assert [f['id'] for f in chosen[:2]] == [1, 21]
    assert len(chosen) <= 12
    assert {f['shot_id'] for f in chosen} == {0, 1}


def test_rejected_verification_becomes_unselected_review_group():
    frames = [{'id': 3, 'seconds': 10, 'shot_id': 1}, {'id': 8, 'seconds': 15, 'shot_id': 2}]
    proposal = {'frame_a': 3, 'frame_b': 8, 'reason': '候选', 'needs_review': False}
    group = da.verified_group(proposal, {'summary': '两张是同一人，无法确认', 'pairs': []}, frames)
    assert group['needs_review'] is True
    assert group['selected'] is False
    assert group['frame_a'] == 3 and group['frame_b'] == 8
    assert '同一人' in group['verification_reason']


def test_distant_duplicate_in_a_component_is_compared_to_kept_representatives():
    frames = [{'id': i, 'seconds': i, 'shot_id': i} for i in range(26)]
    pairs = [dict(frame_a=i*2, frame_b=i*2+1, identity=0 if i==12 else i) for i in range(13)]
    requests=[]
    def compare(window):
        requests.append(window)
        clusters={}
        for local, original in enumerate(window):
            clusters.setdefault(pairs[original]['identity'],[]).append(local)
        return {'clusters':[{'members':members,'representative':members[0]} for members in clusters.values()]}
    result = da.consolidate_dialogues(pairs, frames, compare)
    assert len(result) == 12
    assert any(0 in w and 12 in w for w in requests)
    assert all(len(w)<=12 for w in requests)


def test_brief_intercut_does_not_split_one_continuing_dialogue():
    frames = [{'id': i, 'seconds': t, 'shot_id': i} for i, t in enumerate([310, 320, 350, 360, 365, 367])]
    pairs = [dict(frame_a=0, frame_b=1), dict(frame_a=2, frame_b=3), dict(frame_a=4, frame_b=5)]
    def compare(window):
        assert window == [0, 1, 2]
        return {'clusters':[{'members':[0,1],'representative':0},{'members':[2],'representative':2}]}
    result = da.consolidate_dialogues(pairs, frames, compare)
    assert len(result) == 2
    assert result[0]['scene_start_seconds'] == 310
    assert result[0]['scene_end_seconds'] == 360
