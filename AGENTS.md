# AGENTS.md — MessageHub AI 安装手册

> **这份文件是写给 AI 助手的，不是写给人的。**
> 你（AI 助手）正在帮一位用户把 MessageHub 装到 TA 的 Windows 电脑上。
> 本文件是你的完整执行手册：按章节顺序执行、每步验收、失败按 §9 排错表处理。
> 人读的文档是 [README.md](README.md)；两者冲突时**以本文件为准**（本文件针对 AI 执行做了防呆）。

---

## 0. 你的工作方式

先判断你属于哪种，再开始：

| 模式 | 特征 | 做法 |
|---|---|---|
| **本地 Agent** | 你能在用户电脑上直接执行命令（Claude Code、Cursor、WorkBuddy 桌面版等） | 直接按本手册执行，每步验收后继续 |
| **远程对话 AI** | 你只能输出文字（网页版 ChatGPT / Claude 等） | 把命令**逐条**发给用户复制粘贴，让用户把输出贴回来，你核对后再给下一条。一次只发一条 |

通用纪律：

- **不要跳步**。每一步末尾的 ✅ 验收通过才进下一步。
- **不要替用户编造配置值**。§1 的必答题没拿到答案就停下来问。
- **隐私红线**：用户的昵称、真名、群名只写入本机 `config/personal.json`，不发送到任何外部服务。
- **命令行优先用 PowerShell**（Windows）；bat 文件里有中文，个别终端乱码。
- 项目全部本地运行；安装过程不登录任何账号、不上传任何聊天数据。唯一的对外调用是用户自己配置的 LLM / SMTP，以及 §6 从 GitHub clone 一份 GPL 源码。

---

## 1. 安装前必问（一次性问完）

**合并成一次提问**，不要挤牙膏。用大白话问，不要甩字段名：

### 必答（缺了装不出完整功能）

1. 电脑是 **Windows 10 / 11** 吗？
   - 是 → 完整安装（含微信实时采集）
   - 否（macOS / Linux）→ **只能手动导入模式**：跳过 §6 和 §7，其余步骤相同；告诉用户把聊天导出文件（JSONL/CSV/TXT）放进 `imports/` 目录，网页点扫描导入
2. 你的**微信昵称**是什么？（群里 `@你` 靠它匹配；可多个，逗号分隔；不确定就让用户打开微信 →「我」看昵称）
3. 你的**真实姓名**？（匹配「@班级+张三」这类群昵称）
4. 你自己在群里**发言时显示的名字**？（判断「这事你回过没有」用，通常同昵称）
5. 你的**工作群名**有哪些？（**和微信里一字不差**，逗号分隔；只有这些群会被提取任务）

### 可选（用户没提就跳过，不要追问）

6. **邮件通知**：要的话请用户提供 SMTP 服务器、端口、发件邮箱、**授权码**（不是登录密码）、收件邮箱 → 填 `.env` 的 `SMTP_*` / `BRIEF_TO`
7. **AI 摘要/加急截止解析**：有 OpenAI 兼容接口的填 `.env` 的 `LLM_*`；没有就留空，自动降级本地规则，功能不受影响

> 微信实时采集是本项目核心功能，**默认要装**（§6）。除非用户明确说「不要」「只用导入文件」，才跳过 §6。

---

## 2. 环境预检

```powershell
python --version
```

- `Python 3.11` 及以上 → 通过（Moect 源码用到 `tomllib`，3.11 是硬下限）
- `python` 不是内部命令 / 弹 Microsoft Store → 试 `py -3 --version`；有 3.11+ 则**后续所有 `python` 换成 `py -3`**
- 都没有 → `winget install Python.Python.3.12`，装完**必须新开终端窗口**再验证
- winget 也没有 → 让用户从 <https://www.python.org/downloads/> 下载安装，**务必勾选 "Add python.exe to PATH"**

```powershell
git --version
```

- 没有且你是本地 Agent → `winget install Git.Git`，新开窗口验证
- 远程 AI 且用户不想装 Git → 备选：下载 <https://github.com/Eagle-Xu0213/MessageHub/archive/refs/heads/main.zip> 解压（效果等同 clone）
- §6 安装脚本也要用 git clone 一份 GPL 源码，所以**最好还是装 Git**

⚠️ **常见坑**：装完 Python/Git 后在旧窗口里验证会失败。所有"装完就验证"都必须**新开终端窗口**。

---

## 3. 获取代码

```bat
cd /d %USERPROFILE%\Desktop
git clone https://github.com/Eagle-Xu0213/MessageHub.git
cd MessageHub
```

装到桌面便于用户找到；用户指定其他目录就听用户的。后面所有命令都在**项目根目录**执行。

✅ 验收：目录里有 `setup.bat`、`README.md`、`src\`。

---

## 4. 安装依赖与配置（非交互路径）

> ⚠️ **不要直接运行 `setup.bat`**。它是给"人双击"设计的：会弹记事本等人编辑、用 `Read-Host` 等人回答装不装 Moect，AI 执行会卡死。走下面的等效非交互路径。

### 4.1 创建虚拟环境并装依赖

```bat
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e .
```

pip 失败（网络超时 / 代理）→ 用清华镜像重试：

```bat
.venv\Scripts\python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
```

✅ 验收：

```bat
.venv\Scripts\python -c "import messagehub.app; print('core ok')"
```

输出 `core ok` 无报错。

### 4.2 生成 .env

```bat
copy .env.example .env
```

按 §1 可选项填 `SMTP_*` / `BRIEF_TO` / `LLM_*`。**用户没配的全部留空即可**——不配 LLM 走本地规则，不配 SMTP 只是不发邮件，其他功能照常。

### 4.3 写入个人配置（关键，漏了 = 简报永远空白）

**先复制模板**（非交互路径不会自动创建，这步漏了后面全空）：

```bat
copy config\personal.example.json config\personal.json
```

再把 §1 必答题 2–5 的答案写进 `config/personal.json`：

```json
{
  "identity": {
    "nicknames": ["必答2：微信昵称"],
    "real_name": "必答3：真名",
    "short_name": "",
    "sender_names": ["必答4：自己发言的显示名"]
  },
  "groups": {
    "work": ["必答5：工作群名，精确匹配微信显示名"],
    "watch": [],
    "excluded": []
  },
  "contacts": { "work": [], "watch": [], "excluded": [] }
}
```

说明：

- `contacts` 是**私聊**白名单：没列出的联系人私聊完全不采集（保守默认）
- `short_name` 是别人直呼你的简称（如姓名去掉姓），想启用直呼匹配才填
- 群名必须和微信里显示**一字不差**，没有模糊匹配

✅ 验收：

```bat
.venv\Scripts\python -c "import json; d=json.load(open('config/personal.json', encoding='utf-8')); print('identity:', bool(d['identity']['nicknames']), '| groups.work:', d['groups']['work'])"
```

昵称非空、工作群列表非空。

---

## 5. 启动与验收

### 5.1 启动服务

**本地 Agent 且有受管后台任务能力**（WorkBuddy / Claude Code 等）：

```bat
.venv\Scripts\python -m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000 --log-level warning
```

作为**受管后台任务**运行（不要 `&` 挂后台，会随会话退出而死）。

**没有后台任务能力 / 给用户长期用**：

```bat
start "" "启动 MessageHub.bat"
```

或让用户双击项目根目录的 `启动 MessageHub.bat`。这个 bat 幂等：服务已在跑时不会重复启动。

> ⚠️ **带沙箱的 AI 终端特别注意**（WorkBuddy / CodeBuddy 桌面版等）：这类环境启动的后台进程**会随 AI 会话结束被清理**，而且 venv 里 `psutil` 可能看不到微信进程（进程枚举被注入污染）。**不要试图在这种终端里长期托管服务或 headless 桥**——按 §6 装好计划任务，由 Windows 计划任务在干净环境里拉起，才是可靠路径。这也正是本项目用两个计划任务的原因。

### 5.2 验收

```powershell
curl -s -m 5 http://127.0.0.1:8000/api/health
```

✅ 输出 `{"status":"ok","version":...}`。让用户浏览器打开 <http://127.0.0.1:8000/>，能看到工作台页面即成功。

失败 → §9 排错表第 1–3 行。

---

## 6. 微信实时采集（核心功能，默认要装）

**架构**：不启动 Moect 图形界面。Windows 计划任务 `MessageHubHeadless` 每 5 分钟用**系统 Python + pythonw**直接调 Moect 的 GPL 源码 `Service.connect()`，静默产明文快照；看门狗 `MessageHubWatchdog` 每 5 分钟保活网页服务和采集器，采集器只读快照入库。两者都不弹窗、不需要人工干预。

### 6.1 前提（用户要做，AI 不要代替）

- 微信桌面版**已登录**且是 **4.x**，保持后台运行
- 微信数据在非标准目录时，需要用户在 Moect 配置里指过去

### 6.2 一条命令完成全部安装

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
```

这条脚本会自动完成（**路径全部从脚本自身位置推导，clone 到任何目录都能用**）：

1. 探测 Moect GPL 源码；没有就 clone 到 `<项目目录>\moect_src`（已自动 gitignore，不会污染本仓库）
2. 找一个系统 Python（3.11+，非 venv），缺依赖就装 `psutil pycryptodomex zstandard pillow python-dotenv`（headless 只需要这几个，**不装** PySide6 / pythonnet / WebView2 那些会出问题的 GUI 栈）
3. 注册两个计划任务（登录时 + 每 5 分钟，均无需管理员权限）：
   - `MessageHubHeadless` → `pythonw.exe moect_headless.py --once`（静默产快照，12 分钟预算）
   - `MessageHubWatchdog` → `powershell.exe watchdog.ps1`（保活服务 + 采集器）

想先看会做什么、不修改系统：命令末尾加 ` -DryRun`。

✅ 验收（脚本末尾会打印两个任务的 State / Execute）；再手动核对：

```powershell
Get-ScheduledTask -TaskName "MessageHubHeadless","MessageHubWatchdog" | Select-Object TaskName,State
```

### 6.3 首次采集验收（等 5–10 分钟）

```bat
type runtime\moect_headless.log
type runtime\moect_live.log
curl -s http://127.0.0.1:8000/api/capture-status
```

✅ 成功标志：

- `moect_headless.log` 出现 `connect 结束: completed ... 找到 N 个会话`、`本轮结果: OK`
- `moect_live.log` 出现 `统计: 扫描=... 推送=... 入库=...`
- 网页工作台「消息总数」持续增长

> 首次运行时 headless 桥会自行检测已登录的微信账号，从微信进程内存提取解密凭据（不启动图形界面、不需要预置任何 keys/config 文件），快照写到项目 `runtime/moect_data/archives/`。凭据只落在本机该目录，不打印、不进日志、不上传。快照单份可达 1.5 GB，采集器会自动清掉旧副本，磁盘只留一份。

---

## 7. 开机自启（零操作体验）

§6 的脚本已经注册了两个计划任务，**登录微信后快照刷新和服务保活就已经全自动了**。再多装一步注册表 Run 键，是为了登录后**立即**打开网页服务（否则最多等 5 分钟看门狗拉起）：

**本地 Agent**（PowerShell）：

```powershell
$VBS = "<项目根目录绝对路径>\scripts\autostart_silent.vbs"
New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "MessageHub" -PropertyType String -Value "wscript.exe `"$VBS`"" -Force | Out-Null
(Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run").MessageHub
```

（`<项目根目录绝对路径>` 换成实际值，例如 `C:\Users\Alice\Desktop\MessageHub`；用 `(Get-Location).Path` 在项目根目录里取。）

**远程对话 AI**：把上面 3 行发给用户，在 PowerShell 里粘贴执行。

✅ 验收：最后一条输出 `wscript.exe "...autostart_silent.vbs"`。

取消自启：删 Run 键 + 停用/删除两个计划任务：

```powershell
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "MessageHub"
Disable-ScheduledTask -TaskName "MessageHubHeadless","MessageHubWatchdog"
```

> 全部无需管理员权限。启动器幂等：服务已在跑时自动退出，不会起两份。

---

## 8. 邮件机制（装完告诉用户会收到什么）

| 路径 | 时机 | 邮件标题 |
|---|---|---|
| 唤醒补扫 | 服务启动（开机 / 重启） | `[MessageHub] 积压整理 …` |
| 每小时汇总 | 常驻期间每 1 小时 | `[MessageHub] 每小时汇总 …` |
| 紧急加急 | 每 3 分钟扫到即发 | `[MessageHub] ⚡加急 …` |

三者共用一个整理水位，已发不重复；急事（直呼 + 指派 + 48h 内截止）另外弹 Windows 桌面通知。**没配 SMTP 就一封都不发**，功能不受影响，网页照样能看。空档期没有工作事件时静默，收件箱没邮件不代表坏了。

---

## 9. 排错速查表

| # | 现象 | 原因 | 处理 |
|---|---|---|---|
| 1 | `/api/health` 连不上 | 服务没起来 / 端口被占 | 看 uvicorn 输出；`netstat -ano \| findstr :8000` 查占用；被占则换端口（启动 bat 里 8000→8001 共 5 处 + `.env` 的 `PORT=8001`） |
| 2 | `pip install` 失败 | 网络 / 代理 | §4.1 的清华镜像命令 |
| 3 | `import messagehub.app` 报错 | 依赖没装全 / .venv 损坏 | 重跑 §4.1；仍不行删 `.venv` 重来 |
| 4 | 网页能开但简报空白 | `personal.json` 没填 / 群名不精确 / 还没采到消息 | §4.3 重写；群名必须和微信显示一字不差；再看 §6.3 日志确认有入库 |
| 5 | `moect_headless.log` 报「找不到 Moect GPL 源码」 | clone 失败或路径不对 | clone <https://github.com/mofan6/Moect> 后在 `.env` 写 `MOECT_SRC=<源码目录绝对路径>`（目录下应有 `core/service.py`） |
| 6 | headless 一直等 connect / 连到 0 会话 | 微信没登录、刚升级、或数据目录不在标准位置 | 登录微信桌面版并保持运行；重启微信再试；确认微信 4.x |
| 7 | 找不到快照 / 采集器没入库 | 计划任务没跑或系统 Python 依赖缺失 | 重跑 §6.2 脚本；看 `runtime/watchdog.log`；`type runtime\moect_headless.log` |
| 8 | 每 5 分钟弹 CMD 黑框 | 计划任务用了 `python.exe` | 重跑 §6.2 脚本（它用 `pythonw.exe` 静默版） |
| 9 | 从沙箱终端跑 headless 看不到微信进程 | 终端注入污染进程枚举 | 不要从 AI 终端嵌套拉起；交给 §6 装好的计划任务 |
| 10 | 杀毒软件报脚本有风险 | 误报 | 脚本只装依赖 + 启动本地服务，不上传数据；加信任即可 |
| 11 | AI 摘要 / 分级接口把服务卡死 | 旧版本 bug | `git pull` 更新后重启（2026-11-19 已修复） |
| 12 | 没收到邮件 | 没配 SMTP / 无事静默 / 服务没跑 | 查 `.env` 的 SMTP 五项（`SMTP_PASS` 是授权码）；查日志 `[digest]` 字样；`runtime/digest_state.json` 是否存在；没事件时按设计不发 |

排不掉的问题让用户带完整报错去 <https://github.com/Eagle-Xu0213/MessageHub/issues> 提 Issue。

---

## 10. 完成后告诉用户的话

安装收尾时用一段话向用户交代（远程 AI 让用户保存）：

1. **日常入口**：<http://127.0.0.1:8000/> 工作台；`/brief` 简报；`/live` 消息流；`/memories` 去年今日
2. **会收到什么**：开机把离开期间攒的工作消息整理成邮件；平时每小时汇总一次新增；急事立即邮件 + 桌面通知。没配邮箱就只在网页看
3. **每天要做的**：只登录微信桌面版并保持运行。**不用打开微信翻群**，该知道的事系统会送到面前
4. **数据在哪**：全部在本机项目目录 `runtime\`，不上传任何服务器；卸载 = 删项目文件夹 + 删两个计划任务 + 删 Run 键
5. **改配置**：记事本编辑 `config\personal.json`（加减工作群）保存即可，下一轮自动生效；改完身份/群名想立即看效果，重启一次服务
6. **自检**：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1" -DryRun` 可随时核对计划任务配置是否正确
