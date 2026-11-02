import pytest


def test_uncertain_submission_survives_restart_and_cannot_be_resubmitted(tmp_path):
    from dialogue_tool.store import JobStore, DuplicateJob
    path = tmp_path / '任务.sqlite'
    with JobStore(path) as store:
        job = store.reserve('same-input', {'prompt': '中文'})
        store.set_state(job['id'], 'submitting')
    with JobStore(path) as store:
        store.recover_interrupted()
        assert store.get(job['id'])['state'] == 'submission_unknown'
        with pytest.raises(DuplicateJob):
            store.reserve('same-input', {'prompt': '中文'})


def test_accepted_task_query_failure_keeps_submit_id(tmp_path):
    from dialogue_tool.store import JobStore
    with JobStore(tmp_path / 'jobs.sqlite') as store:
        job = store.reserve('key', {})
        store.accept(job['id'], 'server-42')
        store.note_error(job['id'], 'network timeout')
        row = store.get(job['id'])
        assert row['submit_id'] == 'server-42'
        assert row['state'] == 'querying'
        assert row['error'] == 'network timeout'


def test_duplicate_reservation_is_atomic_across_connections(tmp_path):
    from dialogue_tool.store import JobStore, DuplicateJob
    with JobStore(tmp_path / 'jobs.sqlite') as first, JobStore(tmp_path / 'jobs.sqlite') as second:
        first.reserve('once', {})
        with pytest.raises(DuplicateJob):
            second.reserve('once', {})
