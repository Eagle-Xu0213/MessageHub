# AGENTS.md · 帮用户最快用上 MessageHub

用户发送仓库链接并希望安装时，直接负责安装、配置和验证。先读本页及 [README](README.md)，复用已有代码、虚拟环境、账号配置和数据。优先完成能自动做的部分，把需要人完成的操作集中交给用户。

## 1. 一次收集必要信息

从环境确认操作系统、Python 3.11+、Git、已有检出、配置和服务。用户已说清楚的内容直接沿用。需要询问时合并为一句：想接入微信、QQ 还是只导入文件，以及工作台识别的昵称或发言名是什么。已有身份配置时保留。工作群和联系人让用户稍后在网页选择，无需先发聊天清单。需要首次登录时由用户在客户端完成。

用一句话说明微信采集会在本机生成明文快照、QQ 自动采集会把所选会话的新消息保存到本机。安装可能下载依赖、Moect 源码和注册当前用户计划任务。任务识别无需 API 密钥或 SMTP。用户使用可选外部 AI 或邮件功能时，说明相应数据可能传给配置的服务，部分 AI 功能还有外部备用服务。

## 2. 把工作台先跑起来

- 核对现有目录、Git 远端和未提交改动。复用合适检出，用户未指定且没有已有安装时可克隆到桌面。保留数据库、快照、导入文件和真实配置，无需重建已有 `.venv`。
- Windows 优先运行 `python -m venv .venv`（仅缺失时）和 `.\.venv\Scripts\python.exe -m pip install -e .`。若 Python 命令不可用，试 `py -3`。Linux 和 macOS 用系统的虚拟环境路径，并提供文件导入。自动化中避免调用会暂停的 `setup.bat`。
- `.env`、`config/personal.json` 仅缺失时创建。以 JSON 库填入已知身份和会话，未知值留空，禁止采用示例身份或示例密钥。已知用户昵称或发言名时，请在首次启动前写入配置。需要用户补充时可从首页引导。
- 启动本机 Uvicorn，使用 `.venv` 的 Python 执行 `-m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000`。Windows 微信安装后可由看门狗拉起服务。端口被占用时先核对进程归属。确认 `/api/health` 和首页实际返回后，给用户一个可点击地址。

## 3. 只安装用户选择的采集来源

### 微信

要求微信桌面版 4.x 已由用户登录。Windows 在项目根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
```

脚本自动寻找或下载独立 Moect 源码、安装依赖并注册 `MessageHubHeadless` 和 `MessageHubWatchdog`。无需再安装 Moect 图形程序。核对同名任务是否属于该检出后再使用。检查任务状态、最新快照生成及其实际入库记录，再看 `/api/dashboard/capture`。配置完成或任务注册成功只代表环境就绪，须另行确认采集和入库。

### QQ

优先复用已安装并获用户授权的 OneBot 11 引擎。Windows 上已实际连接过 SnowLuma 2.4.2。若缺失，提供 [发行页](https://github.com/baixiangyuan/SnowLuma/releases/tag/v2.4.2) 及一条明确指令，请用户按 [发行包条款](https://github.com/baixiangyuan/SnowLuma/blob/v2.4.2/EULA.md) 自行获取、解压、确认条款并登录。专有组件的自动部署需取得相应授权。继续交付工作台及其他来源，再从用户选择的安装目录完成连接配置。

```powershell
.\.venv\Scripts\python.exe scripts\configure_qq.py --account <QQ号> --snowluma-root <已安装目录>
```

- 已有 `runtime/qq_onebot.json` 时复用其中的账号和令牌。首次使用从已授权本机引擎确认账号，无法确认再请用户提供。上面的命令会为已安装的 SnowLuma 写入本机反向连接，并保护其他连接设置。已有引擎正在运行时，通过它的正常流程重新载入配置。
- 其他 OneBot 引擎运行同一命令但省略 `--snowluma-root`，然后在该引擎配置 Universal 反向 WebSocket，地址为 `ws://127.0.0.1:8000/api/qq/onebot/ws`，消息格式为数组。将本机配置文件中的令牌填到引擎端，会以 Bearer 方式校验。令牌保留在本机，禁止贴到聊天、终端输出或公开问题。
- 确认管理端口和回连只监听本机，避免多个引擎同时使用同一账号。用户完成 QQ 登录后检查 `/api/qq/status`、心跳和「检测范围 → QQ」的群及好友。提示用户选择会话并保存。有新测试消息才能核实入库和待办。QQ 导出导入始终可独立使用。
- 旧版 `scripts/manage_qq_capture.ps1` 仅管理已存在的 NapCat 安装。指定其实际路径，避免默认下载旧发行包或覆盖用户的引擎设置。

## 4. 验收与交付

1. 核对 [工作台](http://127.0.0.1:8000/) 实际打开。Windows 本机请求可用 `curl.exe --noproxy "*"`。
2. 在 [检测范围](http://127.0.0.1:8000/select) 确认微信和 QQ 选择独立保存。只使用手动导入的用户直接指向「我的待办 → 导入与采集」。
3. 区分服务运行、平台连接、消息入库、规则生成事项四个状态。没有新消息时，如实告知待验证环节。页面自动刷新只更新显示。以平台采集和入库记录判断同步结果。
4. 最终只列用户能做的下一步和入口。需要排查时先读对应日志，只输出脱敏状态。任何升级或维修都保留 `runtime/`、`config/`、`imports/`、`.env` 以及用户未提交的代码。

## 5. 版本与发布

每次完成并验证改动后，维护者按 [语义化版本](https://semver.org/lang/zh-CN/) 选择下一版本，及时同步两个版本声明和变更记录。一般无需再向用户确认选哪一位。具体规则见 [贡献指南](CONTRIBUTING.md#版本管理)。
