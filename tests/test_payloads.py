import pytest


def test_provider_json_with_progress_and_nested_data():
    from dialogue_tool.providers import task_payload
    result = task_payload('正在上传\n{"data":{"submit_id":"paid-1","gen_status":"querying"}}\n')
    assert result['submit_id'] == 'paid-1'
    assert result['gen_status'] == 'querying'


def test_zero_exit_or_unrecognized_response_is_not_task_success():
    from dialogue_tool.providers import task_payload, ProviderError
    with pytest.raises(ProviderError):
        task_payload('Task sent successfully')
    with pytest.raises(ProviderError):
        task_payload('{"status":"success"}')


def test_pair_analysis_rejects_nonexistent_frame_references():
    from dialogue_tool.providers import validate_analysis, ProviderError
    payload = {'summary': '两人对话', 'pairs': [{'frame_a': 0, 'frame_b': 7, 'reason': 'same scene', 'needs_review': False}]}
    with pytest.raises(ProviderError):
        validate_analysis(payload, 2)


def test_pair_analysis_never_allows_same_image_as_both_people():
    from dialogue_tool.providers import validate_analysis, ProviderError
    payload = {'summary': '两人对话', 'pairs': [{'frame_a': 0, 'frame_b': 0, 'reason': 'same scene', 'needs_review': False}]}
    with pytest.raises(ProviderError):
        validate_analysis(payload, 2)


def test_cli_schema_requires_all_keys_but_allows_optional_nulls():
    from dialogue_tool.providers import cli_schema, ANALYSIS_SCHEMA
    schema = cli_schema(ANALYSIS_SCHEMA)
    item = schema['properties']['pairs']['items']
    assert set(item['required']) == set(item['properties'])
    assert 'null' in item['properties']['span_start']['type']
    assert 'span_start' not in ANALYSIS_SCHEMA['properties']['pairs']['items']['required']


def test_usage_limit_error_is_actionable_and_not_misreported_as_bad_images():
    from dialogue_tool.providers import codex_failure
    error = codex_failure('You’ve hit your usage limit. Try again later.', 'logs', 1)
    assert error.code == 'quota_exhausted'
    assert '额度' in str(error)
    assert '继续拆解' in str(error)
