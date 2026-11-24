# MessageHub

把微信和 QQ 里需要处理的工作事项汇总到本机工作台。查看待办、截止日历和原文，完成、忽略或撤销都在同一个页面。

## 最快开始

把 [仓库链接](https://github.com/Eagle-Xu0213/MessageHub) 发给能操作这台电脑的 Agent，附上一句话就够了。

> 帮我在这台电脑安装并打开 MessageHub，接入微信和 QQ。

只想用其中一个平台，或只想导入文件，直接在这句话里说明。Agent 按 [AGENTS.md](AGENTS.md) 接管全部可自动化的工作：安装 Python、Git 和依赖，克隆并配置项目，启动工作台并注册开机自启与看门狗，探测 QQ 账号和引擎目录，给出会话分级建议。你只需做三件事：登录微信客户端；自行下载解压 QQ 引擎、接受其条款并登录；确认自己的名字和会话分级。也可以只发链接，Agent 会用一句话确认接入哪些来源。

### 自己安装

不通过 Agent 时按下列顺序：

1. Windows 10 / 11 安装 [Python 3.11+](https://www.python.org/downloads/) 和 [Git](https://git-scm.com/download/win)，克隆本仓库后双击 `setup.bat`。也可以直接在项目目录创建虚拟环境并执行 `pip install -e .`。macOS 和 Linux 可使用手动导入。
2. **微信自动采集**需要微信桌面版 4.x。保持微信登录，在项目目录运行下面的命令。它会准备 Moect 源码和依赖，并注册本机计划任务。这一步仅对需要微信自动采集的人适用。
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
   ```
3. 双击 `scripts\install_autostart.bat` 注册开机自启，再双击 `启动 MessageHub.bat` 打开 [工作台](http://127.0.0.1:8000/)。按引导填写你在会话中使用的名字，打开「检测范围」选择群和联系人。已有配置会继续使用。不注册自启时，关闭窗口即停止服务。

### QQ 实时采集

MessageHub 通过本机 OneBot 11 接收已登录个人 QQ 的新消息。官方 QQ 客户端登录不等于可采集：项目不解密数据库、不 Hook，唯一通道是引擎通过反向 WebSocket 推送事件。已有兼容引擎可直接复用。没有引擎时，请自行获取并解压 [SnowLuma Windows x64 完整包](https://github.com/baixiangyuan/SnowLuma/releases/tag/v2.4.2)，打开发行包条款确认接受并完成 QQ 登录。其专有组件禁止自动部署，须由用户独立安装。[SnowLuma 发行包条款](https://github.com/baixiangyuan/SnowLuma/blob/v2.4.2/EULA.md)

装好并登录后，Agent 会自动探测安装目录和账号并运行下面的命令，无需手动填写账号、路径或令牌。自行安装时把占位值替换成实际的账号和目录，然后让引擎重载配置，回到 [检测范围 → QQ](http://127.0.0.1:8000/select) 选择会话，并以 `/api/qq/status` 确认连接状态。切换引擎时保留原配置，同一账号只连接一个引擎。

```powershell
.\.venv\Scripts\python.exe scripts\configure_qq.py --account <QQ号> --snowluma-root <SnowLuma安装目录>
```

已有其他 OneBot 11 兼容引擎时省略 `--snowluma-root` 来准备接收器，按 [Agent 指南](AGENTS.md) 配置回连。QQ 只采集保存关注范围之后的新消息，历史可用 QQ Chat Exporter V5 文件导入补齐。

## 日常使用

- [工作台](http://127.0.0.1:8000/)显示待办、待确认和截止日历，按来源筛选，点开就能查原文。
- [检测范围](http://127.0.0.1:8000/select)分别管理微信和 QQ，每条会话直接选「工作」「混合」或「未关注」。QQ 自动接收只保存已关注会话启用后的新消息。
- [运行状态](http://127.0.0.1:8000/#system)查看微信最近同步和 QQ 连接。微信按分钟级间隔同步，QQ 通过消息事件实时接收。QQ 断线期间可用文件补齐。
- 在「我的待办 → 导入与采集」，支持 JSONL、JSON、CSV、TXT，或扫描项目的 `imports/`。
- Windows 用户通过 Git 克隆安装后，可在工作台一键更新。本地有未提交改动或分叉时，更新会停止。ZIP 安装需手动升级。

## 可选配置与隐私

任务识别默认使用本地规则，可能漏判或误判，重要事项请核对原文。使用任务识别不需要配置 AI 或邮件。需要更强的识别时，在「设置 → AI 增强」填写 OpenAI Chat Completions 兼容的接口地址、模型名和密钥；密钥留空表示保留原值，配置只保存在本机 `runtime/llm_settings.json`。主服务失败时部分功能可能尝试 Pollinations 或 LLM7 等备用服务，消息内容可能外发。需要邮件提醒时，在「设置 → 邮件通知」填写 SMTP 配置并开启；邮件可能包含事项内容、来源和截止时间。

数据库、微信明文快照和运行配置保存在本机 `runtime/`，个人设置保存在 `config/personal.json`。保护这些文件、`.env` 和 QQ 接收令牌。排查时可查看 `runtime/moect_headless.log`、`runtime/moect_live.log` 和 `runtime/watchdog.log`，分享前请移除个人信息。

卸载前先停止服务、停用本项目的自启及计划任务，并备份要保留的数据。项目采用 [MIT 许可证](LICENSE)，第三方组件另有许可，见 [第三方组件声明](THIRD_PARTY_NOTICES.md)。反馈请提交到 [GitHub Issues](https://github.com/Eagle-Xu0213/MessageHub/issues)。
