# MessageHub

**把聊天记录中的工作消息整理成待办、简报和提醒。**

MessageHub 是运行在自己电脑上的消息整理工具。它可以读取你导入的聊天记录；在 Windows 上，也可以通过 Moect 生成的微信快照定期获取消息。程序尝试找出与工作有关的通知和任务，并在网页中展示；配置邮箱后，还可以发送汇总和紧急提醒。

[开始安装](#开始安装windows) · [日常使用](#日常使用) · [可选配置](#可选配置) · [常见问题](#常见问题) · [隐私与数据](#隐私与数据)

> MessageHub 使用规则识别消息，结果可能遗漏或误判。重要事项请回看原消息确认。微信采集是间隔数分钟的定期更新，不能保证即时送达。

## 能做什么

- **整理工作消息**：结合你设置的身份和会话范围，识别指派、通知和需要回复的消息，查看原文及上下文。
- **跟踪处理情况**：尝试根据后续回复判断事项是否已处理，也可在工作台管理任务。
- **查看消息**：在本地网页查看简报、消息流、历史搜索和“去年今日”。
- **接收提醒**：微信新快照完成补扫后发送当前待办，运行期间每小时汇总全部未完成任务；24 小时内到期的任务会尝试发送邮件和 Windows 系统通知。
- **选择消息来源**：手动导入聊天导出文件，或在 Windows 上配置微信自动采集。

AI 和邮件均为可选功能。未配置 AI 时使用本地规则；未配置邮箱时不发送邮件。自动识别与提醒受消息内容、配置、采集和服务运行状态影响，不保证不漏消息。

## 使用前准备

| 需要 | 说明 |
| --- | --- |
| 系统 | Windows 10 / 11 适合使用完整功能；macOS / Linux 可使用手动导入，不能使用这里的 Windows 微信采集脚本。 |
| Python | 3.11 或更新版本。 |
| Git | 下载项目及微信自动采集所需的 Moect 源码。仅使用手动导入时，也可下载项目 ZIP。 |
| 微信 | 仅自动采集需要：Windows 桌面版微信 4.x 已登录并保持运行。 |
| 网络 | 安装依赖和源码需要联网；配置外部 AI、邮件或检查更新时也需要网络。 |
| 磁盘空间 | 微信明文快照可能很大，单份可达约 1.5 GB，实际大小取决于数据量。 |

## 开始安装（Windows）

### 1. 安装 Python 和 Git

安装 [Python](https://www.python.org/downloads/) 3.11 或更新版本，安装时勾选 **Add python.exe to PATH**。再安装 [Git for Windows](https://git-scm.com/download/win)。安装后打开新的 PowerShell 窗口，检查：

```powershell
python --version
git --version
```

如果 `python` 无法使用，可试 `py -3 --version`。安装向导也会尝试查找 Python。

### 2. 下载项目

在 PowerShell 中执行：

```powershell
cd $HOME\Desktop
git clone https://github.com/Eagle-Xu0213/MessageHub.git
cd MessageHub
```

也可以从 [GitHub 项目页面](https://github.com/Eagle-Xu0213/MessageHub)通过 **Code → Download ZIP** 下载并解压。后续命令均在项目目录执行；配置自动采集仍需要 Git。

### 3. 运行安装向导并填写身份

双击项目目录中的 `setup.bat`。向导会创建运行环境、安装依赖并生成配置；首次安装时会打开记事本。

在 `config/personal.json` 中将示例值改为自己的信息：

- `identity.nicknames`：你的微信昵称，可以填多个。
- `identity.real_name`：真实姓名，帮助识别群昵称中的称呼。
- `identity.sender_names`：你发言时显示的名字，帮助识别自己的回复。
- `groups.work`：需要整理的工作群名称，须与微信显示名称一致。

其他示例群、联系人和简称按需替换或清空；不使用的列表写成 `[]`，不使用的字符串写成 `""`。保留 JSON 格式中的引号和逗号，保存并关闭记事本。身份或群名填错时，消息可能无法被正确识别。

向导可能询问是否安装 Moect 图形程序；下方自动采集使用单独下载的 Moect 源码，因此此处可选择跳过。向导完成不等于自动采集已经配置。

### 4. 检查可选服务配置

安装向导会从 `.env.example` 创建 `.env`。**模板含示例 AI 密钥和邮箱信息，并非空配置。**如果暂时不使用这些服务，请用记事本打开项目根目录的 `.env`，把 `LLM_API_KEY`、`SMTP_USER`、`SMTP_PASS` 和 `BRIEF_TO` 的等号右侧清空并保存。以后需要时再按[可选配置](#可选配置)填写。

### 5. 启动并选择消息来源

双击 `启动 MessageHub.bat`，在浏览器打开 [http://127.0.0.1:8000/](http://127.0.0.1:8000/)。看到工作台后，按以下方式选择消息来源：

**手动导入**：在工作台的“导入聊天记录”区域选择 JSONL、JSON、CSV 或 TXT 文件；也可以把文件放入项目的 `imports` 文件夹，再点击“立即扫描 imports/ 目录”。导出格式需要与项目支持的解析格式匹配；页面上传的 CSV 和 TXT 按行导入，未必保留原始会话、发送者和时间。导入后查看消息和任务，确认内容被正确识别。

**微信自动采集**：完成下面的额外设置。两种方式可以按需使用。

### 6. 可选：设置微信自动采集

先登录 Windows 桌面版微信 4.x 并保持运行。在项目目录打开 PowerShell，执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
```

脚本会下载或查找 Moect 的独立 GPL 源码、为系统 Python 安装所需依赖，并注册 `MessageHubHeadless` 和 `MessageHubWatchdog` 两个当前用户的 Windows 计划任务及系统通知快捷方式。它们分别负责刷新快照，以及保活网页服务和采集器。此过程会修改计划任务并联网下载依赖；只想预览时，在命令末尾加 `-DryRun`。

首次启用新规则以启用时刻为基线，不把已有历史消息批量变成任务；之后的关机期间消息会在新快照采集完成后补扫。首次运行后等待几分钟，回到工作台查看消息数量和采集状态。也可在 PowerShell 查看任务状态：

```powershell
Get-ScheduledTask -TaskName "MessageHubHeadless","MessageHubWatchdog" | Select-Object TaskName,State
```

看到任务存在不代表消息已成功采集；如果没有消息，请查看[常见问题](#常见问题)。微信账号需要保持登录，快照刷新、入库和提醒各有周期，实际延迟可能超过几分钟。

### 7. 可选：登录 Windows 时立即启动

双击 `scripts/install_autostart.bat`，可为当前用户添加登录自启项。前一步安装的看门狗计划任务也会定期尝试启动服务；此自启项用于登录后尽快启动。

取消该自启项：在项目目录的命令提示符中运行 `scripts\install_autostart.bat uninstall`。若还要停用自动采集，在 PowerShell 中运行：

```powershell
Disable-ScheduledTask -TaskName "MessageHubHeadless","MessageHubWatchdog"
```

## 日常使用

| 页面 | 用途 |
| --- | --- |
| [工作台](http://127.0.0.1:8000/) | 导入消息、扫描文件、查看和管理任务。 |
| [工作简报](http://127.0.0.1:8000/brief) | 打开统一的任务台。 |
| [消息流](http://127.0.0.1:8000/live) | 查看采集状态和近期消息。 |
| [检测范围](http://127.0.0.1:8000/select) | 选择需要关注的群聊和联系人，保存前请检查建议结果。 |
| [去年今日](http://127.0.0.1:8000/memories) | 回顾一年前同一天的消息。 |

使用自动采集时，保持微信登录，并确保服务及计划任务正常运行。手动导入时，需要自行提供新的导出文件。修改身份和会话配置后，如需立即看到完整效果，可重启服务并检查结果。

## 可选配置

### 选择会话范围

`config/personal.json` 中的 `groups` 和 `contacts` 使用 `work`（工作）、`watch`（混合关注）、`excluded`（排除）分类。未列入允许范围的私聊默认不采集。可以在[检测范围页面](http://127.0.0.1:8000/select)调整，或直接编辑文件；群名和联系人名请与显示名称保持一致。

### 邮件提醒

编辑项目根目录的 `.env`：

| 配置项 | 填写内容 |
| --- | --- |
| `SMTP_HOST` | 邮箱服务商提供的 SMTP 服务器地址。 |
| `SMTP_PORT` | SMTP SSL 端口，例如 QQ 邮箱常用 `465`。 |
| `SMTP_USER` | 发件邮箱。 |
| `SMTP_PASS` | 邮箱服务商提供的 SMTP 授权码。 |
| `BRIEF_TO` | 接收提醒的邮箱。 |

保存后重启服务。启动补扫和每小时汇总在没有待办或待确认事项时会静默；紧急提醒要求个人任务有可识别的截止时间且距离截止不超过 24 小时。桌面通知依赖 Windows 通知环境，邮件发送还依赖邮箱配置及网络。

### AI 摘要和辅助识别

如需使用 OpenAI Chat Completions 兼容服务，在 `.env` 中填写 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`，保存后重启服务。没有实际密钥时应将 `LLM_API_KEY` 留空；仅删除示例文本的一部分不能关闭它。

启用 AI 会把相关消息文本及可能包含的会话名、发送者等信息发送给 AI 服务。**部分 AI 请求在主服务失败时，会尝试 Pollinations 和 LLM7 等内置第三方备用服务**；如果不希望内容发送到这些服务，请保持 `LLM_API_KEY` 为空。更多信息见[隐私与数据](#隐私与数据)。

## 常见问题

**网页打不开？** 确认安装向导完成，双击启动器后访问 `http://127.0.0.1:8000/`；查看启动窗口里的错误，并检查 8000 端口是否被其他程序占用。

**网页打开了，但没有待办？** 先确认已导入或采集到消息，再检查 `personal.json` 中的身份、发言名称和工作群名称。并非所有消息都会被识别成任务。

**微信没有新消息？** 检查微信是否已登录、计划任务是否存在，以及 `runtime/moect_headless.log`、`runtime/moect_live.log` 和 `runtime/watchdog.log`。微信升级或数据目录不在常用位置也可能影响采集。

**没有收到邮件？** 检查 SMTP 配置、授权码、收件邮箱、网络及服务运行状态。没有符合条件的新增事项时不会发送汇总。

**如何更新？** 如果使用 Git 下载，在项目目录执行 `git pull`，按需重新运行安装向导更新依赖，再重启服务。首页可能显示更新提示，请结合 GitHub 仓库实际版本核对。

**如何卸载？** 先退出服务、移除登录自启项，并在 Windows 任务计划程序中停用或删除 `MessageHubHeadless`、`MessageHubWatchdog`；删除开始菜单中的 `MessageHub Notifications` 快捷方式。确认不再需要本地聊天数据后，再删除项目文件夹；删除前请自行备份需要保留的内容。

仍有问题可在 [GitHub Issues](https://github.com/Eagle-Xu0213/MessageHub/issues) 反馈。分享日志前请检查并遮去聊天内容、身份信息、密钥和邮箱授权码。

## 隐私与数据

- 消息数据库、微信明文快照及运行日志主要保存在本机项目目录下的 `runtime/`；手动导入文件保存在你选择的位置。快照和数据库可能包含敏感聊天内容，请妥善保护和备份。
- 微信自动采集依赖 Moect 源码读取已登录微信的数据并生成明文快照；解密凭据和快照应视为敏感信息。本项目默认监听本机 `127.0.0.1`。
- 使用外部 AI 时，消息内容、摘要所需信息及会话名等可能发给配置的服务和上述备用服务；使用邮件时，整理结果及相关消息内容会经过配置的 SMTP 服务发送到收件邮箱。请根据自己的隐私要求决定是否启用。
- 首页版本检查会请求 GitHub；安装依赖和 Moect 源码也会访问相应下载服务。使用本地 AI 服务不意味着所有 AI 请求都只留在本机，因为部分请求仍可能进入备用服务。
- `.env`、`config/personal.json`、`runtime/` 和 `imports/` 已配置为不提交到本项目 Git 仓库；不要将这些文件或未经检查的日志公开分享。

## 开发与贡献

Python 3.11+ 环境中安装并运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000
```

测试命令为 `.\.venv\Scripts\python -m pytest -q`。测试使用独立测试数据库和虚构身份配置；请勿将测试环境指向真实数据库。macOS / Linux 可使用相应虚拟环境路径启动服务和手动导入，Windows 专用采集和自启脚本不适用。

主要代码位于 `src/messagehub/`，采集与安装脚本位于 `scripts/`，测试位于 `tests/`。更多贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)，AI 安装指引见 [AGENTS.md](AGENTS.md)。欢迎提交 Issue 或 Pull Request，请勿提交个人配置、凭据或聊天数据。

## 许可证与致谢

MessageHub 采用 [MIT 许可证](LICENSE)。微信快照功能依赖单独下载的 [Moect](https://github.com/mofan6/Moect) GPL 源码；其他第三方组件及许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。请遵守适用的法律和平台条款，并尊重聊天参与者的隐私。
