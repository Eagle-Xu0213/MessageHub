# AGENTS.md · 帮用户最快用上 MessageHub

用户发来仓库链接就是请求安装。**总原则：Agent 接管一切能自动完成的事，用户只做无法代劳的事。** 用户操作压到最少，指令压到一句话，每一步用人话说明。

## 0. 角色边界（先读，别越界）

用户只需亲自做两件，其余一律 Agent 完成，不要把能自动做的事甩回给用户：

1. 登录微信桌面版 4.x（接入微信时）。
2. 确认自己的微信昵称和会话分级——这是身份与隐私判断，Agent 可预填建议，但不可擅自定稿。

**Agent 绝不**：代登录、代接受第三方条款、自动下载或部署第三方专有组件、把本机令牌或密钥贴进对话/终端输出/公开问题、用示例身份或假密钥填空。

## 1. 一句话启动，少问多做

README 给用户的启动指令是：

> 帮我在这台电脑安装并打开 MessageHub，接入微信。

- 用户说清接入哪些来源就照办；只发链接没说来源时，用一句话确认"微信还是只导入文件"。
- **不要索要**聊天清单、群名单、昵称——这些 Agent 自己探测或稍后请用户确认。
- 需要用户动手的事集中说明，不在过程中零散打断。
- 用一句话讲清：微信采集会在本机生成明文快照，数据都留在本机；任务识别不需要密钥或 SMTP；可选 AI/邮件才会把内容发给相应服务。

## 2. Agent 自动完成装机（全程不问用户）

### 2.1 环境与依赖

- 核对操作系统、Python 3.11+、Git、已有检出、配置和服务。复用合适检出、`.venv`、数据库、快照、导入文件和真实配置，不重建已有环境。
- **Python 缺失就自己装**，不要让用户装：优先 `winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements`；winget 不可用时试 `py -3`；都没有才给出 python.org 下载链接，此时请用户装一次。
- **Git 缺失同理**：`winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements`。
- 用户未指定且无已有安装时克隆到桌面。换机器不改脚本路径。
- 建 `.venv` 并 `.\.venv\Scripts\python.exe -m pip install -e .`；依赖已含 `python-multipart`，不要漏装。macOS / Linux 用系统虚拟环境并只提供文件导入。
- `.env` 和 `config/personal.json` 仅缺失时从 example 复制；以 JSON 库写入已知身份，未知值留空。**禁止写入示例身份、示例群名或假密钥。**
- 自动化中避免调用会暂停的 `setup.bat`；直接执行等价命令。

### 2.2 起服务并确保常驻

- 起本机服务：`.venv` 的 Python 执行 `-m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000`。端口被占用先核对进程归属，不要盲目结束。
- **关键**：Agent 会话结束后自己起的进程会被清理。要让服务长期可用，必须注册当前用户自启 `scripts\install_autostart.bat install`（写 HKCU Run → `autostart_silent.vbs`），并让计划任务看门狗每 5 分钟拉活 API 和采集器。只在会话里起一次服务不算部署完成。
- 确认 `/api/health` 与首页实际返回后，给用户可点击的 [工作台](http://127.0.0.1:8000/) 地址。Windows 本机请求可用 `curl.exe --noproxy "*"`。
- 交付时保留 `runtime/`、`config/`、`imports/`、`.env` 和用户未提交的代码。

## 3. 微信：用户只登录一次

- 要求微信桌面版 4.x 已由用户登录。未登录就请用户登录并保持运行——这是账号主体，无法代劳。
- Agent 在项目根运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
```

- 脚本自动寻找或克隆独立 Moect 源码、安装依赖、注册 `MessageHubHeadless` 和 `MessageHubWatchdog`，无需 Moect 图形程序。核对同名任务是否属于该检出后再用。
- **验收**：任务状态 → 最新快照生成 → `/api/dashboard/capture` → 实际入库记录。注册任务成功只代表环境就绪，必须另行确认采集和入库。
- **昵称**：Agent 从快照会话表和本库消息的发送者名统计出候选，经 `GET /api/settings/identity` 看现状、`PUT` 写入候选作为预填，再请用户确认或修正。不要凭空编造昵称，也不要因为用户一时没答就反复追问。

## 4. 会话分级：Agent 预选，用户确认

- Agent 调 `GET /api/scope/conversations` 拿会话清单，按任务词命中、我方发言占比、最近活跃度给出三级建议：工作 / 混合 / 不检测。
- 拿不准的一律往保守放（混合或不检测），**不要默认全部设成工作**，否则噪声淹没真正待办。
- 把建议整理成一句话请用户确认或微调，用户点头后 Agent 通过 `POST /api/scope/config` 保存。用户只需确认，不必逐条勾选。

## 5. 验收与交付

1. [工作台](http://127.0.0.1:8000/) 实际打开；[检测范围](http://127.0.0.1:8000/select) 保存微信会话分级。
2. 区分四个状态：服务运行、平台连接、消息入库、规则生成事项。没有新消息时如实告知待验证环节；页面自动刷新只更新显示，以采集和入库记录判断同步结果。
3. 邮件和 AI 默认都不配。用户主动要邮件提醒时才引导在「设置 → 邮件通知」填 SMTP 授权码；要 AI 时引导在「设置 → AI 增强」填接口地址、模型名和密钥。用户私下给出的密钥由 Agent 通过工作台表单或 gitignored 的 `.env` 写入本机，禁止写进仓库文件、对话或公开问题；并说明消息内容可能外发、主服务失败时可能尝试免费备用服务。
4. 最终只列用户能做的下一步和入口。排查先读对应日志，只输出脱敏状态，分享前移除个人信息。

## 6. 版本与发布

每次完成并验证改动后，维护者按[语义化版本](https://semver.org/lang/zh-CN/)选择下一版本，及时同步两个版本声明和变更记录，一般无需再问选哪一位。规则见[贡献指南](CONTRIBUTING.md#版本管理)。
