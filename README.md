# MessageHub

**把微信工作群变成一份每天早上的待办清单。**

本地运行的聊天消息中枢：接入微信真实群聊消息，用规则引擎提取「谁让你干什么、什么时候截止」，生成每日工作简报。所有数据只存在你本机的 SQLite 里，不联网、不上传、不发送任何消息。

> 核心思路：群消息不是用来读的，是用来**变成任务**的。21 万条历史消息里真正需要你动手的不到 1%，问题从来不是消息太多，而是没有过滤。

---

## 目录

- [能做什么](#能做什么)
- [它是怎么工作的](#它是怎么工作的)
- [零基础安装（Windows，从零开始）](#零基础安装windows从零开始)
- [快速开始（开发者）](#快速开始开发者)
- [Moect 集成（微信消息来源）](#moect-集成微信消息来源)
- [配置](#配置)
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
| **工作简报** | 每天 8 点自动生成三栏简报：今日待办 / 需你回复 / 通知。每条都挂着原文出处，可点开看上下文 |
| **事件提取** | 规则引擎判断哪些消息是在给你派活：`@你`、直呼你、上下文对话中的祈使句，配合任务语义打分 |
| **闭环检测** | 事件之后翻你的回复，已经答应的不再重复提醒。回了「明天可以」就标记为已改期，不会一直挂着 |
| **实时消息流** | 每 120 秒增量拉取新消息，SSE 推送到网页，指纹去重保证幂等 |
| **去年今日** | 按北京时间严格匹配一年前的同一天，回顾当时群里在聊什么 |
| **全文检索** | SQLite FTS5 索引，按群、时间、关键词查历史消息 |

---

## 它是怎么工作的

```
微信客户端
   │
   ├─ Moect（第三方解密工具，本机已登录微信）
   │     └─ 生成明文 SQLite 快照（zstd 压缩的消息内容）
   │            │
   │            ▼
   │      scripts/moect_live.py        ← 采集桥：触发刷新、解压、映射群名、增量读
   │            │
   │            ▼
   │      POST /api/live/ingest        ← 唯一稳定接入点，指纹去重
   │            │
   │            ▼
   │      SQLite（messages 表）─────────┐
   │            │                       │
   │            ▼                       ▼
   │      rules.py 事件提取        SSE → /live 实时流
   │            │
   │            ▼
   │      resolution.py 闭环检测
   │            │
   │            ▼
   │      brief.py 三栏聚合 → LLM 摘要（失败降级本地规则）
   │            │
   │            ▼
   └──── 每日简报 / 邮件推送
```

**为什么绕这么一圈**：微信 4.x 的消息区是自绘渲染层（`MMUIRenderSubWindowHW`），UIA 无障碍树里读不到任何正文，实测 63 个节点全是空壳。所以只能从数据库层取——而数据库是 SQLCipher 加密的，必须借助已经完成解密的工具。

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

### 第 6 步（可选）：要用微信实时采集，安装 Moect

setup.bat 已经问过你了——回答 `y` 会自动执行下面的安装。跳过或当时选了 `n` 的话，之后随时可以补装：

```bat
winget install Moect
```

装完后先**手动运行一次 Moect**，确认它能解密你的微信数据、生成快照。这一步不通过，后面都白搭。详细步骤见 [Moect 集成](#moect-集成微信消息来源) 章节。

> 不装也完全能用：把聊天记录导出文件（JSONL/CSV/TXT）丢进 `imports\` 目录扫描导入，其余功能一模一样。

### 第 7 步：双击「启动 MessageHub.bat」

浏览器会自动打开 <http://127.0.0.1:8000/>，工作台页面出现即成功。保持窗口开启即可使用，关闭窗口即停止服务。

之后每天的使用就是：**双击启动 → 看简报 → 干活**。

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

[Moect](https://github.com/ILoveScratch2/Moect) 是一个开源的微信数据库解密工具（Windows）。它读取微信本地的加密数据库，用你已登录的微信进程里的密钥解密，导出为明文 SQLite。

**本项目不包含、不分发 Moect**，只调用它已经生成的明文快照。你需要自己安装并让它正常工作。

### 复现步骤

#### 1. 安装并让 Moect 跑通

```bash
# 方式一：winget
winget install Moect

# 方式二：从 Releases 下载
# https://github.com/ILoveScratch2/Moect/releases
```

安装后**先手动运行一次 Moect**，确认它能解密你的微信数据、生成快照。这一步不通过，后面都白搭。

#### 2. 确认快照位置

Moect 默认把快照放在：

```
%LOCALAPPDATA%\Moect\archives\<账号ID>\snapshot_<时间戳>\
```

里面是 `message_*.db`（消息）、`contact.db`（联系人）等文件。

检查一下：

```bat
dir "%LOCALAPPDATA%\Moect\archives"
```

能看到 `snapshot_*` 目录就对了。

#### 3. 让 MessageHub 找到 Moect

`scripts/moect_live.py` 会按顺序自动探测：

1. 系统环境变量 `MOECT_EXE`
2. 项目根 `.env` 文件里的 `MOECT_EXE`（采集器自己会读取 `.env`）
3. `%LOCALAPPDATA%\Programs\Moect\Moect.exe`
4. `%PROGRAMFILES%\Moect\Moect.exe`
5. `%PROGRAMFILES(X86)%\Moect\Moect.exe`

**装在别处就必须显式指定**，在 `.env` 里加：

```ini
MOECT_EXE=C:\Tools\Moect\Moect.exe
```

`scripts/moect_live.py` 启动时会自动读取项目根的 `.env`。改完 `.env` 后无需重启采集器，下一轮采集生效。

先验证一下探测结果：

```bash
python scripts/moect_live.py --check
```

输出会告诉你找到了哪个 `Moect.exe`、快照目录在哪、能不能读到。如果显示「未找到」，就用上面的 `MOECT_EXE` 显式指定。

再跑一次只读试运行，看快照路径通不通：

```bash
python scripts/moect_live.py --once --no-refresh
```

`--no-refresh` 表示只读现有快照、不触发 Moect 刷新。这一步能跑通说明快照路径没问题，再进入下一步。

#### 4. 首次运行（关键）

第一次必须让 Moect 把密钥档案复制到项目的 data-dir：

```bash
python scripts/moect_live.py --once
```

脚本会做三件事：

1. 把 `%LOCALAPPDATA%\Moect\archives\*\keys.json` 和 `config.json` 复制到 `runtime/moect_data/`（**只复制文件，不读取密钥内容**）
2. 用 `Moect.exe --smoke 1 --data-dir runtime/moect_data` 触发一次刷新，生成新快照到项目目录内
3. 读快照 → 解压 zstd → 提取群聊文本消息 → POST 到 `/api/live/ingest`

成功的日志长这样：

```
[moect] 已启用隐藏桌面隔离：Moect 前台窗口不再出现在主桌面
[moect] Moect 已在隐藏桌面启动（用户不可见）
[moect] 快照合并：移动 12 个文件到固定目录（磁盘恒定一份）
[moect] 统计: 扫描=3000 非文本过滤=842 私聊跳过=511 候选=1204 推送=1204 入库=1204
```

#### 5. 常驻运行

```bash
python scripts/moect_live.py --loop --interval 120
```

或者直接双击 `启动 MessageHub.bat`，它会自动把采集器挂到后台。

### 命令行参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--once` | — | 跑一轮就退出 |
| `--loop` | — | 常驻循环 |
| `--interval` | 60 | 刷新间隔（秒） |
| `--since-hours` | 24 | 首次回溯多少小时 |
| `--limit` | 3000 | 单轮最多推送条数 |
| `--no-refresh` | off | 不触发 Moect 刷新，只读现有快照 |
| `--keep-snapshots` | off | 不清理旧快照 |
| `--include-private` | off | 同时采集单聊（**默认只采群聊**） |

### 几个实现细节（踩过的坑）

**单实例锁用回环端口**，不用锁文件。`127.0.0.1:8765` 被占用说明已有实例在跑，第二个进程直接退出——进程崩溃即释放，不会留下需要手动清理的僵尸锁文件。

**Moect 是 GUI 程序**，`--smoke` 模式也会弹窗，`SW_HIDE` 对它无效。所以脚本用 `CreateDesktopW` 建了一个隐藏桌面 `MessageHubHeadless`，把 Moect 的界面画在那里，你的主桌面完全无感。创建失败会回退普通启动，只是窗口会闪一下。

**刷新后必须杀进程**。`Moect --smoke` 生成快照后 GUI 不会自己退出，`subprocess.run` 会一直阻塞到超时。所以脚本改成轮询新快照出现 → 立即结束进程树。

**水位回看 5 分钟**。新消息落库可能乱序（`create_time` 相同或倒序），所以水位推进时回退 300 秒覆盖这种抖动。重复由 ingest 端指纹去重兜底，多推不会重复入库。

**快照恒定一份**。每轮生成的快照会被合并移动到 `runtime/moect_data/archives/current/`，旧的删掉。不清理的话每次刷新都是一个 1.5 GB 的目录，磁盘会炸。清理有路径白名单校验，**永不触碰** `%LOCALAPPDATA%\Moect\archives` 下你自己的快照。

### 常见失败与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `--check` 显示 Moect ✗ 未找到 | 装在非标准位置 | 按上面的方法在 `.env` 里写 `MOECT_EXE=完整路径` |
| Moect 解不开密钥 / 报密钥错误 | 微信未登录，或微信刚升级密钥变了 | 先登录微信桌面版并保持运行；重启微信后重跑 Moect；确认微信是 4.x 版本 |
| 快照目录是空的 | Moect 还没成功跑过一次解密 | 手动打开 Moect 界面完整跑一次导出，看到快照生成后再用本项目的采集器 |
| `dir "%LOCALAPPDATA%\Moect\archives"` 看不到 `snapshot_*` | 快照位置不在默认处 | 在 Moect 设置里确认导出位置，或检查 `%LOCALAPPDATA%\Moect` 是否存在 |
| 采集器报连不上 127.0.0.1:8000 | MessageHub 服务没启动 | 先双击「启动 MessageHub.bat」，等页面能打开再跑采集 |

排查顺序建议：先跑 `.venv\Scripts\python scripts\moect_live.py --check`，它会一行行告诉你哪项没就绪。

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
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 任意 OpenAI 兼容接口。不配则降级本地规则 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `BRIEF_TO` | 简报邮件推送，可选 |
| `MOECT_EXE` | Moect.exe 路径，可选（有自动探测） |
| `HOST` / `PORT` | `PORT` 是**采集器推送目标端口**，需与实际服务端口一致；网页服务端口由启动脚本决定（见下） |

LLM 降级链：多模型重试 → JSON 稳健解析 → 本地规则摘要。**任何一环失败都不影响主流程**，永远有输出，不会假装「今天没有工作信息」。

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
| `answered_ok` 已应承 | 好 / 行 / 可以 / 收到 / 没问题 / 我去 |
| `answered_no` 已婉拒 | 不行 / 没空 / 去不了 |
| `answered_alt` 改期 | 明天晚上应该可以 |
| `unanswered` 未答复 | 窗口内没有你的发言 |

已应答的事件不再进「今日待办」，只进「今日进展」。这是这套东西能天天用下去的关键——**不会反复提醒你已经处理过的事**。

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

---

## 测试

```bash
pytest -q
```

96 项测试，全部隔离在临时库，**绝不碰生产数据**。

测试用 `tests/fixtures/personal_test.json` 这套虚构身份（真名「张三」、群名「工作群甲」），不读你的 `config/personal.json`。所以测试结果不会随个人配置漂移，别人 clone 下来直接能跑。

安全护栏：如果 `MESSAGEHUB_DB_PATH` 指向生产库，`conftest.py` 直接拒绝启动——历史上真发生过 pytest 清空 21 万条真实消息的事故。

---

## 安全边界

| 原则 | 实现 |
|---|---|
| **不发送任何消息** | 全程只读，没有任何发送接口 |
| **不点击、不置前** | 不操作微信界面；Moect 跑在隐藏桌面 |
| **不改原始数据库** | 只读 Moect 已生成的明文快照 |
| **只监听回环** | 默认 `127.0.0.1:8000`，不对外暴露 |
| **只采群聊** | 默认跳过单聊，`--include-private` 才放开 |
| **密钥不落地** | 脚本只复制 Moect 的 `keys.json`，从不读取内容 |
| **敏感文件隔离** | `.env`、`config/personal.json`、`runtime/`、`imports/` 全部 gitignore |
| **提交前拦截** | `scripts/install_hooks.py` 装 pre-commit 钩子，拦 `.env`、密钥、含聊天正文的产物 |

**你的聊天记录不会离开这台机器。** 唯一的外部调用是可选的 LLM 摘要——如果配了 LLM，简报生成时会把事件文本（不含完整上下文）发给你配置的接口。不想要就把 `LLM_*` 全留空，走本地规则。

---

## 已知限制

- **准实时，不是毫秒推送**。延迟 = 刷新间隔（默认 120 秒）。每轮会短暂启动 Moect，有约 1.5 GB 的同盘文件搬运，所以别把间隔设太短。
- **Moect 通道仅 Windows**。macOS / Linux 只能走 `/api/live/ingest` 或文件导入。
- **QQ 明文通道未接**。QQ NT 的库是 SQLCipher 加密，需要可用的上游工具。
- **规则引擎是启发式的**。中文表达太灵活，总有漏判和误判。规则集中在 `rules.py`，可以直接改正则。
- **依赖 Moect 的稳定性**。Moect 版本更新可能改变快照结构，届时需要调整 `build_maps` 里的表名反查逻辑。

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
│   ├── brief.py                 简报生成与落库
│   ├── schedule.py              定时调度（8 点日报 + 整点滚动）
│   ├── mail.py                  简报邮件桥
│   ├── adapters/registry.py     导出格式解析器注册表
│   ├── pipeline/
│   │   ├── importer.py          统一导入与指纹去重
│   │   ├── live.py              实时采集器（tail / uia / ingest 三源）
│   │   ├── watcher.py           目录扫描与增量状态
│   │   └── extract.py           规则 + LLM 任务提取
│   ├── routers/                 tasks / memories / live / brief
│   ├── llm/provider.py          OpenAI 兼容客户端与降级链
│   └── templates/               四个页面
├── scripts/
│   ├── moect_live.py            ★ Moect 采集桥（单实例锁、隐藏桌面、增量水位）
│   ├── db_bridge.py             备用桥接（chatlog HTTP / 已解密 SQLite / JSONL）
│   ├── push_message.py          命令行推送
│   ├── build_memories_report.py 生成离线「去年今日」HTML
│   ├── install_hooks.py         安装 pre-commit 钩子
│   └── install_autostart.bat    开机自启安装
├── tests/                       96 项测试
├── imports/                     你的导出文件（gitignore）
└── runtime/                     数据库 / 日志 / 快照（gitignore）
```

---

## 贡献

Issue 和 PR 都欢迎。改规则引擎前先跑 `pytest -q`，96 项测试锁住了已知的所有判读案例。

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
