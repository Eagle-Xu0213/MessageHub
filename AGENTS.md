# AGENTS.md · MessageHub 操作规程

## 1. 按用户意图选择流程

| 用户意图 | 执行 |
| --- | --- |
| 只发本仓库链接 | 视为安装请求。来源不明确时只问一次「接入微信，还是只导入文件？」 |
| 安装并接入微信 | 执行环境准备、启动工作台和微信接入。 |
| 安装并只导入文件 | 执行环境准备、启动工作台和文件导入；不注册微信快照任务。 |
| 已安装，要求排障 | 先定位实际运行的检出和故障阶段，再处理对应环节。 |
| 开发或维护仓库 | 按任务范围修改和验证，参照末尾的维护规则。 |

## 2. 授权与数据边界

- 接管已授权、可自动化的工作。集中请用户本人登录微信，并确认有证据支持的身份和会话分级。由 Agent 探测候选，不要求用户提供整份群聊或联系人清单。
- 复用现有检出、虚拟环境和真实配置。保留用户未提交的改动、`runtime/`、`config/`、`imports/` 和 `.env`。不代登录、不代接受第三方条款、不自动下载或部署第三方专有组件。
- 告知用户：微信会生成本机明文快照，默认规则在本机运行。仅在用户主动要求时配置 AI 或邮件，先说明发送的数据和目的地。消息页生成待办只使用用户指定的 AI 接口，其他 AI 功能可能尝试备用服务。
- 通过工作台设置或已被 Git 忽略的 `.env` 保存凭据。对话、终端输出、提交和公开问题只能使用脱敏信息。未知身份留空，禁止沿用示例身份、示例会话或假密钥。

## 3. 准备环境并启动工作台

1. **检查前置条件。** 核对操作系统、Python 3.11+、Git、已有检出和 `127.0.0.1:8000` 的进程归属。已有合适检出就复用。未指定安装位置且没有检出时，克隆到桌面。
2. **准备依赖。** Windows 缺少解释器时先检查 `py -3`，仍不可用时运行 `winget install --id Python.Python.3.12 -e`。缺 Git 时运行 `winget install --id Git.Git -e`。若安装受条款确认阻塞，由用户确认。若没有可用的安装工具，只请求用户完成缺失的安装步骤。
3. **在项目根安装。** 虚拟环境缺失时执行 `py -3 -m venv .venv`（Windows，若无启动器则用已验证版本的 `python -m venv .venv`）或 `python3 -m venv .venv`（macOS / Linux）。用环境内的 Python 执行 `-m pip install -e .`，Windows 用 `.\.venv\Scripts\python.exe`，macOS / Linux 用 `.venv/bin/python`。复用已有效的依赖，避免自动化调用会暂停的 `setup.bat`。仅在文件缺失时，从 `.env.example` 准备 `.env`，以 `config/personal.example.json` 为结构参考生成个人配置，清空占位身份和会话。
4. **启动并检查。** 如果端口空闲，用环境内的 Python 从项目根执行 `-m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000`。如果端口被占用，先核对进程归属并复用正确的服务。执行 `GET /api/health` 和 `GET /`，核对版本和页面。Windows 可用 `curl.exe --noproxy "*"` 排除代理干扰。
5. **落实常驻。** Windows 的登录自启用 `scripts\install_autostart.bat install`，写入当前用户 Run 项。启动链为 `autostart_silent.vbs → watchdog_silent.vbs → .venv\Scripts\pythonw.exe → watchdog.py`。看门狗会恢复 API 和快照入库进程。微信场景再按下一节注册定时快照与看门狗任务。确认注册项指向当前检出，并确认进程脱离 Agent 会话后仍可用；仅验证一次 HTTP 响应不能证明常驻。macOS / Linux 按文件导入流程启动，交付时说明服务的运行方式。

## 4. 接入微信

1. **检查前置条件。** Windows 上需要微信桌面版 4.x。检查微信是否已登录。同名计划任务若已存在，先核对其动作和工作目录归属；归属不明时暂停覆盖。
2. **注册任务。** 在项目根执行以下命令。脚本会复用或克隆开源 Moect 源码、安装依赖，并注册 `MessageHubHeadless` 与 `MessageHubWatchdog`。前者用系统 `pythonw.exe` 生成快照，后者约每五分钟走静默启动链恢复 API 和入库进程。需要先检查安装动作时，加 `-DryRun`。

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
   ```

3. **预填身份。** 用 `GET /api/settings/identity` 读取现状。根据本机快照会话及消息发送者统计候选；证据充分且无明确已有配置时，可用 `PUT /api/settings/identity` 预填，并请用户确认或修正。无可靠候选就留空。
4. **建议检测范围。** 用 `GET /api/scope/conversations` 读取会话，结合任务词、本人发言占比和活跃度建议「工作」「混合」「未关注」。不确定时优先建议「混合」或「未关注」。把建议和身份确认集中交给用户；收到确认后才用 `POST /api/scope/config` 保存，再通过 [检测范围](http://127.0.0.1:8000/select) 核对。
5. **处理阻塞。** 未登录则请用户登录并保持微信运行。任务失败先看快照日志；尚未确认检测范围时，只报告已验证的阶段。遇到同名但归属不明的任务，保留原任务并说明冲突。

## 5. 只导入文件

1. **检查前置条件。** 先确认工作台可用，并确认待导入文件是用户指定的 JSONL、JSON、CSV 或 TXT。
2. **执行导入。** 通过工作台「我的待办 → 导入与采集」上传，或向 `POST /api/import` 提交 multipart 字段 `file`。若用户指定扫描项目已有的 `imports/`，用页面扫描按钮或 `POST /api/scan`。
3. **验证结果。** 核对新增及重复条数，并在「消息」页或本机数据库核对导入记录。没有文件时交付工作台和导入入口；如果导入失败，先检查格式、文件路径与接口返回，再说明仍缺什么。

## 6. 按阶段排障和验收

| 阶段 | 成功证据 | 未通过先查 |
| --- | --- | --- |
| 服务可用 | `GET /api/health` 返回 `status: ok` 和预期版本，首页可访问 | 8000 端口归属、自启链、`runtime/watchdog.log` |
| 微信连接 | 微信已登录，采集日志显示已检测并连接目标账号 | 任务动作和 `runtime/moect_headless.log` |
| 快照生成 | `runtime/snapshot_generation.json` 出现本轮新代次 | 最新快照尝试和快照任务结果 |
| 消息入库 | `runtime/moect_live_state.json` 的 `processed_generation` 与新快照一致，`GET /api/dashboard/capture` 对应最近同步；有新消息时抽查实际记录 | 检测范围、`runtime/moect_live.log` 和入库接口错误 |
| 事项生成 | 核对符合规则的消息及其对应待办，能回查来源 | 身份是否已确认、会话分级、规则运行结果 |

快照任务注册成功或页面刷新只能验证对应阶段。没有新消息或没有符合规则的事项时，明确标为待验证。交付给出 [工作台](http://127.0.0.1:8000/) 和实际验证结果，只列用户仍需完成的操作。

## 7. 开发维护

- 按 [贡献指南](CONTRIBUTING.md#版本管理) 选择下一语义化版本，同步 `pyproject.toml`、`src/messagehub/__init__.py` 和 `CHANGELOG.md` 的 `Unreleased`。验证范围与改动风险相匹配，测试使用隔离数据。
- 修改工作台界面时，验证 16 英寸笔记本和 27 英寸显示器在系统缩放后的工作区。DSH 内嵌版需扣除自身侧栏。无实机时用约 1200 和 2200 CSS 像素宽度模拟，并说明采用了模拟视口。
