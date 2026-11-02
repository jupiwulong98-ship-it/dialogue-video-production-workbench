# 即梦画布 CLI 接入与任务协议

日期：2026-11-02。实现：dialogue_tool/generation.py、desktop.py、ui/index.html。

## 选择的接口

优先采用即梦官网的 **全能画布 CLI**，可执行文件 \`dreamina-canvas\`。
选择 Canvas 是因为现在的 A/B 是两名人物的独立**参考图**，并非起始帧/结束帧：
- 对两个人物参考，使用 \`node create video --mode m2v --ref res:<A> --ref res:<B>\`；
- \`first_last_frame\` 是时间上的首尾过渡，A/B 会被当成视频起终点，不能默认用于人物对话；
- 本版本仅实现 **多参考视频**，不接用户提供的音色 MP3，不做音画对齐或拼接。

官方 CLI 和普通 Web API 不是同一种认证方式。**本版本无需填写 API Key**：客户在自己的 Mac 安装和登录 CLI。未来若采用火山引擎/第三方 API，须另开发适配器，不要把 API Key 填入 CLI 路径字段。

官方安装入口：https://jimeng.jianying.com/ai-tool/install
Mac（安装前先查看官方脚本内容，确保来源可信）：

\`\`\`bash
curl -fsSL https://jimeng.jianying.com/canvas-cli/install.sh | bash
dreamina-canvas --help
dreamina-canvas version
dreamina-canvas schema
dreamina-canvas auth login
dreamina-canvas --format json auth account
dreamina-canvas --format json model list --type video
\`\`\`

如果 CLI 不在 PATH，在仓库根目录新建未跟踪的 \`settings.local.json\`：

\`\`\`json
{
  "dreamina_canvas": "/Users/你的用户目录/.local/bin/dreamina-canvas"
}
\`\`\`

路径仅为示例，按实际安装位置调整。也支持环境变量 \`DIALOGUE_CANVAS_CLI\`。**不得**把 Cookie、Token 或登录态 JSON 写入仓库或此配置文件。

## 软件实际调用顺序

在已完成自动配组的项目中，勾选需要生成的 A/B 组，右侧“即梦视频生成”：
1. 点击**查询可用视频模型**，用实际返回的模型标识填入输入框，填时长、全局提示词；单组提示词与全局提示词拼接（缺任意一项可，但最终 prompt 不得为空）。
2. **① 准备草稿**：持久化固定 \`projectId\` → \`canvas create\` → 对每个 A/B 使用预先生成的 \`resourceId\` 上传 → \`node create video --mode m2v\`；保存真实 \`nodeId\`。此阶段不扣费。
3. **② 查询积分报价**：对节点执行 \`node quote\`，必须得到 \`confirmable=true\`、\`totalMaxCredits\`。显示用户当前批量最高积分。
4. **③ 确认并提交**：用户在窗口中主动确认**最高额度与组数**；重新报价确保未超过上限，\`node confirm --credit-ceiling\` 取得内存中的短时令牌；为每个节点预先持久化新的 \`submitId\`，一次性 \`node run\` 提交。请求可能产生真实消费。
5. **④ 更新任务状态**：逐个 \`operation status <submitId>\`；不生成新 submitId，不因本地超时自动重复收费。
6. **⑤ 下载**：根据成功结果的 \`resourceId\` 使用 \`resource get\` + \`resource download\`，验证本地大小和 SHA256，存回原项目文件夹。

关键节点 \`projectId\`、\`resourceId\`、\`nodeId\`、\`submitId\` 都与 **本地图片组 ID** 关联；一个 A/B 组对应一个收费节点和一次提交身份，不会把多组图片合并成一段视频。

### 输入与返回

| 阶段 | 给 CLI 的字段 | 要保存的返回 |
|---|---|---|
| 登录/发现 | 本机 CLI、账号授权 | 可用模型能力，仅展示 |
| Canvas | 画布名称、持久的 UUID | projectId |
| 素材 | A/B PNG 完整路径、各自 resourceId、projectId | resourceId |
| 视频草稿 | 两个 res: 引用、合并 prompt、model、duration、mode=m2v | nodeId |
| 报价 | projectId、nodeIds | totalMaxCredits、confirmable |
| 授权 | 已核对的积分上限、节点清单 | 短时 creditConfirmationToken（内存，**不落盘**） |
| 生成 | 同一个 projectId、节点与预存 submitId | 受理/部分受理/不确定；不是最终视频 |
| 查询 | projectId、submitId | submission.state、成功后 resourceId |
| 下载 | projectId、resourceId、输出目录 | 文件路径、byteCount、SHA256 |

### 异常处理与边界

- 上传时预先保存 \`resourceId\`，失败/超时后必须用原 ID 恢复；**创建节点结果不明时**无法保证无重复，会暂停并提示核对，不盲目重新创建。
- \`submitId\` 在收费运行之前写入本地 SQLite。只要有 submitId，就禁止重新创建付费任务；重新运行前必须由用户明确决定，当前 UI 不做自动重提。
- 程序关闭时并不会取消服务器上的任务。重新启动后用原 ID 点“更新任务状态”。
- 更换图片、修复图、单组 prompt、模型或全局 prompt 后，已有草稿不能自动沿用；错误消息会阻止过期参数提交。
- \`operation status\` 的输出资源字段依赖实际 CLI 版本；若没有可确认的 \`resourceId\`，该组保持 \`completed_no_resource\`，**不能宣称已下载成功**，需检查实际返回再补充解析。
- \`model list\` 返回的模型能力须由用户核对；不同模型并不保证支持两张图的 m2v 或任意视频时长。账号权限不够、模型不支持时会返回失败。
- 当前只做单机本地验证版。Mac 真机安装、账号登录和一次实际视频生成仍需要真实环境与用户自愿批准积分，尚未完成收费端到端验收。
