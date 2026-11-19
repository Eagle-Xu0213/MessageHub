# AGENTS.md — MessageHub AI 安装手册

> **这份文件是写给 AI 助手的，不是写给人的。**
> 你（AI 助手）正在帮一位用户把 MessageHub 安装到 TA 的 Windows 电脑上。
> 本文件是你的完整执行手册：按章节顺序执行、每步验收、失败时按 §8 排错表处理。
> 人读的文档是 [README.md](README.md)；两者冲突时以本文件为准（本文件针对 AI 执行做了防呆）。

---

## 0. 你的工作方式

先判断你属于哪种，再开始：

| 模式 | 特征 | 做法 |
|---|---|---|
| **本地 Agent** | 你能在用户电脑上直接执行命令（如 Claude Code、Cursor、WorkBuddy 桌面版） | 直接按本手册执行命令，每步验收后继续 |
| **远程对话 AI** | 你只能输出文字（如网页版 ChatGPT / Claude） | 把命令**逐条**发给用户执行，让用户把输出粘贴回来，你核对后再给下一条。一次只发一条 |

通用纪律：

- **不要跳步**。每一步末尾的 ✅ 验收通过才进下一步。
- **不要替用户编造配置值**。§1 的问题没问到答案，就停下来问。
- **隐私红线**：用户的身份信息（昵称、真名、群名）只写入本机 `config/personal.json`，不发送到任何外部服务，不写进你的回复日志之外的地方。
- 本项目**全部本地运行**，唯一的对外网络调用是用户自己配置的 LLM API 和 SMTP 邮件，安装过程不涉及。

---

## 1. 安装前必问（一次性问完）

先向用户确认以下内容，拿到答案再动手。**合并成一次提问**，不要挤牙膏式追问。

### 必答（不答装不了完整功能）

1. 你的电脑是 **Windows 10/11** 吗？
   - 是 → 完整安装（含微信实时采集选项）
   - 否（macOS / Linux）→ 只能**手动导入模式**：跳过 §6（Moect 仅 Windows），其余步骤相同，开机自启改用系统常规方法（systemd / launchd），本手册 §7 不适用
2. 你的**微信昵称**是什么？（群里的 `@你` 靠它匹配，可多个，逗号分隔）
3. 你的**真实姓名**？（匹配「@班级+张三」这类群昵称格式）
4. 你自己在群里**发言时显示的名字**？（用于判断"这事你回过没有"，通常就是昵称）
5. 你的**工作群名**有哪些？（**精确名字**，和微信里显示的一字不差，逗号分隔；只有这些群会被提取任务）

### 可选（默认跳过，用户没提就不配）

6. 要**微信实时采集**吗？（每天自动从微信群拉新消息 → 需要装 Moect，前提：Windows + 微信桌面版 4.x 已登录）→ 要则走 §6
7. 要**简报自动发邮件**吗？→ 要则请用户提供：SMTP 服务器、端口、发件邮箱、SMTP 授权码（不是登录密码）、收件邮箱，填入 `.env` 的 `SMTP_*` / `BRIEF_TO`
8. 有 **LLM API** 吗？（简报 AI 摘要用，OpenAI 兼容接口）→ 有则填 `.env` 的 `LLM_*`；没有就留空，自动降级本地规则，功能不受影响

---

## 2. 环境预检

依次执行并核对：

```bat
python --version
```

- 输出 `Python 3.11` 及以上 → 通过
- `python` 不是内部命令或弹 Microsoft Store → 试 `py -3 --version`；有 3.11+ 则**后续所有 `python` 换成 `py -3`**
- 都没有 → 执行 `winget install Python.Python.3.12`，装完**必须开新的终端窗口**（PATH 只对新进程生效）再验证
- `winget` 也没有 → 让用户从 <https://www.python.org/downloads/> 下载安装，**务必勾选 "Add python.exe to PATH"**

```bat
git --version
```

- 没有且你是本地 Agent → `winget install Git.Git`，开新窗口验证
- 远程对话 AI 且用户不想装 Git → 备选：浏览器打开 `https://github.com/Eagle-Xu0213/MessageHub/archive/refs/heads/main.zip` 下载解压（效果等同 clone）

⚠️ **常见坑**：装完 Python/Git 后在旧窗口里验证会失败。所有"装完就验证"都必须**新开终端窗口**。

---

## 3. 获取代码

```bat
cd /d %USERPROFILE%\Desktop
git clone https://github.com/Eagle-Xu0213/MessageHub.git
cd MessageHub
```

装到桌面便于用户找到；用户指定了其他目录就听用户的。后面所有命令都在**项目根目录**执行。

✅ 验收：目录里有 `setup.bat`、`README.md`、`src\`。

---

## 4. 安装依赖与配置（非交互路径）

> ⚠️ **不要直接运行 `setup.bat`**。它是给"人双击"设计的：会弹出记事本等人编辑配置、用 `Read-Host` 等人回答是否装 Moect。AI 执行会卡死在交互上。走下面的等效非交互路径。

### 4.1 创建虚拟环境并装依赖

```bat
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e .
```

- pip 安装失败（网络超时/被代理拦截）→ 改用清华镜像重试：
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

按 §1 可选项 7/8 的答案，用文本编辑写入 `.env` 对应变量（文件里每项都有注释说明）。用户没配的可选项保持模板原样即可，**全部可留空**。

### 4.3 写入个人配置（关键步骤，装错 = 简报永远空白）

把 §1 必答题 2-5 的答案填进 `config/personal.json`。文件已由模板提供结构，只需替换占位值：

```json
{
  "identity": {
    "nicknames": ["<必答2：微信昵称，可多个>"],
    "real_name": "<必答3：真名>",
    "short_name": "",
    "sender_names": ["<必答4：自己发言的显示名>"]
  },
  "groups": {
    "work": ["<必答5：工作群名，精确匹配微信显示名>"],
    "watch": [],
    "excluded": []
  },
  "contacts": { "work": [], "watch": [], "excluded": [] }
}
```

说明：

- `contacts` 三级分层是**私聊**采集白名单：`work` 级联系人私聊非噪声即报；`watch` 级需带任务语义；**没勾的联系人私聊完全不采集**（保守默认）。用户想监控某人的私聊就填进 `work`
- `short_name` 是"别人直呼你"的简称（如姓名去掉姓），用户想要直呼匹配才填
- 群名/联系人名必须和微信里显示的**一字不差**，模糊匹配不存在

✅ 验收：

```bat
.venv\Scripts\python -c "import json; d=json.load(open('config/personal.json', encoding='utf-8')); print('identity:', bool(d['identity']['nicknames']), '| groups.work:', d['groups']['work'])"
```

输出里昵称非空、工作群列表非空。

---

## 5. 启动与验收

### 5.1 启动服务（按你的执行环境二选一）

**本地 Agent 且支持后台任务管理**（WorkBuddy / Claude Code 后台 shell 等）：

```bat
.venv\Scripts\python -m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000 --log-level warning
```

作为**受管后台任务**运行（不要 `&` 挂后台——会随会话退出而死）。

**没有后台任务能力 / 给用户长期用**：让用户双击项目根目录的 `启动 MessageHub.bat`（弹一个窗口，关窗口即停服务），或：

```bat
start "" "启动 MessageHub.bat"
```

> 这个 bat 是幂等的：服务已在跑时不会重复启动，只会打开页面。它同时会把采集器挂到后台（如果已装 Moect）。

### 5.2 验收

```bat
curl -s -m 5 http://127.0.0.1:8000/api/health
```

✅ 输出 `{"status":"ok","version":...}`。再让用户浏览器打开 <http://127.0.0.1:8000/>，能看到工作台页面即成功。

失败 → §8 排错表第 1-3 行。

---

## 6. 可选：微信实时采集（Moect）

跳过条件：§1 问了、用户说不要；或用户不是 Windows。跳过时直接告诉用户：把聊天导出文件（JSONL/CSV/TXT）丢进项目 `imports\` 目录，网页点「扫描导入」即可，其余功能一样。

### 6.1 安装 Moect

```bat
winget install Moect
```

winget 失败 → 从 <https://github.com/ILoveScratch2/Moect/releases> 手动下载。

### 6.2 前提校验（这一步不通过，后面全白搭）

- 微信桌面版**已登录**且是 **4.x** 版本
- 让用户**手动运行一次 Moect**（开始菜单找 Moect），确认界面能解密并生成快照

✅ 验收（快照存在）：

```bat
dir "%LOCALAPPDATA%\Moect\archives"
```

能看到 `snapshot_*` 子目录。

### 6.3 让采集器找到 Moect

采集器自动探测：环境变量 `MOECT_EXE` → 项目 `.env` 的 `MOECT_EXE` → `%LOCALAPPDATA%\Programs\Moect\Moect.exe` → Program Files 常见位置。装在别处时在项目 `.env` 加一行：

```ini
MOECT_EXE=C:\实际路径\Moect.exe
```

✅ 验收（环境自检，逐项检查 Moect/快照/服务/依赖）：

```bat
.venv\Scripts\python scripts\moect_live.py --check
```

全部 ✓ 才继续。有 ✗ 按输出提示修。

### 6.4 首次采集 + 常驻

```bat
.venv\Scripts\python scripts\moect_live.py --once
```

成功标志：日志出现 `统计: 扫描=... 推送=... 入库=...`。然后常驻：

```bat
.venv\Scripts\python scripts\moect_live.py --loop --interval 120
```

同样按 §5.1 的原则放**受管后台任务**，或让用户直接双击 `启动 MessageHub.bat`（它自动把采集器挂后台）。

⚠️ 细节：每轮采集会短暂启动 Moect 并搬运约 1.5 GB 快照文件（同盘移动），别把 interval 设低于 60；采集器有单实例锁（端口 8765），起两个会自动退一个。

---

## 7. 可选：开机自启

> 仅 Windows。效果：登录后自动静默启动服务 + 采集器 + 简报调度，简报有实质内容自动发邮件，全程零操作。不需要管理员权限。

**本地 Agent** 直接执行（PowerShell）：

```powershell
$VBS = "<项目根目录绝对路径>\scripts\autostart_silent.vbs"
New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "MessageHub" -PropertyType String -Value "wscript.exe `"$VBS`"" -Force | Out-Null
(Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run").MessageHub
```

（`<项目根目录绝对路径>` 换成实际值，如 `D:\MessageHub`。）

**远程对话 AI**：把上面 3 行发给用户在 PowerShell 里粘贴执行。

✅ 验收：最后一条命令输出 `wscript.exe "...autostart_silent.vbs"` 即成功。

取消自启：

```powershell
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "MessageHub"
```

> 也可以双击 `scripts\install_autostart.bat` 安装（效果相同）。但该 bat 含中文注释，个别终端有编码乱码问题——**优先用上面的 PowerShell 命令**。
>
> 原理：写当前用户注册表 Run 键指向 `autostart_silent.vbs`。启动器幂等：服务和采集器已在运行时（8000 端口占用 / 8765 单实例锁）自动退出，不会起两份。

---

## 8. 排错速查表

| # | 现象 | 原因 | 处理 |
|---|---|---|---|
| 1 | `/api/health` 连不上 | 服务没起来 / 端口被占 | 看 uvicorn 输出；`netstat -ano \| findstr :8000` 查占用，被占则让用户换端口（bat 里改 8000→8001 共 5 处 + `.env` 的 `PORT=8001`），或杀掉占用进程 |
| 2 | `pip install` 失败 | 网络/代理 | §4.1 的清华镜像命令 |
| 3 | `import messagehub.app` 报错 | 依赖没装全 / .venv 损坏 | 重跑 §4.1；仍不行删 `.venv` 目录重来 |
| 4 | 简报是空的 | `personal.json` 没填或群名不精确 | §4.3 重写；群名必须和微信显示**一字不差** |
| 5 | `--check` 显示 Moect ✗ | 非标准安装位置 | `.env` 写 `MOECT_EXE=完整路径` |
| 6 | Moect 解不开密钥 | 微信没登录 / 刚升级 | 登录微信桌面版并保持运行；重启微信后重跑 Moect；确认微信是 4.x |
| 7 | 采集器报连不上 127.0.0.1:8000 | 服务没启动 | 先起服务再跑采集 |
| 8 | AI 摘要/分级接口把服务卡死（全部请求超时） | 旧版本 bug | `git pull` 更新后重启服务（2026-11-19 已修复） |
| 9 | 杀毒软件报脚本有风险 | 误报 | 脚本只装依赖+启动本地服务，不联网上传；加信任即可 |

排不掉的问题让用户带完整报错去 <https://github.com/Eagle-Xu0213/MessageHub/issues> 提 Issue。

---

## 9. 完成后告诉用户的话

安装收尾时，用一段话向用户交代（远程 AI 让用户保存这段话）：

1. **日常入口**：<http://127.0.0.1:8000/> 工作台；`/brief` 每日简报；`/live` 实时消息流；`/memories` 去年今日
2. **简报节奏**：每天 8 点自动生成 + 每小时滚动检查，有新工作事件才更新；配了邮件就自动送达邮箱
3. **数据在哪**：全部在本机项目目录 `runtime\` 里，不上传任何服务器；卸载 = 删项目文件夹
4. **怎么停**：关掉「启动 MessageHub.bat」的窗口（装了自启则任务管理器结束 python 进程）
5. **改配置**：随时用记事本编辑 `config\personal.json`（加减工作群、调整私聊白名单），保存后下一轮采集自动生效，不用重启
6. **出问题**：README 末尾有完整排错 FAQ；`python scripts\moect_live.py --check` 是万能自检
