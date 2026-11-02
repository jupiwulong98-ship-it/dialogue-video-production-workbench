# Windows 11 无独显：安装和验收

目标机器：Windows 11 x64，无独立显卡（集成显卡或 CPU）。本项目的扫描抽帧依赖 CPU 和 FFmpeg，人物配组由 Codex 云端模型完成，视频生成由即梦云端完成。**主流程无需 CUDA / PyTorch**。

## 1. 确认基础环境

- 安装 Python 3.11，Windows Edge WebView2 Runtime（Win11 通常已具备，但需要验证）。
- 安装 FFmpeg / FFprobe、Codex CLI 和即梦 Canvas CLI，运行命令前确认 CLI 属于官方来源。
- 将工作台放在普通本地文件夹（尽量避免同步中的 OneDrive 目录），原视频和生成结果应有足够磁盘空间。

## 2. PowerShell 安装命令（客户电脑执行）

下面是需要执行的命令。远程安装脚本由官方发布，**先核对来源再执行**；安装后重新打开终端刷新 PATH。

```powershell
winget install -e --id Python.Python.3.11
winget install -e --id Gyan.FFmpeg
powershell -ExecutionPolicy Bypass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
irm https://jimeng.jianying.com/canvas-cli/install.ps1 | iex
```

然后分别登录、检查服务端账户：

```powershell
codex login
codex login status
dreamina-canvas version
dreamina-canvas auth login
dreamina-canvas --format json auth account
dreamina-canvas --format json model list --type video
ffmpeg -version
ffprobe -version
```

即梦官方 Windows CMD 安装命令也可在官网查阅：https://jimeng.jianying.com/ai-tool/install 。

## 3. 安装/启动工作台

在工作台根目录运行本地 PowerShell 安装脚本（只装 Python 基础依赖，不自动装第三方 CLI）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

完成后双击 `start_windows.bat` 启动，或命令行执行：

```powershell
.\.venv\Scripts\python.exe -m dialogue_tool doctor
.\.venv\Scripts\python.exe -m dialogue_tool desktop
```

如果 CLI 已安装但软件显示未找到，重启 PowerShell/软件，必要时创建未跟踪的 `settings.local.json`：

```json
{
  "codex": "C:\\实际目录\\codex.exe",
  "dreamina_canvas": "C:\\实际目录\\dreamina-canvas.exe",
  "ffmpeg": "C:\\实际目录\\ffmpeg.exe",
  "ffprobe": "C:\\实际目录\\ffprobe.exe"
}
```

上面路径仅为示意，必须填客户机器真实可执行路径，绝不能在仓库提交 Cookie、Token、登录凭证。

## 4. 最小验收

1. 能启动 WebView2 窗口；导入 15 秒样片能完整扫描。
2. Codex 已登录，能对真实短片抽取对话 A/B；原片不被修改。
3. 选择一组 A/B，填写提示词，查询即梦当前可用且支持双参考的模型。
4. 准备生成草稿、查看最高积分报价。**此前不批准扣费**。
5. 经客户针对本次金额明确同意，生成最小数量样例，使用原 submitId 查询状态，成功后校验本地下载。
6. 如生成状态不明，不盲目重试；必须先查询原任务状态，避免重复扣费。

## 5. CPU 性能与边界

CPU 扫描长片可能较慢，无独显不影响即梦云端生成。当前默认不安装 PyTorch；可选 LaMa 修图需要单独安装 CPU PyTorch 和本地 LaMa TorchScript 模型。Windows 的真实性能、截图准确率和即梦实际计费生成仍须在客户机器现场验证。

**当前是 Windows 开发启动版，不是已经封装的独立 EXE 安装包。**

参考：
- 即梦官方安装 https://jimeng.jianying.com/ai-tool/install
- OpenAI Codex Windows 安装 https://github.com/openai/codex
- pywebview WebView2 https://pywebview.idepy.com/en/guide/web_engine