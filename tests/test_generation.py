"""Offline contract checks for official Dreamina Canvas integration.
No login, network calls or paid generation.
"""
import hashlib
import json
from pathlib import Path

import pytest

from dialogue_tool import generation
from dialogue_tool.generation import CanvasError, CanvasWorkflow
from dialogue_tool.service import Service


@pytest.fixture
def context(tmp_path, monkeypatch):
    file_a, file_b = tmp_path / "a.png", tmp_path / "b.png"
    file_a.write_bytes(b"PNG A")
    file_b.write_bytes(b"PNG B")
    service = Service({"data": str(tmp_path / "db"), "dreamina_canvas": "fake-canvas",
                       "ffmpeg": "unused", "ffprobe": "unused"})
    source = tmp_path / "source.mp4"
    source.write_bytes(b"test")
    project = service.projects.create(source)
    project["groups"] = [{"id": "group-1", "frame_a": 1, "frame_b": 2, "selected": True,
                          "needs_review": False, "prompt": "人物自然交流"}]
    service.projects.save(project)
    monkeypatch.setattr(generation, "original_frame",
                        lambda project, frame_id, *unused: file_a if frame_id == 1 else file_b)
    calls = []
    output_rid = "c61a296f-68cd-48f7-9bb8-0a89c83d0011"

    def fake(cli, args, timeout=120):
        calls.append(list(args))
        if args[:2] == ["canvas", "create"]:
            return {"projectId": args[args.index("--project-id") + 1]}
        if args[:2] == ["resource", "upload"]:
            return {"resourceId": args[args.index("--resource-id") + 1]}
        if args[:3] == ["node", "create", "video"]:
            assert args.count("--ref") == 2
            assert args[args.index("--mode") + 1] == "m2v"  # A/B are NOT first and last frames.
            return {"nodeId": "node_localtest"}
        if args[:2] == ["node", "quote"]:
            return {"confirmable": True, "totalMaxCredits": 12}
        if args[:2] == ["node", "confirm"]:
            assert args[args.index("--credit-ceiling") + 1] == 12
            return {"creditConfirmationToken": "do-not-persist"}
        if args[:2] == ["node", "run"]:
            assert "--credit-token" in args
            assert args.count("--submit-id") == args.count("--node-id")
            return {"items": [{"state": "ACCEPTED"}]}
        if args[:2] == ["operation", "status"]:
            return {"submission": {"state": "completed"}, "resources": [{"resourceId": output_rid}]}
        if args[:2] == ["resource", "get"]:
            return {"resourceId": output_rid}
        if args[:2] == ["resource", "download"]:
            folder = Path(args[args.index("--output") + 1])
            target = folder / "test.mp4"
            target.write_bytes(b"fake mp4 result")
            return {"path": str(target), "byteCount": target.stat().st_size,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        raise AssertionError(args)

    monkeypatch.setattr(generation, "invoke", fake)
    return CanvasWorkflow(service), project["id"], calls


def test_prepare_quote_approve_status_download(context):
    flow, project_id, calls = context
    flow.configure(project_id, "video-model-returned-by-discovery", 5, "保留人物一致性")
    p = flow.prepare(project_id)
    assert p["generation"]["tasks"]["group-1"]["status"] == "draft"
    assert not any(args[:2] == ["node", "run"] for args in calls)
    q = flow.quote(project_id)
    assert q["generation"]["quote"]["ceiling"] == 12
    with pytest.raises(CanvasError, match="报价不匹配"):
        flow.run_approved(project_id, 999)  # Cannot spend beyond explicit UI consent.
    p = flow.run_approved(project_id, 12)
    task = p["generation"]["tasks"]["group-1"]
    submit_id = task["submit_id"]
    assert submit_id
    assert "do-not-persist" not in json.dumps(p)
    with pytest.raises(CanvasError, match="授权报价不匹配"):
        flow.run_approved(project_id, 12)
    p = flow.refresh(project_id)
    assert p["generation"]["tasks"]["group-1"]["status"] == "completed"
    assert p["generation"]["tasks"]["group-1"]["submit_id"] == submit_id
    p = flow.download(project_id)
    assert p["generation"]["tasks"]["group-1"]["status"] == "downloaded"
    assert Path(p["generation"]["tasks"]["group-1"]["output"]).is_file()
    assert len([x for x in calls if x[:2] == ["node", "run"]]) == 1


def test_changed_prompt_rejects_paid_submission(context):
    flow, project_id, calls = context
    flow.configure(project_id, "model", 5, "全局")
    flow.prepare(project_id)
    flow.quote(project_id)
    p = flow.service.projects.get(project_id)
    p["groups"][0]["prompt"] = "已修改的新提示词"
    flow.service.projects.save(p)
    with pytest.raises(CanvasError, match="提示词已变化"):
        flow.run_approved(project_id, 12)
    assert not any(x[:2] == ["node", "run"] for x in calls)


def test_node_create_ambiguous_never_repeats(context, monkeypatch):
    flow, project_id, calls = context
    flow.configure(project_id, "model", 5, "全局")
    original = generation.invoke

    def lost(cli, args, timeout=120):
        if args[:3] == ["node", "create", "video"]:
            calls.append(list(args))
            raise CanvasError("模拟创建成功但响应丢失")
        return original(cli, args, timeout)

    monkeypatch.setattr(generation, "invoke", lost)
    with pytest.raises(CanvasError, match="响应丢失"):
        flow.prepare(project_id)
    with pytest.raises(CanvasError, match="不确定"):
        flow.prepare(project_id)
    assert len([x for x in calls if x[:3] == ["node", "create", "video"]]) == 1


def test_cli_argv_json_no_shell(monkeypatch):
    class Done:
        returncode = 0
        stdout = '{"data":{"nodeId":"node_x"}}'
    received = {}
    def fake_run(args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs
        return Done()
    monkeypatch.setattr(generation.subprocess, "run", fake_run)
    assert generation.invoke("/tmp/dreamina-canvas", ["node", "show", "quotes; rm -rf /"]) == {"nodeId": "node_x"}
    assert received["kwargs"]["capture_output"] is True
    assert received["args"][-1] == "quotes; rm -rf /"
    assert "shell" not in received["kwargs"]


def test_download_checksum_mismatch_is_not_marked_success(context, monkeypatch):
    flow, project_id, calls = context
    flow.configure(project_id, "model", 5, "全局")
    flow.prepare(project_id)
    flow.quote(project_id)
    flow.run_approved(project_id, 12)
    flow.refresh(project_id)
    original = generation.invoke
    def wrong(cli, args, timeout=120):
        result = original(cli, args, timeout)
        if args[:2] == ["resource", "download"]:
            result["sha256"] = "0" * 64
        return result
    monkeypatch.setattr(generation, "invoke", wrong)
    with pytest.raises(CanvasError, match="校验失败"):
        flow.download(project_id)
    assert flow.service.projects.get(project_id)["generation"]["tasks"]["group-1"]["status"] == "completed"
