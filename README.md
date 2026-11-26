# MessageHub

**把微信里的工作事项汇总到本机待办。** 在工作台查看截止日期、回查消息原文，也可以手动添加待办或导入已有文件。

[交给 Agent 安装](#交给-agent-安装) · [自己安装](#自己安装) · [日常使用](#日常使用)

## 交给 Agent 安装

把 [仓库链接](https://github.com/Eagle-Xu0213/MessageHub) 发给能操作这台电脑的 Agent，再复制适合你的一句。

> 帮我在这台电脑安装并打开 MessageHub，接入微信。

> 帮我在这台电脑安装并打开 MessageHub，只导入文件。

接入微信时，你只需要登录微信桌面版，并确认 Agent 找到的昵称和建议检测的会话。安装、启动和检查同步由 Agent 按 [AGENTS.md](AGENTS.md) 完成。

## 自己安装

### Windows 接入微信

需要 Windows 10 或 11、[Python 3.11+](https://www.python.org/downloads/)、[Git](https://git-scm.com/download/win) 和已登录的微信桌面版 4.x。

1. 克隆本仓库，双击项目里的 `setup.bat` 安装依赖。
2. 保持微信运行，在项目目录打开 PowerShell 并运行以下命令。脚本会准备 Moect 开源采集组件，并注册定时快照与自动恢复服务的任务。

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
   ```

3. 双击 `scripts\install_autostart.bat` 注册登录自启，再双击 `启动 MessageHub.bat` 打开 [工作台](http://127.0.0.1:8000/)。
4. 在工作台确认你的微信昵称，再到 [检测范围](http://127.0.0.1:8000/select) 选择会话。稍等几分钟，到 [运行状态](http://127.0.0.1:8000/#system) 查看最近同步时间，再到「消息」核对新消息是否出现。

### 只导入文件

Windows、macOS 和 Linux 都可以只导入文件。需要 Python 3.11+。在项目目录执行对应命令，启动后保持终端运行。

**Windows PowerShell**

如果没有 `py` 启动器，且 `python --version` 显示 3.11 或更新版本，第一行改用 `python -m venv .venv`。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000
```

**macOS / Linux**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000
```

打开 [工作台](http://127.0.0.1:8000/)，在「我的待办 → 导入与采集」上传 JSONL、JSON、CSV 或 TXT。页面会显示新增和重复条数。也可以把文件放进项目的 `imports/`，再从页面扫描目录。

## 日常使用

| 想做什么 | 去哪里 |
| --- | --- |
| 看待办、截止日期和待确认事项 | [工作台](http://127.0.0.1:8000/) |
| 查看来源或核对消息原文 | 打开待办详情，或进入工作台的「消息」页 |
| 调整哪些群聊和私聊参与检测 | [检测范围](http://127.0.0.1:8000/select)，按会话选择「工作」「混合」或「未关注」 |
| 查看微信最近同步时间 | [运行状态](http://127.0.0.1:8000/#system) |

## 在 DeepSeek Harness 中使用

需要 Windows、已运行的 MessageHub 和 DSH 0.2.3 桌面版。在项目目录执行以下命令安装扩展。若入口未出现，重启 DSH。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dsh_integration.ps1 install
```

- 在 DSH 左侧点「MessageHub 工作台」打开完整版。也可以在会话右栏添加「待办」，两处使用同一份本机数据。
- 启用 DS Bot 后，DSH「工作」导航提供 DS Bot 入口，并收起独立的「任务看板」。在有活动会话时，点 DS Bot 左下角的 ✓，即可在右侧打开 MessageHub 待办。
- 卸载扩展时，将命令末尾的 `install` 改为 `uninstall`。

## 数据与隐私

| 功能 | 数据怎样处理 |
| --- | --- |
| 本地规则 | 默认在本机识别待办，无需 AI 密钥或 SMTP。规则可能漏判或误判，重要事项请核对原文。 |
| 微信自动采集 | 在本机生成明文快照，并将采集到的消息存入本机数据库。 |
| AI 增强（可选） | 在「设置 → AI 增强」配置兼容 OpenAI Chat Completions 的接口。用「消息」页生成待办时，选中的消息只发给你指定的接口。其他 AI 功能在主服务失败时可能尝试 Pollinations 或 LLM7 等备用服务。 |
| 邮件通知（可选） | 在「设置 → 邮件通知」填写 SMTP 配置并启用后，通知会经邮件服务发送，邮件可能包含事项内容、来源和截止时间。 |

保护 `runtime/` 中的数据库、明文快照和运行设置，以及 `config/personal.json` 和 `.env`。分享诊断信息前请移除聊天内容、个人信息和密钥。

## 更新、排障与开发

- **更新**。Windows 上通过 Git 克隆安装的用户可在工作台一键更新。若有未提交改动或分叉，先处理本地改动。ZIP 安装需手动更新。
- **排障**。先看工作台「运行状态」。需要进一步排查时，查看 `runtime/moect_headless.log`（生成快照）、`runtime/moect_live.log`（消息采集）或 `runtime/watchdog.log`（服务自动恢复）。反馈可提交到 [GitHub Issues](https://github.com/Eagle-Xu0213/MessageHub/issues)。
- **卸载**。先停止服务，停用本项目的自启和计划任务，并备份要保留的数据。
- **参与开发**。见 [贡献指南](CONTRIBUTING.md) 和 [变更记录](CHANGELOG.md)。项目采用 [MIT 许可证](LICENSE)，第三方组件见 [第三方组件声明](THIRD_PARTY_NOTICES.md)。
