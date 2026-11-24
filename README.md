# MessageHub

把微信和 QQ 里需要处理的工作事项汇总到本机工作台。查看待办、截止日历和原文，完成、忽略或撤销都在同一个页面。

## 最快开始

把 [仓库链接](https://github.com/Eagle-Xu0213/MessageHub) 发给能操作这台电脑的 Agent，附上一句话就够了。

> 帮我在这台电脑安装并打开 MessageHub。我想接入微信和 QQ。

只想用其中一个平台，或只想导入文件，直接在这句话里说明。Agent 会按 [AGENTS.md](AGENTS.md) 检查和复用现有安装、安装依赖、启动工作台，然后只请你完成必要的登录、QQ 引擎首次设置和会话选择。你也可以只发送链接，Agent 会用一句话确认要接入哪些来源。

### 自己安装

1. Windows 10 / 11 安装 [Python 3.11+](https://www.python.org/downloads/) 和 [Git](https://git-scm.com/download/win)，克隆本仓库后双击 `setup.bat`。也可以直接在项目目录创建虚拟环境并执行 `pip install -e .`。macOS 和 Linux 可使用手动导入。
2. **微信自动采集**需要微信桌面版 4.x。保持微信登录，在项目目录运行下面的命令。它会准备 Moect 源码和依赖，并注册本机计划任务。这一步仅对需要微信自动采集的人适用。
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
   ```
3. 双击 `启动 MessageHub.bat` 打开 [工作台](http://127.0.0.1:8000/)。按引导填写你在会话中使用的名字，打开「检测范围」选择群和联系人。已有配置会继续使用。

### QQ 实时采集

MessageHub 通过本机 OneBot 11 接收已登录个人 QQ 的新消息。已有兼容引擎可以直接复用。没有引擎时，可自行获取并解压 [SnowLuma Windows x64 完整包](https://github.com/baixiangyuan/SnowLuma/releases/tag/v2.4.2)，首次启动时请自行阅读许可和隐私条款，并完成 QQ 登录。SnowLuma 的专有组件有自动部署限制，需要独立安装。 [SnowLuma 发行包条款](https://github.com/baixiangyuan/SnowLuma/blob/v2.4.2/EULA.md)

Agent 可以用下方命令配置已安装的 SnowLuma，无需手动填写令牌。自行安装时，把示例账号和路径替换成自己的。然后在 SnowLuma 本机管理页接入 QQ，回到 [检测范围 → QQ](http://127.0.0.1:8000/select) 选择会话。切换引擎时请保留原配置，同一账号只连接一个引擎，避免登录冲突。

```powershell
.\.venv\Scripts\python.exe scripts\configure_qq.py --account 123456789 --snowluma-root "D:\你的SnowLuma目录"
```

已有其他 OneBot 11 兼容引擎时，可以省略 `--snowluma-root` 来准备 MessageHub 接收器。按 [Agent 指南](AGENTS.md) 配置引擎回连。QQ 无法实时连接时，也可以导入 QQ Chat Exporter V5 文件。

## 日常使用

- [工作台](http://127.0.0.1:8000/)显示待办、待确认和截止日历，按来源筛选，点开就能查原文。
- [检测范围](http://127.0.0.1:8000/select)分别管理微信和 QQ，每条会话直接选「工作」「混合」或「未关注」。QQ 自动接收只保存已关注会话启用后的新消息。
- [运行状态](http://127.0.0.1:8000/#system)查看微信最近同步和 QQ 连接。微信按分钟级间隔同步，QQ 通过消息事件实时接收。QQ 断线期间可用文件补齐。
- 在「我的待办 → 导入与采集」，支持 JSONL、JSON、CSV、TXT，或扫描项目的 `imports/`。
- Windows 用户通过 Git 克隆安装后，可在工作台一键更新。本地有未提交改动或分叉时，更新会停止。ZIP 安装需手动升级。

## 可选配置与隐私

任务识别使用本地规则，可能漏判或误判，重要事项请核对原文。使用任务识别不需要配置 AI 或邮件。需要邮件提醒时，在「设置 → 邮件通知」填写 SMTP 配置并开启；邮件可能包含事项内容、来源和截止时间。可选 AI 功能才会使用本机 `.env` 中的 `LLM_BASE_URL`、`LLM_MODEL` 和 `LLM_API_KEY`，其中部分功能可能尝试 Pollinations 或 LLM7 等备用服务。

数据库、微信明文快照和运行配置保存在本机 `runtime/`，个人设置保存在 `config/personal.json`。保护这些文件、`.env` 和 QQ 接收令牌。排查时可查看 `runtime/moect_headless.log`、`runtime/moect_live.log` 和 `runtime/watchdog.log`，分享前请移除个人信息。

卸载前先停止服务、停用本项目的自启及计划任务，并备份要保留的数据。项目采用 [MIT 许可证](LICENSE)，第三方组件另有许可，见 [第三方组件声明](THIRD_PARTY_NOTICES.md)。反馈请提交到 [GitHub Issues](https://github.com/Eagle-Xu0213/MessageHub/issues)。
