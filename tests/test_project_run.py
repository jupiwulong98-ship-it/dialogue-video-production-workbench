import pytest


def test_analysis_lock_is_shared_between_instances_and_recovers_after_exit(tmp_path):
    from dialogue_tool.project_run import analysis_lock, analysis_running, AnalysisBusy
    assert not analysis_running(tmp_path)
    with analysis_lock(tmp_path):
        assert analysis_running(tmp_path)
        with pytest.raises(AnalysisBusy):
            with analysis_lock(tmp_path):
                pass
    assert not analysis_running(tmp_path)
    with pytest.raises(RuntimeError):
        with analysis_lock(tmp_path):
            raise RuntimeError('interruption')
    assert not analysis_running(tmp_path)
