# MessageHub

把微信 里需要处理的工作事项汇总到本机工作台。查看待办、截止日历和原文，完成、忽略或撤销都在同一个页面。

## 最快开始

把 [仓库链接](https://github.com/Eagle-Xu0213/MessageHub) 发给能操作这台电脑的 Agent，附上一句话就够了。

> 帮我在这台电脑安装并打开 MessageHub，接入微信。

只想导入文件，直接在这句话里说明。Agent 按 [AGENTS.md](AGENTS.md) 接管全部可自动化的工作：安装 Python、Git 和依赖，克隆并配置项目，启动工作台并注册开机自启与看门狗，给出会话分级建议。你只需做两件事：登录微信客户端；确认自己的名字和会话分级。也可以只发链接，Agent 会用一句话确认接入哪些来源。

### 在 DeepSeek Harness 中使用（可选）

Windows 上已运行 MessageHub、已安装 DSH 0.2.3 桌面版时，可在项目目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dsh_integration.ps1 install
```

在 DSH 左侧点「MessageHub 工作台」打开完整版。在会话右栏的添加菜单中选「待办」，也可以从完整版点「在右侧查看待办」。两处读取同一份本机事项，展开某项后切换页面可以继续查看该事项。扩展只打开本机工作台页面。

安装后若入口未出现，重启 DSH。卸载时用同一条命令，把末尾的 `install` 换成 `uninstall`。

### 自己安装

不通过 Agent 时按下列顺序：

1. Windows 10 / 11 安装 [Python 3.11+](https://www.python.org/downloads/) 和 [Git](https://git-scm.com/download/win)，克隆本仓库后双击 `setup.bat`。也可以直接在项目目录创建虚拟环境并执行 `pip install -e .`。macOS 和 Linux 可使用手动导入。
2. **微信自动采集**需要微信桌面版 4.x。保持微信登录，在项目目录运行下面的命令。它会准备 Moect 源码和依赖，并注册本机计划任务。这一步仅对需要微信自动采集的人适用。
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
   ```
3. 双击 `scripts\install_autostart.bat` 注册开机自启，再双击 `启动 MessageHub.bat` 打开 [工作台](http://127.0.0.1:8000/)。按引导填写你在会话中使用的名字，打开「检测范围」选择群和联系人。已有配置会继续使用。不注册自启时，关闭窗口即停止服务。

## 日常使用

- [工作台](http://127.0.0.1:8000/)显示待办、待确认和截止日历，按来源筛选，点开就能查原文。
- [检测范围](http://127.0.0.1:8000/select)分别管理微信，每条会话直接选「工作」「混合」或「未关注」。
- [运行状态](http://127.0.0.1:8000/#system)查看微信最近同步情况。微信按分钟级间隔同步。
- 在「我的待办 → 导入与采集」，支持 JSONL、JSON、CSV、TXT，或扫描项目的 `imports/`。
- Windows 用户通过 Git 克隆安装后，可在工作台一键更新。本地有未提交改动或分叉时，更新会停止。ZIP 安装需手动升级。

## 可选配置与隐私

任务识别默认使用本地规则，可能漏判或误判，重要事项请核对原文。使用任务识别不需要配置 AI 或邮件。需要更强的识别时，在「设置 → AI 增强」填写 OpenAI Chat Completions 兼容的接口地址、模型名和密钥；密钥留空表示保留原值，配置只保存在本机 `runtime/llm_settings.json`。主服务失败时部分功能可能尝试 Pollinations 或 LLM7 等备用服务，消息内容可能外发。需要邮件提醒时，在「设置 → 邮件通知」填写 SMTP 配置并开启；邮件可能包含事项内容、来源和截止时间。

数据库、微信明文快照和运行配置保存在本机 `runtime/`，个人设置保存在 `config/personal.json`。保护这些文件和 `.env`。排查时可查看 `runtime/moect_headless.log`、`runtime/moect_live.log` 和 `runtime/watchdog.log`，分享前请移除个人信息。

卸载前先停止服务、停用本项目的自启及计划任务，并备份要保留的数据。项目采用 [MIT 许可证](LICENSE)，第三方组件另有许可，见 [第三方组件声明](THIRD_PARTY_NOTICES.md)。反馈请提交到 [GitHub Issues](https://github.com/Eagle-Xu0213/MessageHub/issues)。
