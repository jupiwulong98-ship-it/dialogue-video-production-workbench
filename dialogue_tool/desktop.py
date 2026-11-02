"""只暴露项目内图片和固定操作，窗口不加载远程页面。"""

import base64
import io
import os
import threading
from pathlib import Path

from PIL import Image

from .media import probe
from .projects import original_frame, project_asset
from .providers import run
from .service import Service
from .generation import CanvasWorkflow
from .settings import ROOT, settings
from .project_run import analysis_running


class DesktopAPI:
    def __init__(self, service: Service):
        self._service = service
        self._generation = CanvasWorkflow(service)
        self._window = None
        self.lock = threading.RLock()
        items = service.projects.list()
        self.current = items[0]['id'] if items else None
        self.version = 1
        self.busy = False
        self.message = '准备就绪'
        self.progress = 0
        self.error = None

    def get_state(self):
        with self.lock:
            project = self._service.projects.get(self.current) if self.current else None
            external = bool(project and not self.busy and analysis_running(Path(project['directory'])))
            status = project.get('run_status', {}) if external else {}
            version = f'{self.version}:{self.current}:{project.get("phase") if project else ""}:{len(project["groups"]) if project else 0}'
            return {'version': version, 'busy': self.busy or external,
                    'message': status.get('message', '此项目正在后台拆解') if external else self.message,
                    'progress': status.get('progress', 0) if external else self.progress,
                    'error': self.error or (project.get('error') if project else None),
                    'project': project,
                    'projects': [{'id': p['id'], 'name': p['name'], 'phase': p['phase']} for p in self._service.projects.list()]}

    def _idle(self):
        project = self._service.projects.get(self.current) if self.current else None
        if self.busy or (project and analysis_running(Path(project['directory']))):
            raise ValueError('当前任务尚未结束，请稍候')

    def _tick(self, progress, message):
        with self.lock:
            self.progress, self.message = progress, message

    def _start(self, target):
        with self.lock:
            self._idle()
            self.busy, self.error, self.progress = True, None, 0
            self.message = '正在准备'

        def work():
            try:
                message = target()
                self._tick(1, message or '完成，请检查结果')
            except Exception as exc:
                with self.lock:
                    self.error, self.message = str(exc), '任务未完成，请查看原因'
            finally:
                with self.lock:
                    self.busy = False
                    self.version += 1
        threading.Thread(target=work, daemon=True).start()
        return True

    def choose_video(self):
        import webview
        with self.lock:
            self._idle()
            chosen = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                                   file_types=('视频 (*.mp4;*.mov;*.mkv;*.webm;*.m4v;*.avi)', '所有文件 (*.*)'))
            if not chosen:
                return False
            source = Path(chosen[0])
            probe(source, self._service.config['ffprobe'])
            project = self._service.projects.create(source)
            self.current, self.error = project['id'], None
            self.version += 1
            return True

    def select_project(self, identifier):
        with self.lock:
            self._idle()
            self._service.projects.get(identifier)
            self.current, self.error = identifier, None
            self.version += 1

    def start_analysis(self, start, end, scan_only=False):
        identifier = self.current
        if not identifier:
            raise ValueError('请先选择视频')
        start = float(start or 0)
        end = float(end) if end not in ('', None) else None

        def task():
            project = self._service.analyze(identifier, start, end, scan_only=bool(scan_only), progress=self._tick)
            ready = sum(not g['needs_review'] for g in project['groups'])
            return f'已提取 {len(project["scan"]["frames"])} 张候选图' if scan_only else f'拆解完成：{ready} 组对话结果，{len(project["groups"]) - ready} 组待确认'
        return self._start(task)

    def save_groups(self, groups, project_id):
        with self.lock:
            self._idle()
            if project_id != self.current:
                raise ValueError('项目已切换，请重新载入后保存')
            self._service.projects.update_groups(self.current, groups)
            self.version += 1
            return True

    def get_image(self, frame_id, group_id=None, side=None, original=False):
        if not self.current:
            raise ValueError('没有当前项目')
        project = self._service.projects.get(self.current)
        if original:
            path = original_frame(project, int(frame_id), self._service.config['ffmpeg'], self._service.config['ffprobe'])
        elif group_id:
            group = next(g for g in project['groups'] if g['id'] == group_id)
            if side not in ('a', 'b') or group[f'frame_{side}'] != frame_id:
                raise ValueError('图片与配组不对应')
            repaired = group.get(f'repaired_{side}')
            if repaired:
                path = project_asset(project, repaired)
            else:
                path = original_frame(project, int(frame_id), self._service.config['ffmpeg'], self._service.config['ffprobe'])
        else:
            frame = next(f for f in project['scan']['frames'] if f['id'] == frame_id)
            path = project_asset(project, str(Path(project['scan_directory']) / frame['file']))
        with Image.open(path) as source:
            image = source.convert('RGB')
            width, height = image.size
            image.thumbnail((1200, 900) if original else (700, 500))
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=90)
        return {'url': 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'),
                'width': width, 'height': height}

    def start_repair(self, group_id, side, rect, apply_all, expected_frame, project_id):
        with self.lock:
            self._idle()
            identifier = self.current
            if not identifier or project_id != identifier:
                raise ValueError('项目已切换，请重新框选')
            project = self._service.projects.get(identifier)
            group = next((g for g in project['groups'] if g['id'] == group_id), None)
            if side not in ('a', 'b') or not group or group[f'frame_{side}'] != expected_frame:
                raise ValueError('截图已变化，请重新框选')
            return self._start(lambda: (self._service.repair(identifier, group_id, side, tuple(int(v) for v in rect),
                                                           apply_all=bool(apply_all), progress=self._tick) and '修复完成，请对比检查'))

    def choose_export(self):
        import webview
        with self.lock:
            self._idle()
            chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not chosen:
                return False
            identifier, destination = self.current, Path(chosen[0])
        return self._start(lambda: '图片已导出到：' + str(self._service.export(identifier, destination)))

    def generation_models(self):
        return self._generation.models()

    def generation_configure(self, model, duration, prompt, project_id):
        with self.lock:
            self._idle()
            if not project_id or self.current != project_id:
                raise ValueError("项目已经切换")
            result = self._generation.configure(project_id, model, duration, prompt)
            self.version += 1
            return {"model": result["generation"]["model"], "duration": result["generation"]["duration"]}

    def generation_prepare(self):
        identifier = self.current
        return self._start(lambda: (self._generation.prepare(identifier) and "生成草稿已准备好，尚未扣费"))

    def generation_quote(self):
        identifier = self.current
        return self._start(lambda: (self._generation.quote(identifier) and "即梦积分报价已返回，请核对后手动批准"))

    def generation_run(self, approved_ceiling, project_id):
        with self.lock:
            self._idle()
            if self.current != project_id:
                raise ValueError("项目已切换")
        return self._start(lambda: (self._generation.run_approved(project_id, approved_ceiling) and "即梦任务已提交，使用原任务ID查询"))

    def generation_refresh(self):
        identifier = self.current
        return self._start(lambda: (self._generation.refresh(identifier) and "任务状态已更新"))

    def generation_download(self):
        identifier = self.current
        return self._start(lambda: (self._generation.download(identifier) and "已下载并核验可用视频"))

    def check_environment(self):
        result = {}
        for name in ('codex', 'ffmpeg', 'ffprobe'):
            path = self._service.config.get(name)
            if not path:
                result[name] = {'ok': False, 'message': '未找到'}
                continue
            command = [path, '--version' if name == 'codex' else '-version']
            try:
                checked = run(command, 15)
                result[name] = {'ok': checked.returncode == 0, 'message': (checked.stdout or checked.stderr).splitlines()[0], 'path': path}
            except Exception as exc:
                result[name] = {'ok': False, 'message': str(exc)}
        try:
            auth = run([self._service.config['codex'], 'login', 'status'], 15)
            result['codex_login'] = {'ok': auth.returncode == 0, 'message': (auth.stdout + auth.stderr).strip()}
        except Exception as exc:
            result['codex_login'] = {'ok': False, 'message': str(exc)}
        import importlib.util
        cpu_model = Path(self._service.config['model']).is_file()
        cpu_torch = importlib.util.find_spec('torch') is not None
        result['local_model'] = {'ok': cpu_model and cpu_torch,
                                 'message': 'LaMa CPU 修复可用' if cpu_model and cpu_torch else
                                            'CPU 修复为可选功能：尚缺本地模型或 PyTorch；拆组和即梦不受影响'}
        canvas = self._service.config.get('dreamina_canvas')
        if canvas:
            try:
                detected = run([canvas, 'version'], 15)
                ok_version = detected.returncode == 0
                ok_account = ok_version and run([canvas, '--format', 'json', 'auth', 'account'], 20).returncode == 0
                result['generation'] = {'ok': ok_account,
                    'message': '即梦 CLI 已安装且账户已授权' if ok_account else
                               '即梦 CLI 存在，但请在 PowerShell 验证版本和登录状态'}
            except Exception:
                result['generation'] = {'ok': False, 'message': '即梦 CLI 启动失败，请核对安装路径和登录'}
        else:
            result['generation'] = {'ok': False, 'message': '未找到即梦 CLI，Windows 可原生安装'}
        return result


def launch():
    import webview
    api = DesktopAPI(Service(settings()))
    html = (Path(__file__).parent / 'ui' / 'index.html').read_text(encoding='utf-8')
    api._window = webview.create_window('对话视频工具', html=html,
                                       js_api=api, width=1280, height=850, min_size=(1000, 700))
    # Require the modern WebView2 engine on Windows 11; IE11 cannot run our UI.
    if os.name == 'nt':
        webview.start(gui='edgechromium', debug=False)
    else:
        webview.start(debug=False)
