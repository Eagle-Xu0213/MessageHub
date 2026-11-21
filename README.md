# MessageHub

**你不在电脑前时攒下的工作消息，回来时已经整理成一份待办清单躺在邮箱里。**

本地运行的聊天消息中枢：接入微信真实群聊消息，用规则引擎提取「谁让你干什么、什么时候截止」。程序唤醒时（开机/服务启动）自动把上次整理以来的积压事件整理成邮件发给你，真正紧急的事（直呼 + 指派 + 48 小时内截止）则立即加急推送。所有聊天数据只存在你本机的 SQLite 里，不上云、不上传。

> 核心思路：群消息不是用来读的，是用来**变成任务**的。21 万条历史消息里真正需要你动手的不到 1%，问题从来不是消息太多，而是没有过滤。定时晨报没意义——你几点开电脑不固定；真正有价值的是「离开期间攒下的，回来一次性整理好」。

---

> ### 🤖 交给 AI 安装
>
> 不想手动装？把这个仓库链接直接发给你的 AI 助手（Claude、ChatGPT、Cursor、WorkBuddy 等）并说一句「帮我安装」。
>
> AI 助手会自动读取仓库根目录的 **[AGENTS.md](AGENTS.md)**——那是为本项目专门写的 AI 安装手册：只问你 **5 个关于你自己的问题**（微信昵称、真名、工作群名等），其余全部由它执行并逐步验收，失败有对应排错表。你不需要懂命令行。人也可以照着它手动装。
>
> 喜欢自己动手 → 直接看下面的[零基础安装](#零基础安装windows从零开始)。

---

## 目录

- [🤖 交给 AI 安装](#-交给-ai-安装)
- [能做什么](#能做什么)
- [它是怎么工作的](#它是怎么工作的)
- [零基础安装（Windows，从零开始）](#零基础安装windows从零开始)
- [快速开始（开发者）](#快速开始开发者)
- [Moect 集成（微信消息来源）](#moect-集成微信消息来源)
- [配置](#配置)
- [邮件通知机制](#邮件通知机制)
- [工作事件提取规则](#工作事件提取规则)
- [页面与 API](#页面与-api)
- [数据来源适配器](#数据来源适配器)
- [排错 FAQ](#排错-faq)
- [测试](#测试)
- [安全边界](#安全边界)
- [已知限制](#已知限制)
- [项目结构](#项目结构)
- [License](#license)

---

## 能做什么

| 功能 | 说明 |
|---|---|
| **唤醒补扫邮件** | 服务启动时把「上次关机 → 现在」积压的事件一次性整理发到邮箱。LLM 打语义标签分组（课程作业 / 报名截止 / 会议安排…），另附一段人话摘要。不看网页也知道离开了多久攒了哪些事 |
| **每小时汇总** | 电脑连续开着时，每 1 小时把这一小时新增的普通工作事件整理成一封「工作汇总」邮件。和补扫共用一个整理水位，不重复、不漏；这一小时没有工作事就静默不发 |
| **紧急加急** | 直呼你 + 明确指派 + LLM 解析截止在 48h 内，三者同时满足 → 立即发加急邮件 + Windows 桌面通知，不限数量，同一条不重复发。已加急的事件不再进汇总，避免同一件事打扰两次 |
| **事件提取** | 规则引擎判断哪些消息是在给你派活：`@你`、直呼你、上下文对话中的祈使句，配合任务语义打分 |
| **闭环检测** | 事件之后翻你的回复，已经答应的、已经交付的（包括直接把文件发过去）自动归入「已处理」，不再进待办——不会反复提醒你做过的事；配置了 LLM 还会对词表漏掉的说法做语义复核 |
| **实时消息流** | 每 120 秒增量拉取新消息，SSE 推送到网页，指纹去重保证幂等 |
| **网页简报** | `/brief` 按需看三栏简报（待办 / 需回复 / 通知），每条挂着原文出处；不再定时自动发信 |
| **去年今日** | 按北京时间严格匹配一年前的同一天，回顾当时群里在聊什么 |
| **全文检索** | SQLite FTS5 索引，按群、时间、关键词查历史消息 |

---

## 它是怎么工作的

```
【快照刷新链】由 Windows 计划任务驱动，无需人工干预
  微信客户端（已登录）
      │
      ▼
  MessageHubHeadless 计划任务（每 5 分钟，pythonw 静默无窗口）
      └─ scripts/moect_headless.py   ← headless 桥：直接调 Moect 源码 Service.connect()
             └─ 明文快照 archives/current/（zstd 压缩）

【采集入库链】看门狗保活
  MessageHubWatchdog 计划任务（每 5 分钟拉活）
      ├─ API 服务（uvicorn :8000）
      └─ scripts/moect_live.py --loop --no-refresh   ← 只读快照、解压、增量推
             │
             ▼
        POST /api/live/ingest        ← 唯一稳定接入点，指纹去重
             │
             ▼
        SQLite（messages 表）──────────┐
             │                          │
             ▼                          ▼
        rules.py 事件提取          SSE → /live 实时流
             │
             ▼
        resolution.py 闭环检测
             │
       ┌──────────────┼──────────────────┐
       ▼              ▼                  ▼
  digest 唤醒补扫  digest 每小时汇总   digest 加急检查
  （服务启动1次）  （每 1 小时）        （每 3 分钟）
       │              │                  │
       └──── 共用同一整理水位 ────┘   三者同时满足？
                │                         │
          LLM 打标签分组            邮件 + 桌面通知
                │
       ┌────────┴────────┐
       ▼                 ▼
  积压整理邮件        每小时汇总邮件
  （开机攒的）        （开着时新增的）
```

**为什么要 headless 桥**：微信 4.x 的消息区是自绘渲染层（`MMUIRenderSubWindowHW`），UIA 无障碍树里读不到任何正文。只能从数据库层取，而数据库是 SQLCipher 加密的，密钥只在已登录的微信进程内存里。早期方案是启动 Moect GUI 触发解密，但它的 WebView2 / .NET 初始化在本机极不稳定（反复弹窗、卡死、静默退出）。现在的 headless 桥直接 import Moect 的 GPL 源码调用 `Service.connect()`，不开任何界面，稳定得多。刷新快照（headless 任务）和读快照入库（采集器）完全解耦，各自独立保活。

**为什么是「补扫 + 每小时 + 加急」三条路径**：你几点开电脑不固定，定时晨报在 8 点发信时服务可能还没起，积压的消息就漏了。所以服务一唤醒就把「上次关机 → 现在」一次性整理发信（补扫）；电脑连续开着不重启时，靠每 1 小时的汇总兜住中途新增的普通事件；真正急的事不等汇总，扫到就立即加急。三条路径共用同一个整理水位，互不重叠、已发不重复——目标是平时不用主动打开微信翻群，该知道的事系统送到你面前。

---

## 零基础安装（Windows，从零开始）

没写过代码也能装。每一步只做一件事，做完再进下一步。

### 第 1 步：安装 Python 3.12

- 浏览器打开 <https://www.python.org/downloads/>，点黄色的 **Download Python 3.12.x** 按钮
- 运行下载的安装包，**安装第一屏最下方务必勾选 `Add python.exe to PATH`**，再点 Install Now
- 装完按 `Win+R`，输入 `cmd` 回车，在新窗口输入 `python --version`，显示 `Python 3.12.x` 就成功了

也可以在 cmd 里一行搞定（Win10/11 自带 winget）：

```bat
winget install Python.Python.3.12
```

> ✅ 成功的样子：`python --version` 有版本号输出。
> ✗ 失败了：看 [排错 FAQ](#排错-faq) 第 2 条。

### 第 2 步：安装 Git

```bat
winget install Git.Git
```

装完**关掉旧的 cmd 窗口，开一个新窗口**（否则 git 命令不生效），输入 `git --version` 验证。

### 第 3 步：下载本项目

```bat
cd /d %USERPROFILE%\Desktop
git clone https://github.com/Eagle-Xu0213/MessageHub.git
cd MessageHub
```

这会把项目下载到你的桌面，并进入项目目录。以后所有的命令都在这个目录里运行。

### 第 4 步：双击 `setup.bat`

它会自动完成：创建独立运行环境 → 下载依赖 → 生成配置文件 → 打开记事本让你填写身份 → 询问是否安装 Moect → 自检。第一次运行需要联网下载依赖，大约 1-2 分钟。

> ✅ 成功的样子：窗口最后显示「安装完成！下一步」，并且**没有红色报错**。
> 重复双击也是安全的，已完成的步骤会自动跳过。

### 第 5 步：填写你的身份和工作群

setup.bat 会自动打开记事本，编辑 `config\personal.json`，把里面两项改成你自己的：

```json
{
  "identity": {
    "nicknames": ["你的微信昵称"],
    "real_name": "你的真名",
    "sender_names": ["你的微信昵称"]
  },
  "groups": {
    "work": ["你的工作群名1", "你的工作群名2"]
  }
}
```

- `nicknames` / `real_name` / `sender_names`：程序靠它认出「哪条消息是在叫你」
- `groups.work`：程序靠它判断「哪些群是来工作的」

保存关闭即可。字段含义详见 [配置](#配置) 章节。

> ⚠️ **不填会怎样**：软件能启动、能导入历史消息，但**不会生成任何待办**——因为它不知道你是谁、哪些群算工作群。这是「简报是空的」的第一大原因。

### 第 6 步：要用微信实时采集，运行一条安装命令（PowerShell）

登录微信桌面版并保持运行，然后在项目目录打开 PowerShell，粘贴这一条：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
```

它会自动完成三件事：下载 Moect 的 GPL 源码（独立开源项目，不装 Moect 图形界面）、给系统 Python 装好快照刷新所需的几个小依赖、注册两个静默运行的 Windows 计划任务（每 5 分钟刷新快照 + 保活服务）。**路径自动识别，clone 到任何目录都能用**，不需要管理员权限。想先看它会做什么、不实际修改系统，命令末尾加 ` -DryRun`。

> 不装也完全能用：把聊天记录导出文件（JSONL/CSV/TXT）丢进 `imports\` 目录扫描导入，邮件整理等其余功能一模一样。详见 [Moect 集成](#moect-集成微信消息来源) 章节。

### 第 7 步：双击「启动 MessageHub.bat」

浏览器会自动打开 <http://127.0.0.1:8000/>，工作台页面出现即成功。保持窗口开启即可使用，关闭窗口即停止服务。

服务启动时会自动补扫积压并发一封整理邮件，之后常驻期间每 1 小时汇总一次新增、急事随时插队（如果要发信，先在 `.env` 配好 SMTP，见[配置](#配置)）。之后每天的使用就是：**登录微信 → 双击启动 → 看邮件/网页简报 → 干活**——平时不用主动打开微信翻群，该知道的事系统会送到你面前。

> 想连双击都省了？做[第 8 步](#第-8-步可选开机自启开机后零操作)开机自启，登录微信后什么都不用点。

### 第 8 步（可选）：开机自启，开机后零操作

先按[第 6 步](#第-6-步要用微信实时采集运行一条安装命令powershell)装好两个计划任务，再双击 `scripts\install_autostart.bat` 装上注册表 Run 键（登录后立即打开服务）。之后登录 Windows 就能全自动运行——你唯一要做的就是**登录微信客户端**，其余不用碰：

| 组件 | 由谁拉起 | 作用 |
|---|---|---|
| 网页服务 + 采集器 | 注册表 Run 键 → `autostart_silent.vbs`，另有 `MessageHubWatchdog` 计划任务每 5 分钟保活 | 服务挂了自动拉起；采集器只读快照入库 |
| 快照刷新 | `MessageHubHeadless` 计划任务（登录时 + 每 5 分钟，静默无窗口） | headless 桥调 Moect 源码刷新明文快照 |
| 邮件整理 | 服务内部的 digest 调度线程 | 启动时补扫积压 → 每 1 小时汇总新增 → 每 3 分钟查加急，三条路径都不漏 |

装好之后，开机 → 登录微信 → 等几十秒，积压的工作消息就整理成邮件发到你的邮箱了；之后即使一直不关机，每小时也会收到新增工作的汇总，急事则随时插队。

取消自启：

```bat
scripts\install_autostart.bat uninstall
```

> **原理**：Run 键写一条 `wscript.exe autostart_silent.vbs`（不需管理员权限），两个计划任务用 `MessageHubHeadless`（系统 Python + `pythonw.exe`，不弹黑框）和 `MessageHubWatchdog`（PowerShell 保活）注册。启动器都是幂等的——服务已在运行时自动退出，重复操作不会起两份。
>
> 如果 `install_autostart.bat` 在某些终端里因中文编码乱码执行失败，直接用 PowerShell 手动装 Run 键（等效）：
>
> ```powershell
> $VBS = "$PWD\scripts\autostart_silent.vbs"   # 在项目根目录运行
> New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "MessageHub" -PropertyType String -Value "wscript.exe `"$VBS`"" -Force | Out-Null
> ```
>
> **本机注意**：headless 桥必须走系统 Python + 计划任务触发，不能从某些带沙箱注入的终端里嵌起——那些环境会污染进程枚举，导致看不到微信进程。用上面装好的计划任务就没这个问题。

---

## 快速开始（开发者）

已经熟悉 Python 和命令行的话，走这条路：

### 环境要求

- **Python 3.11+**（开发环境为 3.12）
- **Windows 10/11**（Moect 通道仅 Windows；其他功能跨平台）
- 微信客户端（要用实时采集的话）

### 安装

```bash
git clone https://github.com/Eagle-Xu0213/MessageHub.git
cd MessageHub

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .
```

### 配置

```bash
# Windows
copy config\personal.example.json config\personal.json
copy .env.example .env
# macOS / Linux
cp config/personal.example.json config/personal.json
cp .env.example .env
```

编辑 `config/personal.json`，至少填两项：

```json
{
  "identity": {
    "nicknames": ["你的微信昵称"],
    "real_name": "你的真名",
    "sender_names": ["你的微信昵称"]
  },
  "groups": {
    "work": ["工作群名1", "工作群名2"]
  }
}
```

**不填会怎样**：导入、检索、去年今日照常工作，但不会提取任何工作事件——因为程序不知道「你是谁」和「哪些群算工作群」，瞎猜没有意义。

### 启动

```bash
# Windows：双击
启动 MessageHub.bat

# 手动启动
set PYTHONPATH=src          # Windows
export PYTHONPATH=src       # macOS / Linux

python -m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/>。

`启动 MessageHub.bat` 会自动探测 Python（优先 `.venv`，其次 PATH），换机器不用改脚本。零基础用户装完环境后也可以直接用它——只要先跑过一次 `setup.bat` 把依赖装好。

---

## Moect 集成（微信消息来源）

这是整个项目**最难复现的一环**，单独讲清楚。

### Moect 是什么

[Moect](https://github.com/mofan6/Moect) 是一个 GPL-3.0 的开源微信数据库解密工具（Windows）。它用你已登录的微信进程内存里的密钥，解密本地 SQLCipher 加密数据库，导出为明文 SQLite 快照。

**本项目不包含、不分发 Moect**，只调用它（或其 GPL 源码）产出的明文快照。

> **两种工作模式**，取决于你的机器：
> - **headless 模式（推荐，Windows + 已配置计划任务）**：`scripts/moect_headless.py` 直接 import Moect 的 GPL 源码调 `Service.connect()`，不开任何界面。稳定、静默，不受 WebView2 / .NET 初始化问题困扰。这是本项目实测最可靠的路径。
> - **GUI 回退模式**：采集器退而求其次启动 `Moect.exe --smoke` 触发刷新。在本机因 GUI 初始化不稳已不作为主路径，但代码保留作兜底。

### 复现步骤

#### 1. 一键安装（推荐）

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
```

这条命令会自动 clone Moect 的 GPL 源码到项目目录 `moect_src/`（已 gitignore，不进本项目发行包）、装好 headless 所需的依赖、并注册两个计划任务。headless 桥从这里 import `core.service.Service`，**不启动 Moect 图形界面**——这正是它比旧方案稳定的原因。

源码位置自动探测，顺序为：`.env` 的 `MOECT_SRC` → 项目内 `moect_src/` → 用户主目录 `moect_src/` → `D:\Projects\moect_src`。手动 clone 到别处时，在 `.env` 里写一行即可：

```ini
MOECT_SRC=C:\你的路径\Moect
```

> 微信数据目录不在标准位置时（本机是 `D:\Wechat_Files\xwechat_files`），需要在 Moect 设置里指过去。

#### 2. 确认快照位置

Moect 默认把快照放在 `%LOCALAPPDATA%\Moect\archives\<账号ID>\` 下。本项目的 headless 模式则写到项目内：

```
runtime/moect_data/archives/current/     ← 当前工作副本，采集器读这里
```

里面是 `message_*.db`（消息）、`contact.db`（联系人）等文件。看看当前快照是否生成：

```bat
dir runtime\moect_data\archives\current
```

能看到一堆 `.db` 文件就对了。

#### 3. 让 MessageHub 找到 Moect（仅 GUI 回退模式需要）

headless 模式不用配 `MOECT_EXE`——它直接用源码。只有走 GUI 回退时才需要让采集器找到 `Moect.exe`，它按这个顺序探测：

1. 系统环境变量 `MOECT_EXE`
2. 项目根 `.env` 文件里的 `MOECT_EXE`（采集器自己会读取 `.env`）
3. `%LOCALAPPDATA%\Programs\Moect\Moect.exe`
4. `%PROGRAMFILES%\Moect\Moect.exe`
5. `%PROGRAMFILES(X86)%\Moect\Moect.exe`

装在别处就显式指定，在 `.env` 里加 `MOECT_EXE=C:\Tools\Moect\Moect.exe`，改完下一轮采集生效。验证探测结果：

```bash
python scripts/moect_live.py --check
```

#### 4. 刷新快照（headless 桥）

正式运行交给第 1 步装好的计划任务（`pythonw.exe` 静默、无黑框、每 5 分钟一轮）。想手动调试一轮也可以：

```bat
# 单轮：连一次微信、产出快照、退出（计划任务就是这个模式）
C:\Users\<你>\AppData\Local\Programs\Python\Python312\pythonw.exe scripts\moect_headless.py --once
```

> ⚠️ 用**系统 Python**（3.11+），不要用项目的 `.venv`，也不要从带沙箱注入的 AI 终端里嵌套拉起——那些环境会污染进程枚举，导致看不到微信进程。计划任务跑在干净环境里，没有这个问题。

成功日志（`runtime/moect_headless.log`）：

```
[20:38:15] headless bridge 启动 mode=once
[20:38:15] connect 任务已创建 c29bb0e0...，等待完成…
[20:38:22] connect 结束: completed (100%) detail=已连接，找到 1453 个会话
[20:38:22] 新快照: ...\archives\58fa5d0d0fe6b507\snapshot_c29bb0e05491
[20:38:22] 本轮结果: OK
```

首次运行时 headless 桥会自行检测已登录的微信账号，从微信进程内存提取解密凭据（不启动图形界面、不需要预置任何 keys/config 文件），快照写到 `runtime/moect_data/archives/`。凭据只落在本机该目录，不打印、不进日志、不上传。之后每轮只更新快照。

#### 5. 采集入库

快照由 headless 任务产出后，采集器只读它、不触发刷新：

```bash
python scripts/moect_live.py --loop --interval 120 --no-refresh
```

`--no-refresh` 表示只读现有快照——这是当前的推荐姿态，刷新交给独立的 headless 任务。它读快照 → 解压 zstd → 提取群聊文本 → POST 到 `/api/live/ingest`。日志在 `runtime/moect_live.log`。

直接双击「启动 MessageHub.bat」或装好开机自启后，这些都会由计划任务/看门狗自动拉起，不用手动跑。

### 命令行参数

`scripts/moect_headless.py`（刷新快照，系统 Python 直跑）：

| 参数 | 说明 |
|---|---|
| `--once` | 连一次微信、产快照、退出（计划任务用这个） |
| `--loop` | 常驻循环刷新 |

`scripts/moect_live.py`（读快照入库）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--once` | — | 跑一轮就退出 |
| `--loop` | — | 常驻循环 |
| `--interval` | 60 | 轮询间隔（秒） |
| `--since-hours` | 24 | 首次回溯多少小时 |
| `--limit` | 3000 | 单轮最多推送条数 |
| `--no-refresh` | off | **只读写好的快照、不碰 GUI**（当前推荐姿态，刷新交给 headless 任务） |
| `--keep-snapshots` | off | 不清理旧快照 |
| `--include-private` | off | 同时采集单聊（**默认只采群聊**） |
| `--check` | — | 探测 Moect.exe 路径（仅 GUI 回退模式用） |

### 几个实现细节（踩过的坑）

**headless 桥绕开整个 GUI 栈**。Moect GUI 的 WebView2 / pythonnet 初始化在本机反复失败（弹「GDI+ Window」fallback 窗口、`__ready` 永不设置、`--smoke` 30 秒超时退出）。而且 `--smoke` 本质只是 UI 回归测试钩子，真正的快照来自前端 React 加载后调 `startup_reconnect`。headless 桥直接 import 源码 `Service`：先 `detect()` 找到已登录的微信账号（首次运行顺带初始化 library 配置），再直接 `connect()`，轮询任务完成后 `shutdown()`——完全绕开 GUI 及其教程门控（`tutorials_seen.app` 那套只在 GUI 自动重连时起作用）。另有一个隐藏陷阱：stdout 接 `DEVNULL` 会让 pythonnet 自杀，所以计划任务用 `pythonw.exe` 并把输出写进日志文件。

**刷新与采集解耦，各自保活**。`MessageHubHeadless` 任务负责产快照（每 5 分钟，`pythonw.exe` 静默无黑框、12 分钟预算、`IgnoreNew` 防重叠）；`MessageHubWatchdog` 负责拉活 API 和采集器（每 5 分钟）。两者独立，一个挂了不影响另一个。

**单实例锁用回环端口**，不用锁文件。`127.0.0.1:8765` 被占用说明采集器已有实例在跑，第二个进程直接退出——进程崩溃即释放，不留僵尸锁文件。

**水位回看 5 分钟**。新消息落库可能乱序（`create_time` 相同或倒序），所以水位推进时回退 300 秒覆盖抖动。重复由 ingest 端指纹去重兜底，多推不会重复入库。

**快照目录要清理**。Moect 每轮 connect 会在 `archives/<账号ID>/` 下新建一份 `snapshot_*`（单份可达 1.5 GB）。采集器会把新快照合并到 `archives/current/` 并清掉旧副本，避免磁盘被撑爆——清理有路径白名单，**永不触碰** `%LOCALAPPDATA%\Moect\archives` 下你自己的快照。历史快照积压太多时可以只留 `current/`。

### 常见失败与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| headless 日志报找不到微信进程 | 从带沙箱注入的终端里跑了桥 | 改用装好的计划任务/系统 Python 直跑，别在受限终端里嵌起 |
| headless 一直等 connect 不完成 | 微信未登录，或 Moect 源码路径不对 | 登录微信桌面版并保持运行；核对 `moect_headless.py` 顶部 `sys.path.insert` 的源码路径 |
| 快照目录是空的 / 连到 0 会话 | 微信刚升级、密钥变了，或数据目录不在标准位置 | 重启微信重新登录；确认微信 4.x；检查 Moect `config.json` 里 `library.root` 指向 `archives/current` |
| 采集器报连不上 127.0.0.1:8000 | MessageHub 服务没启动 | 先双击「启动 MessageHub.bat」，等页面能打开再跑采集 |
| 每 5 分钟弹一个 CMD 黑框 | 计划任务用了 `python.exe` | 确认 `MessageHubHeadless` 的 Execute 指向 `pythonw.exe`（无控制台版本） |
| `--check` 显示 Moect ✗ 未找到（仅 GUI 模式） | 装在非标准位置 | 在 `.env` 里写 `MOECT_EXE=完整路径` |

排查顺序建议：先看 `runtime/moect_headless.log`（快照有没有产出）→ `runtime/moect_live.log`（有没有入库）→ `runtime/watchdog.log`（服务有没有被拉活）。

### 不用 Moect 可以吗

可以。`/api/live/ingest` 是通用接入点，任何能产出标准格式 JSON 的工具都能推：

```bash
curl -X POST http://127.0.0.1:8000/api/live/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{
      "source_platform": "wechat",
      "source_method": "custom",
      "conversation_id": "123@chatroom",
      "conversation_name": "工作群",
      "sender_name": "张三",
      "sent_at": "2026-11-18T10:00:00+08:00",
      "text": "明天下午三点前把材料交一下"
    }]
  }'
```

也支持文件导入：把导出的 JSONL / JSON / CSV / TXT 丢进 `imports/` 目录，调 `POST /api/scan` 自动扫描入库。

---

## 配置

### `config/personal.json`（个人配置，不进版本库）

| 字段 | 说明 |
|---|---|
| `identity.nicknames` | 你的微信昵称，可多个。`@` 匹配用这个 |
| `identity.real_name` | 真名。匹配「@班级+姓名」这类群昵称 |
| `identity.short_name` | 简称，用于直呼匹配（「张三你帮我…」） |
| `identity.short_name_exclusions` | 简称后跟这些字不算称呼，防复合词误判（如「世博会」） |
| `identity.sender_names` | 你自己发言的显示名，闭环检测靠它判断「我回复了没」 |
| `groups.work` | 工作群名单，提取指派 / @我 / 通知 |
| `groups.watch` | 混杂群，必须「cue 我 + 有任务语义」双条件才报 |
| `groups.excluded` | 明确排除的群，规则不读 |
| `contacts.work` | 工作级联系人（备注/昵称），TA 的私聊非噪声即报 |
| `contacts.watch` | 混合级联系人，私聊需带任务语义才报 |
| `contacts.excluded` | 不检测的联系人；**未列出的联系人私聊一律不采集** |
| `aliases` | 群 ID → 可读群名（Moect 导出只带 chatroom id） |
| `brief.persona` | 生成简报时 LLM 的人设描述 |

### `.env`（环境配置，不进版本库）

| 变量 | 说明 |
|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 任意 OpenAI 兼容接口。不配则降级本地规则/群名标签 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `BRIEF_TO` | 邮件推送（整理邮件 + 加急邮件），可选；`SMTP_PASS` 是邮箱授权码不是登录密码 |
| `MOECT_SRC` | Moect **GPL 源码目录**（其下应有 `core/service.py`）。headless 模式自动探测，源码装在别处时才需要显式指定 |
| `MOECT_EXE` | Moect.exe 路径，仅 GUI 回退模式需要（headless 模式不用） |
| `HOST` / `PORT` | `PORT` 是**采集器推送目标端口**，需与实际服务端口一致；网页服务端口由启动脚本决定（见下） |

LLM 降级链：多模型重试 → JSON 稳健解析 → 本地规则摘要。**任何一环失败都不影响主流程**：标签失败退化为群名、截止解析失败退化为规则、摘要失败不发摘要——绝不会因为 LLM 挂了就不整理、不发信。

---

## 邮件通知机制

邮件的核心价值是**筛选和聚合**，不是转发消息。三条路径都由 `src/messagehub/digest.py` 驱动，共用一个整理水位，互不重叠：

| 路径 | 触发时机 | 邮件标题 |
|---|---|---|
| **唤醒补扫** | 服务启动（开机/重启）| `[MessageHub] 积压整理 …` |
| **每小时汇总** | 常驻期间每 1 小时 | `[MessageHub] 每小时汇总 …` |
| **紧急加急** | 每 3 分钟扫到即发 | `[MessageHub] ⚡加急 …` |

### 1 + 2. 整理邮件（补扫 & 每小时汇总，共用一套逻辑）

补扫在服务一启动（开机自启、手动启动、服务重启都算）时跑一次；之后常驻期间每 1 小时跑一次汇总。两者走完全相同的整理流程，只是时间窗口不同：

1. 读整理水位 `runtime/digest_state.json` 里的 `last_digest_at`，收集这之后的所有工作事件（首次运行回溯最近 3 天）
2. 排除已单独加急发过的事件，避免同一件事既加急又汇总、打扰两次
3. 提取事件 + 闭环检测，把「已处理」的折到邮件末尾供回顾，不混进待办
4. LLM 给每条待办打语义标签（课程作业 / 报名截止 / 会议安排…），同类聚一组，再生成一段 80 字以内的人话摘要
5. 发一封 HTML 邮件，按标签分组展示；**发信成功才推进水位**，没有未闭环事件就静默不发

因为共用一个水位：开机补扫先把「关机 → 开机」这段消化掉并把水位推到当下，之后每小时汇总接着消化「这一小时 → 现在」的新增——**既不重复也不漏**。电脑连续开几天不重启，中途的普通事也会每小时送达。水位 `digest_state.json` 跟随数据库路径，测试库有自己的一份，绝不污染生产水位。

### 3. 加急邮件 + 桌面通知（插队）

服务运行期间每 3 分钟扫一次近 3 天的事件，**三个条件同时满足**才加急：

| 条件 | 判定 |
|---|---|
| 直接cue | `@你` 或直呼你 |
| 强指派 | 任务语义分 ≥ 2 且是 assignment 类 |
| 临近截止 | LLM 把截止原文解析成具体时间，且落在当前 48 小时内 |

三者齐全基本等于「有人明确把一件有时限的事交给你了」。放宽任何一条都会让加急变成骚扰，两周你就会养成忽略这个邮箱的习惯——所以**宁可漏一封，不可滥一封**。加急不限数量，每条按 `message_id` 去重（同一条只发一次），邮件和 Windows 桌面通知双发。已加急的事件会被记入去重表，之后不再进补扫/汇总。没配 LLM 时用规则降级：只认「今天 / 明天」这类高置信截止。

### 关于「快照 vs 增量」

整理邮件是**增量汇总**：每封只装「上次整理水位 → 现在」这段的新增事件，推进水位后这段就不会再出现。同一件事没完成前只在它产生的那封邮件里出现一次，完成后进「已处理」，不会反复提醒——不是逐条消息转发，也不是每封都重复全部待办。你不用翻聊天记录，按时间顺序看邮件就能跟上新事。

### 手动触发（调试用）

```bash
# 前台跑一次补扫并发信（会真实发邮件、推进水位）
PYTHONPATH=src .venv/Scripts/python.exe -c "from messagehub import digest; print(digest.run_catchup())"

# 只预览不发送、也不推进生产水位
PYTHONPATH=src .venv/Scripts/python.exe -c "from messagehub import digest; print(digest.run_catchup(send_mail=False))"
```

> 注意：`send_mail=False` 时仍会推进**当前数据库路径下**的水位。想预览真实库又不污染水位，先备份 `runtime/digest_state.json`，看完再还原。

---

## 工作事件提取规则

规则在 `src/messagehub/rules.py`，纯正则 + 打分，不依赖 LLM。

### 事件类型

| 类型 | 触发条件 |
|---|---|
| `assignment` | 指派给你：`@你` + 任务动词，或直呼 + 任务动词，或上下文祈使句 |
| `reply_needed` | `@你` + 疑问句，需要你回答 |
| `notice` | 时间、地点、签到码、报名通知——不需要你回复但你需要知道 |

### 任务语义打分

| 分数 | 信号 |
|---|---|
| +2 | 明确指派：`请/麻烦/辛苦/帮我/记得/提交/完成/填写/盖章…`；祈使产出物 `弄个/搞个/写个 + 宾语`；把字句 `把X发我/交上来` |
| +1 | 弱任务词，但必须与截止词同现：`准备/整理/发送` + `截止/之前/前交` |
| +1 | 截止压力：`截止/截至/ddl/最迟/最晚/前提交` |

### 混杂群的双条件

工作群里的通知可以直接报；混杂群（同学群、生活群）不行——那里天天有人说话。所以混杂群必须同时满足：

1. 明确 cue 到你（`@你` 或直呼你）
2. 任务语义分 ≥ 1

再叠加玩梗排除：一句话里出现多个 `@` 且没有任务语义，判定为玩梗（`@12306 @你 @10086竭诚为您服务`）。

### 上下文指向

派活经常不点名——「你们弄个策划案出来」，这是说给正在对话的人听的。判定方法是看这条消息之前的 6 条里，发送者和你是否**正在**互相说话：

- 条数近：前 6 条内
- 时间近：都在 10 分钟内

隔了几小时的旧发言不算对话上下文，避免把派给别人的活认成你的。

### 闭环检测

事件发生后，在同群后续 15 条消息内找你的发言，按词表分类：

| 状态 | 例子 |
|---|---|
| `answered_ok` 已应承/已交付 | 好 / 行 / 可以 / 收到 / 没问题 / 我去；**直接把文件发过去**（[文件]/[图片]）、「已经发群里了」「pdf 发过去了」「文档见上条」 |
| `answered_no` 已婉拒 | 不行 / 没空 / 去不了 |
| `answered_alt` 改期 | 明天晚上应该可以 |
| `unanswered` 未答复 | 窗口内没有你的发言 |

已应答的事件不再进「今日待办」，只进「今日进展」。这是这套东西能天天用下去的关键——**不会反复提醒你已经处理过的事**。

词表覆盖不了的说法还有第二道保险：配置了 LLM 时，每小时汇总前会对仍判为「未答复」的事件做一次**语义复核**——把事件原文和之后的群聊上下文交给 LLM 判断「这件事是否已经不需要你做什么」。判为办结的同样归入已处理；LLM 失败或解析不出就维持原判（宁可多提醒，不假装已办）。

### 版本更新提示

已安装的实例会自己检查上游是否有新版本：打开首页时后台查一次 GitHub（结果缓存 1 小时，失败静默不影响使用）。发现有新版时页首出现一条可关闭的横幅，提示当前版本、最新版本和更新命令（`git pull` + 重启服务）。

- 上游发了 Release → 按语义化版本比较，只在新版本**更高**时提示
- 没发过 Release → 回退比较 main 分支最新 commit，有新提交就提示
- 手动强查：`curl http://127.0.0.1:8000/api/version?refresh=1`
- 发新版本的流程：改 `src/messagehub/__init__.py` 的 `__version__` → push → GitHub 发 Release（tag 如 `v0.1.1`），装出去的实例下次打开首页就能看到

---

## 页面与 API

### 页面

| 路径 | 用途 |
|---|---|
| `/` | 工作台：消息总数、待办数、今日各群消息量、采集状态 |
| `/brief` | 每日简报：三栏 + 闭环进展，每条可溯源到原文 |
| `/live` | 实时消息流、采集状态、接入示例 |
| `/memories` | 去年今日 |

### 主要接口

```
GET  /api/health                              健康检查
GET  /api/version?refresh=1                   本地 vs 上游版本比对（refresh 强制重查）
POST /api/import                              上传 JSONL/JSON/CSV/TXT 导入
POST /api/scan                                扫描 imports/ 目录并自动提取任务
GET  /api/capture-status                      采集状态

GET  /api/live/status                         采集器状态
POST /api/live/ingest                         标准消息接入点（批量）
POST /api/live/inject                         单条注入（调试用）
GET  /api/live/stream                         SSE 实时流
POST /api/live/start | /stop | /poll          采集器控制
POST /api/live/configure                      配置采集源

GET  /api/brief?date=2026-11-18               当日简报
GET  /api/brief/dates                         有简报的日期列表
GET  /api/brief/event/{id}/context            事件原文及前后文
POST /api/brief/reset                         重设基线（历史清零）

GET  /api/memories?date=2025-11-14            去年今日
GET  /api/memories/groups                     当天活跃群
POST /api/memories/summarize                  AI 总结

GET  /api/tasks                               任务列表
GET  /api/tasks/{id}/evidence                 任务证据链
GET  /api/messages/search?q=关键词             全文检索
```

---

## 数据来源适配器

`src/messagehub/adapters/registry.py` 是表驱动的解析器注册表，`detect_and_parse` 按文件特征自动选择：

| 适配器 | 输入 |
|---|---|
| MessageHub 标准 | 本项目格式的 JSON / JSONL |
| Moect | Moect 导出的 JSON / JSONL（启发式字段映射） |
| QQ Chat Exporter | V5 JSON |
| 通用 CSV / TXT | 每行一条消息文本 |

所有解析器返回统一的 `RawMessage` 字典，字段对齐数据库 schema。

**去重指纹**（`pipeline/importer.py` 的 `make_fingerprint`）是全项目唯一实现：

- 有平台消息 ID → 用 `id::平台::ID`
- 没有 → 内容指纹 `平台|会话|发送者|时间(精确到分钟)|正文` 的哈希

同一文件导入三次不会膨胀，跨通道重复消息也能识别。

---

## 排错 FAQ

**1. 简报里什么都没有 / 没有生成待办**

第一大原因：`config\personal.json` 没填你的身份和工作群。程序不知道「谁是你」「哪些群算工作群」，就不会提取任何事件。用记事本打开该文件，填好 `identity` 和 `groups.work`，重启服务。

**2. 双击 setup.bat 说找不到 Python**

- 安装 Python 时没勾 **Add python.exe to PATH** → 重新运行安装包勾选，或用 `winget install Python.Python.3.12`
- 装好了但旧 cmd 窗口不认识 → **关掉所有 cmd 窗口，开新窗口再试**（PATH 只在新进程生效）

**3. pip 安装依赖失败 / 超时**

国内网络较慢或被代理拦截。打开 cmd 进入项目目录，改用清华镜像手动装：

```bat
.venv\Scripts\pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
```

装完重新双击 setup.bat，它会跳过已完成的步骤继续。

**4. 双击 setup.bat 窗口一闪而过**

正常情况下报错会停住等你按键。如果真闪退了：打开 cmd，把 setup.bat 拖进窗口回车运行，就能看到报错内容。

**5. 启动后浏览器页面打不开**

- 服务启动要几秒，等一下再刷新
- 看「启动 MessageHub.bat」窗口里有没有红色报错
- 手动在浏览器输入 `http://127.0.0.1:8000/` 试试

**6. 提示端口 8000 被占用**

可能是上一个 MessageHub 没退干净，或别的软件占了 8000。查看占用：

```bat
netstat -ano | findstr :8000
```

不想折腾就换个端口跑：用记事本打开「启动 MessageHub.bat」，把里面所有的 `8000` 改成 `8001`（共 5 处，网页地址与启动命令都要改），保存后重新双击。同时把 `.env` 里的 `PORT=8001` 也改掉——采集器按它决定推送目标。

**7. Moect 找不到 / 快照找不到 / 密钥解不开**

见 [Moect 集成 → 常见失败与处理](#常见失败与处理)。

**8. 杀毒软件报 setup.bat / 启动脚本有风险**

脚本只做「装依赖 + 复制配置模板 + 启动本地服务」，不联网上传任何数据。可以在杀毒软件里添加信任，或打开 cmd 手动执行里面的命令。

**9. 想彻底重来**

```bat
rmdir /s /q .venv
del .env
del config\personal.json
```

然后重新双击 setup.bat。注意：这不会删除你的消息数据库（在 `runtime\` 里）。

**10. 点了「去年今日」的 AI 摘要或「检测范围」的 AI 分级后，整个网页卡死**

v0.1.0 及更早版本的 bug：这两个接口在请求线程里同步调 LLM（超时重试链最长十几分钟），期间所有请求（包括采集入库）全部排队超时。已在 2026-11-19 修复（三处调用挪进线程池）。如果你还在旧版本，`git pull` 后重启服务即可。

**11. 开机自启没生效 / 想取消**

验证是否安装成功（PowerShell）：

```powershell
(Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run").MessageHub
Get-ScheduledTask -TaskName "MessageHubHeadless","MessageHubWatchdog" | Select-Object TaskName,State
```

Run 键输出为空 = 没装上，按[第 8 步](#第-8-步可选开机自启开机后零操作)重装。取消：删 Run 键 + 停用两个计划任务（`Disable-ScheduledTask -TaskName ...`）。

**12. 没收到邮件**

按顺序查：

1. `.env` 的 SMTP 五项是否填齐（`BRIEF_TO` 是收件箱，`SMTP_PASS` 是授权码不是登录密码）
2. 服务日志有没有 `[digest] 唤醒补扫` / `[digest] 每小时汇总` / `[brief-mail] 已发送` 字样
3. `runtime/digest_state.json` 是否存在——不存在说明服务没跑起来，或补扫线程报错
4. 是不是「无事静默」：没有未闭环事件时按设计不发信，收件箱里没有邮件不代表坏了

**13. 加急邮件没触发**

加急要求三个条件**同时**满足（直呼 + 强指派 + 48h 内截止），任一不满足都不会发——这是刻意设计的防骚扰门槛。另外加急需要 LLM 能解析出具体截止时间；没配 LLM 时只认「今天 / 明天」这类高置信说法。日志里加急检查会写明每条为什么没通过（如「截止偏远」「非强指派」）。

**14. 电脑连续开好几天不重启，中途的工作消息会漏吗？**

不会。开机补扫只负责消化「关机 → 开机」那段；之后常驻期间每 1 小时自动汇总一次新增的普通工作事件（邮件标题为「[MessageHub] 每小时汇总 …」），急事则随时插队。三条路径共用同一个整理水位，消化过的不会再发。所以哪怕一直不关机，该知道的事也会持续送到邮箱。

---

## 测试

```bash
pytest -q
```

122 项测试，全部隔离在临时库，**绝不碰生产数据**。

测试用 `tests/fixtures/personal_test.json` 这套虚构身份（真名「张三」、群名「工作群甲」），不读你的 `config/personal.json`。所以测试结果不会随个人配置漂移，别人 clone 下来直接能跑。

安全护栏：如果 `MESSAGEHUB_DB_PATH` 指向生产库，`conftest.py` 直接拒绝启动——历史上真发生过 pytest 清空 21 万条真实消息的事故。

---

## 安全边界

| 原则 | 实现 |
|---|---|
| **不向微信发任何消息** | 全程只读，不操作微信界面，没有任何发送消息的接口 |
| **不开 GUI、不置前** | headless 桥直接调源码，无窗口；计划任务用 `pythonw.exe` 静默运行 |
| **不改原始数据库** | 只读 Moect 产出的明文快照，不改微信自己的库 |
| **只监听回环** | 默认 `127.0.0.1:8000`，不对外暴露 |
| **只采群聊** | 默认跳过单聊，`--include-private` 才放开；私聊分级见配置 |
| **密钥只在本机** | headless 桥从微信进程内存提取的解密凭据只写入本机 `runtime/moect_data/`，不打印、不进日志、不上传 |
| **邮件是唯一外发通道** | 只发到你 `.env` 里配置的 `BRIEF_TO` 收件箱，内容是整理后的待办，不是原始聊天记录 |
| **敏感文件隔离** | `.env`、`config/personal.json`、`runtime/`、`imports/` 全部 gitignore |
| **提交前拦截** | `scripts/install_hooks.py` 装 pre-commit 钩子，拦 `.env`、密钥、含聊天正文的产物 |

**你的聊天正文不会离开这台机器。** 外部调用只有两处，都可关：① 配了 LLM 时，整理/加急判定会把事件文本片段（不含完整上下文）发给你自己配置的接口；不想要就把 `LLM_*` 留空，走本地规则降级。② 配了 SMTP 时，整理后的待办摘要发到你的邮箱——这是主动设计的功能，不配则不发。

---

## 已知限制

- **准实时，不是毫秒推送**。快照刷新间隔 5 分钟，采集间隔 120 秒；加急检查每 3 分钟。每轮刷新会搬运约 1.5 GB 的快照文件，别把间隔设太短。
- **headless 桥依赖一份单独的 Moect GPL 源码**（安装脚本会自动 clone，不随本项目分发）。源码位置可自动探测或用 `.env` 的 `MOECT_SRC` 指定。升级 Moect 版本时需检查教程门控常量（`TUTORIAL_REVISION`）是否变化。
- **Moect 通道仅 Windows**。macOS / Linux 只能走 `/api/live/ingest` 或文件导入。
- **加急判定偏保守**。三个条件同时满足才发，缺 LLM 时只认「今天 / 明天」——可能漏掉一些其实挺急但表述模糊的事。这是刻意的取舍：宁可漏一封，不可滥一封。
- **邮件依赖外部 SMTP / LLM**。没配 LLM 时标签退化为群名、加急截止只走规则；没配 SMTP 时完全不发邮件，只能在网页看。
- **QQ 明文通道未接**。QQ NT 的库是 SQLCipher 加密，需要可用的上游工具。
- **规则引擎是启发式的**。中文表达太灵活，总有漏判和误判。规则集中在 `rules.py`，可以直接改正则。

---

## 项目结构

```
MessageHub/
├── 启动 MessageHub.bat          一键启动（自动探测 Python）
├── setup.bat                    一键安装入口（双击后调起 setup.ps1）
├── setup.ps1                    安装向导主逻辑（PowerShell，UTF-8 BOM）
├── config/
│   ├── personal.example.json    个人配置模板
│   └── personal.json            你的配置（gitignore）
├── src/messagehub/
│   ├── app.py                   FastAPI 入口，只组装路由与 lifespan
│   ├── config.py                路径 / 时区 / 群名映射
│   ├── personal.py              个人身份与工作群配置加载
│   ├── database.py              SQLite schema（唯一定义点）
│   ├── rules.py                 工作事件提取引擎
│   ├── resolution.py            闭环检测
│   ├── brief.py                 网页简报生成与落库（按需，不自动发信）
│   ├── digest.py                ★ 整理引擎（补扫/汇总共用、加急分流、LLM 标签 / 截止解析 / 水位去重）
│   ├── schedule.py              后台调度（启动补扫一次 + 每 1 小时汇总 + 每 3 分钟加急）
│   ├── mail.py                  邮件桥（整理邮件 / 加急邮件 / 旧网页简报）
│   ├── desktop_notify.py        Windows 桌面通知（加急时用）
│   ├── adapters/registry.py     导出格式解析器注册表
│   ├── pipeline/
│   │   ├── importer.py          统一导入与指纹去重
│   │   ├── live.py              实时采集器（tail / uia / ingest 三源）
│   │   ├── watcher.py           目录扫描与增量状态
│   │   └── extract.py           规则 + LLM 任务提取
│   ├── routers/                 tasks / memories / live / brief / scope
│   ├── llm/provider.py          OpenAI 兼容客户端与降级链
│   └── templates/               页面模板
├── scripts/
│   ├── moect_headless.py        ★ headless 快照桥（直接调 Moect 源码 Service.connect）
│   ├── moect_live.py            采集桥（只读快照、单实例锁、增量水位）
│   ├── watchdog.ps1             看门狗：保活 API + 采集器（计划任务每 5 分钟）
│   ├── autostart_silent.vbs     静默启动器（注册表 Run 键）
│   ├── db_bridge.py             备用桥接（chatlog HTTP / 已解密 SQLite / JSONL）
│   ├── push_message.py          命令行推送
│   ├── build_memories_report.py 生成离线「去年今日」HTML
│   ├── install_hooks.py         安装 pre-commit 钩子
│   ├── install_autostart.bat    注册表 Run 键安装（登录即启动服务）
│   └── install_scheduled_tasks.ps1 ★ 一键装 Moect 源码+依赖+双计划任务（任意目录可用）
├── tests/                       122 项测试
├── imports/                     你的导出文件（gitignore）
└── runtime/                     数据库 / 日志 / 快照 / digest 水位（gitignore）
```

---

## 贡献

Issue 和 PR 都欢迎。改规则引擎前先跑 `pytest -q`，122 项测试锁住了已知的所有判读案例。

复现环境直接双击 `setup.bat`（或看上面的[快速开始（开发者）](#快速开始开发者)）。

提交前装一下钩子，它会拦住误提交的密钥和聊天数据：

```bash
python scripts/install_hooks.py
```

---

## License

[MIT](LICENSE)

第三方组件完整清单见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。主要依赖：

- [Moect](https://github.com/ILoveScratch2/Moect) — 微信数据库解密，独立项目（GPL-3.0），本项目不包含其代码
- [FastAPI](https://github.com/tiangolo/fastapi) / [Starlette](https://github.com/encode/starlette) — MIT / BSD
- [zstandard](https://github.com/indygreg/python-zstandard) — BSD

---

**免责声明**：本工具仅供个人备份与整理自己的聊天记录。请遵守当地法律法规与平台服务条款，不要用它采集他人数据。聊天内容属于隐私，请勿公开分享导出的数据。
