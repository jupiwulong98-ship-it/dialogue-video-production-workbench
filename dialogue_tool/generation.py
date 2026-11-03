"""Official Dreamina Canvas CLI integration: two character references, not first/last frames.

No browser credentials, API keys, credit tokens, or signed URLs are persisted.
Calls are argv-based and require a locally authenticated dreamina-canvas executable.
"""
import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from .projects import original_frame, project_asset


class CanvasError(RuntimeError):
    pass


def _data(envelope):
    if not isinstance(envelope, dict):
        raise CanvasError("即梦 CLI 未返回有效 JSON")
    if envelope.get("error"):
        err = envelope["error"]
        code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
        action = err.get("requiredAction", "") if isinstance(err, dict) else ""
        raise CanvasError(f"即梦接口错误：{code}，后续操作：{action}")
    return envelope.get("data", envelope)


def invoke(cli, args, timeout=120):
    """No shell, no credential logging. An uncertain write must not be reissued blindly."""
    if not cli:
        raise CanvasError("未安装官方 dreamina-canvas CLI。先在客户 Mac 安装并登录。")
    cmd = [str(cli), "--format", "json", "--non-interactive", *map(str, args)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CanvasError("即梦命令未确认成功；若是写操作，请先核查已有任务，不要直接重提") from exc
    try:
        envelope = json.loads(done.stdout.strip())
    except ValueError as exc:
        raise CanvasError(f"即梦返回格式不可解析（退出码 {done.returncode}）；不要直接重提交") from exc
    # Exit 20 means recover by original identity, never assume a paid request failed.
    if done.returncode not in (0, 20):
        err = envelope.get("error") if isinstance(envelope, dict) else None
        code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
        action = err.get("requiredAction", "") if isinstance(err, dict) else ""
        raise CanvasError(f"即梦 CLI 退出 {done.returncode}：{code}，后续操作 {action}")
    return _data(envelope)


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def _uuid():
    return str(uuid.uuid4())


def _result_resource(value):
    """Only resource identities; never save provider URLs or tokens."""
    if isinstance(value, dict):
        for key in ("resourceId", "resultResourceId", "outputResourceId"):
            item = value.get(key)
            if isinstance(item, str) and len(item) == 36:
                return item
        for key in ("resources", "outputs", "result", "items", "assets"):
            if key in value:
                found = _result_resource(value[key])
                if found:
                    return found
    elif isinstance(value, list):
        for item in value:
            found = _result_resource(item)
            if found:
                return found
    return None


class CanvasWorkflow:
    def __init__(self, service):
        self.service = service

    @property
    def cli(self):
        return self.service.config.get("dreamina_canvas")

    def _load(self, identifier):
        return self.service.projects.get(identifier)

    def _save(self, project):
        self.service.projects.save(project)

    def configure(self, identifier, model, duration, global_prompt):
        project = self._load(identifier)
        settings = project.setdefault("generation", {})
        if any(task.get("submit_id") for task in settings.get("tasks", {}).values()):
            # Updating generation configuration after running would invalidate paid task identity.
            raise CanvasError("已有提交任务。请在新项目配置新模型或提示词。")
        model = str(model).strip()
        if not model:
            raise CanvasError("请先通过 CLI 查询当前可用视频模型，并填写其准确 model 标识")
        duration = int(duration)
        if not 1 <= duration <= 60:
            raise CanvasError("时长须为 1–60 秒且必须符合所选模型的实际约束")
        incoming_prompt = str(global_prompt).strip()
        if settings.get("tasks") and (settings.get("model") != model or
                settings.get("duration") != duration or settings.get("global_prompt") != incoming_prompt):
            raise CanvasError("已有即梦草稿，不能悄悄更改模型、时长或全局提示词。请新建项目再调整。")
        settings.update(model=model, duration=duration, global_prompt=incoming_prompt,
                        mode="m2v")
        settings.pop("quote", None)
        self._save(project)
        return project

    def models(self):
        return invoke(self.cli, ["model", "list", "--type", "video"])

    def prepare(self, identifier):
        """Create non-billable draft nodes. Network calls may write canvas metadata."""
        project = self._load(identifier)
        config = project.get("generation", {})
        if not config.get("model"):
            raise CanvasError("先配置视频模型和提示词")
        selected = [g for g in project["groups"] if g.get("selected") and not g.get("needs_review")]
        if not selected:
            raise CanvasError("至少选择一组已通过自动复核的 A/B 截图")
        if any(task.get("submit_id") for task in config.get("tasks", {}).values()):
            raise CanvasError("项目已有执行任务；请先查询结果，不要重复创建新的付费任务")
        if not config.get("project_id"):
            config["project_id"] = _uuid()
            self._save(project)
        canvas_id = config["project_id"]
        # Repeated create with the same project-id is idempotent.
        actual = invoke(self.cli, ["canvas", "create", project["name"][:45], "--project-id", canvas_id])
        if isinstance(actual, dict) and actual.get("projectId") not in (None, canvas_id):
            raise CanvasError("画布返回的 projectId 与本地登记不一致")
        tasks = config.setdefault("tasks", {})
        for group in selected:
            gid = group["id"]
            prompt = "\n".join(x for x in (config.get("global_prompt", ""), group.get("prompt", "").strip()) if x).strip()
            if not prompt:
                raise CanvasError("存在没有提示词的组，请填写全局提示词或单组提示词")
            paths = []
            for side in ("a", "b"):
                repaired = group.get(f"repaired_{side}")
                paths.append(project_asset(project, repaired) if repaired else
                             original_frame(project, group[f"frame_{side}"],
                                            self.service.config["ffmpeg"], self.service.config["ffprobe"]))
            hashes = [_sha(path) for path in paths]
            fingerprint = hashlib.sha256(json.dumps({
                "prompt": prompt, "hashes": hashes, "model": config["model"],
                "duration": config["duration"], "mode": "m2v"}, sort_keys=True).encode()).hexdigest()
            task = tasks.setdefault(gid, {})
            if task.get("status") == "node_create_unknown":
                raise CanvasError("节点创建结果不确定；请先在即梦画布核对，不自动创建第二个节点")
            if task.get("node_id"):
                if task.get("fingerprint") != fingerprint:
                    raise CanvasError(f"图片组 {gid} 的提示词或素材发生变化；已有草稿不能静默复用，请新建项目")
                continue
            task.update(group_id=gid, fingerprint=fingerprint, prompt=prompt, images=hashes,
                        status="preparing", resource_ids=task.get("resource_ids") or [_uuid(), _uuid()])
            self._save(project)  # Identities must be persisted BEFORE uploads.
            for path, rid in zip(paths, task["resource_ids"]):
                response = invoke(self.cli, ["resource", "upload", "--file", str(path),
                                             "--project-id", canvas_id, "--resource-id", rid,
                                             "--import-kind", "local_upload"], timeout=180)
                if isinstance(response, dict) and response.get("resourceId") not in (None, rid):
                    raise CanvasError("资源上传返回的 identity 与预留 ID 不同")
            # Node create has no caller-provided idempotency key: mark unknown before the write.
            task["status"] = "node_create_unknown"
            self._save(project)
            data = invoke(self.cli, ["node", "create", "video", "--project-id", canvas_id,
                                     "--mode", "m2v", "--model", config["model"],
                                     "--duration", config["duration"], "--prompt", prompt,
                                     "--ref", "res:" + task["resource_ids"][0],
                                     "--ref", "res:" + task["resource_ids"][1]], timeout=180)
            node_id = data.get("nodeId") if isinstance(data, dict) else None
            if not isinstance(node_id, str) or not node_id.startswith("node_"):
                raise CanvasError("即梦未返回 nodeId，草稿创建结果不确定；禁止盲目重提")
            task.update(node_id=node_id, status="draft")
            self._save(project)
        config.pop("quote", None)
        self._save(project)
        return project

    def quote(self, identifier):
        project = self._load(identifier)
        config = project.get("generation", {})
        tasks = list(config.get("tasks", {}).values())
        if not tasks or any(x.get("status") != "draft" for x in tasks):
            raise CanvasError("请先准备好所有未付费的视频草稿")
        args = ["node", "quote", "--project-id", config["project_id"]]
        for t in tasks:
            args += ["--node-id", t["node_id"]]
        q = invoke(self.cli, args)
        if not isinstance(q, dict) or q.get("confirmable") is not True or not isinstance(q.get("totalMaxCredits"), (int, float)):
            raise CanvasError("即梦未返回可确认的积分上限，禁止提交")
        if q["totalMaxCredits"] < 0:
            raise CanvasError("报价异常")
        config["quote"] = {"ceiling": q["totalMaxCredits"],
                           "group_ids": [t["group_id"] for t in tasks],
                           "node_ids": [t["node_id"] for t in tasks]}
        self._save(project)
        return project

    def run_approved(self, identifier, approved_ceiling):
        """Only call after an explicit UI approval with displayed amount."""
        project = self._load(identifier)
        config = project.get("generation", {})
        q = config.get("quote") or {}
        if not q or approved_ceiling != q.get("ceiling"):
            raise CanvasError("授权报价不匹配；必须重新查看积分报价")
        tasks = [config["tasks"][gid] for gid in q["group_ids"]]
        selected = {g["id"]: g for g in project["groups"] if g.get("selected") and not g.get("needs_review")}
        if set(selected) != set(q["group_ids"]):
            raise CanvasError("选中的图片组已改变，请重新准备并报价")
        for task in tasks:
            g = selected[task["group_id"]]
            current_prompt = "\n".join(x for x in (config.get("global_prompt", ""), g.get("prompt", "").strip()) if x).strip()
            if current_prompt != task["prompt"]:
                raise CanvasError("图片组提示词已变化，拒绝使用旧草稿扣费")
            files = [project_asset(project, g[f"repaired_{side}"]) if g.get(f"repaired_{side}") else
                     original_frame(project, g[f"frame_{side}"], self.service.config["ffmpeg"],
                                    self.service.config["ffprobe"]) for side in ("a", "b")]
            if [_sha(file) for file in files] != task["images"]:
                raise CanvasError("图片组图片已变化，拒绝使用旧草稿扣费")
        if any(t.get("status") != "draft" or t.get("submit_id") for t in tasks):
            raise CanvasError("任务已提交或状态改变，不能重复扣费")
        # Second quote immediately before approval, bounded by the amount shown in UI.
        ids = q["node_ids"]
        args = ["node", "quote", "--project-id", config["project_id"]]
        for nid in ids:
            args += ["--node-id", nid]
        refreshed = invoke(self.cli, args)
        if (not isinstance(refreshed, dict) or refreshed.get("confirmable") is not True or
                not isinstance(refreshed.get("totalMaxCredits"), (int, float)) or
                refreshed["totalMaxCredits"] > approved_ceiling):
            raise CanvasError("最新报价超出用户批准的积分上限，请重新确认")
        confirm = ["node", "confirm", "--project-id", config["project_id"],
                   "--credit-ceiling", approved_ceiling]
        for nid in ids:
            confirm += ["--node-id", nid]
        token_data = invoke(self.cli, confirm)
        token = token_data.get("creditConfirmationToken") if isinstance(token_data, dict) else None
        if not token:
            raise CanvasError("即梦未签发积分确认凭证")
        for t in tasks:
            t["submit_id"] = _uuid()
            t["status"] = "submission_unknown"
        config.pop("quote", None)
        self._save(project)  # Durable identity BEFORE any paid operation.
        command = ["node", "run", "--project-id", config["project_id"], "--credit-token", token]
        for nid in ids:
            command += ["--node-id", nid]
        for t in tasks:
            command += ["--submit-id", t["submit_id"]]
        try:
            invoke(self.cli, command, timeout=180)
        except CanvasError:
            # Unknown even if CLI exits nonzero. Reconcile by original submit_ids.
            self._save(project)
            raise
        for t in tasks:
            t["status"] = "submitted"
        self._save(project)
        return project

    def refresh(self, identifier):
        project = self._load(identifier)
        cfg = project.get("generation", {})
        for t in cfg.get("tasks", {}).values():
            if not t.get("submit_id") or t.get("status") == "downloaded":
                continue
            data = invoke(self.cli, ["operation", "status", t["submit_id"],
                                     "--project-id", cfg["project_id"]])
            if not isinstance(data, dict):
                continue
            submission = data.get("submission")
            state = submission.get("state") if isinstance(submission, dict) else None
            if state == "absent":
                t["status"] = "requires_manual_review"
            elif state == "completed":
                rid = _result_resource(data)
                if rid:
                    t["resource_id"] = rid
                    t["status"] = "completed"
                else:
                    t["status"] = "completed_no_resource"
            else:
                t["status"] = "running"
            self._save(project)
        return project

    def download(self, identifier):
        project = self._load(identifier)
        cfg = project.get("generation", {})
        for task in cfg.get("tasks", {}).values():
            if task.get("status") != "completed" or not task.get("resource_id"):
                continue
            rid = task["resource_id"]
            invoke(self.cli, ["resource", "get", rid, "--project-id", cfg["project_id"]])
            folder = Path(project["directory"]) / "generated-videos" / task["group_id"]
            folder.mkdir(parents=True, exist_ok=True)
            response = invoke(self.cli, ["resource", "download", rid,
                                         "--project-id", cfg["project_id"], "--output", str(folder)],
                              timeout=240)
            if not isinstance(response, dict):
                raise CanvasError("下载未返回校验结果")
            local = response.get("path") or response.get("localPath") or response.get("outputPath")
            if not isinstance(local, str):
                raise CanvasError("下载响应缺少本地文件路径")
            file = Path(local).resolve()
            if not file.is_file() or not file.is_relative_to(folder.resolve()):
                raise CanvasError("即梦下载路径不在项目输出目录内")
            count, checksum = response.get("byteCount"), response.get("sha256")
            if (not isinstance(count, int) or count <= 0 or
                    file.stat().st_size != count or not isinstance(checksum, str) or _sha(file) != checksum):
                raise CanvasError("即梦下载的文件大小或 SHA256 校验失败")
            task.update(status="downloaded", output=str(file), sha256=checksum)
            self._save(project)
        return project
