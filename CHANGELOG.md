# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

## [2.1.1] - 2026-11-24

- 明确语义化版本的选位规则和版本声明同步流程。

## [2.1.0] - 2026-11-24

- 统一界面、通知和安装提示，修正已过时的文档说明。

## [2.0.2] - 2026-11-24

- 调整工作台首页的配色、字号与留白，保留便当盒布局和现有操作入口。
- 日历突出有待办到期的日期，消息采集卡增加状态标记。

## [2.0.1] - 2026-11-24

- 重新设计工作台，新增按待办截止日期查看的日历和活动页面自动刷新。
- 支持本机 OneBot 11 实时接收 QQ 群聊和好友消息，保留 QQ 文件导入。
- 微信与 QQ 独立设置工作、混合和未关注会话，支持按分级筛选及按可用活动时间排列 QQ 群。
- 修正微信快照发布与入库之间的交接，补充 QQ 连接诊断和 Agent 安装指引。

## [1.3.0] - 2026-11-23

- 首页引导用户设置微信身份、选择关注会话，并显示首次同步、同步延迟和采集失败等状态。
- 首页优先显示逾期和即将到期的待办，并可直达待确认、查看原文及撤销完成或忽略。
- 会话设置默认使用关注开关，已有分级继续可用。紧急邮件与每小时待办汇总可独立设置。

## [1.2.0] - 2026-11-22

本次将 v0.1.0 以来积压的改动正式发版，涵盖任务管线、检测范围、实时采集与工作台体验的完整升级。

### 移除

- 移除“去年今日”的页面、接口、离线报告生成及专用摘要代码；保留已有聊天数据和历史报告。下方旧版本记录仅供追溯。

### 新增

- **个人任务持久管线（task_pipeline / task_runtime）**：增量分批处理新消息，保守分类生成 `personal_tasks` 表（pending / accepted / in_progress / completed / review 五态），带截止时间解析与分类标签；后台调度器读快照完成标记（`snapshot_generation.json`）触发处理，替换旧 digest 调度成为默认任务流。`/api/personal-tasks` 系列接口 + 工作台任务面板与后台状态展示
- **快照代际标记**：headless 桥在 connect 完整成功且快照确实更新后原子写入 generation 文件，采集端以此判断「本轮快照可信完整」，替代此前靠时间戳猜的做法（`tests/test_moect_incremental.py` 覆盖）
- **检测范围按群 ID 绑定**：`personal.json` 新增 `group_ids` 段，/select 保存时对显示名唯一映射的 `@chatroom` 群落稳定 ID，群改名不再丢配置
- **桌面通知可点击直达任务**：Toast 通知带 `launch` 协议跳转工作台对应任务锚点；Toast 失败自动降级 NotifyIcon 气泡（同样可点击）；安装脚本注册开始菜单 `MessageHub Notifications` 快捷方式以提升通知来源可信度
- **测试 dotenv 隔离**：`MESSAGEHUB_DISABLE_DOTENV=1` 环境变量（mail / llm / headless 三处生效），conftest 强制启用，杜绝测试环境误读真实 `.env` 密钥外发
- **安装向导可选装 Moect**：setup.bat 运行中询问「要用微信实时采集吗」，确认后自动通过 winget 安装 Moect 并给出首次运行指引；跳过则进入纯手动导入模式（检测到已安装时直接跳过该步）
- **检测范围管理页面（/select）**：把群聊与私聊统一为三级分层（工作 / 混合 / 不检测）。会话清单来自 Moect 快照与本库消息统计，按最近活跃排序，支持搜索与类型筛选；AI 自动分级只做预勾选（仅收统计特征与脱敏样本，不发送完整聊天记录），人工复核后保存即时生效
- **私聊分级检测**：`personal.json` 新增 `contacts` 三级分层——工作级联系人发来非噪声即报；混合级需带任务语义才报；未勾选/不检测的私聊完全不采集（采集白名单自动跟随勾选结果）
- **README 开机自启指引**：新增「第 8 步（可选）：开机自启」，覆盖安装 / 取消 / PowerShell 手动等效命令与验证方法
- **工作台明暗双主题**：浅 / 深两套莫兰迪低饱和配色，设置页「外观」可跟随系统或手动切换；深色分层与对比度重新调校，替换原毛玻璃风格
- **版本更新提示与一键更新**：`/api/version` 对比远端 Release，工作台提示新版本；具备 Git 条件时可在页面一键更新

### 变更

- README 全文重写为面向普通用户的克制版本：去掉夸张宣称，明确 AI 备用服务（Pollinations / LLM7）外发风险、采集延迟、识别不保证准确；`.env.example` 同步提示不使用 AI/邮件时需清空示例值
- **简报与任务台统一**：`/brief` 重定向到工作台单一任务视图，`/api/brief` 直接读 `personal_tasks`，历史简报数据保留可查
- `moect_live.py` 采集改为优先消费代际标记指向的快照，并按 `--selected-groups` 收紧采集范围
- 采集器 `--include-private` 语义从「采集全部私聊」改为「按 contacts 分层放行」，未勾选任何联系人时等价于只采群聊
- 事件数据新增 `channel` 字段（group / private）区分消息来源
- 桌面通知默认落地 URL 为工作台锚点，加急通知携带任务 ID
- 工作台视觉重做：单一主题样式源、等宽数字、细边框与顶部高光、修复搜索框按钮在 flex 下被压成竖排的问题

### 修复

- headless 桥原先在 connect 完成但快照未实际更新时也返回成功，导致采集端可能合并半成品快照；现要求快照路径/时间戳确有变化才落标记
- **AI 接口卡死整个服务**：`/api/memories/summarize`、`/api/scope/ai-presel`、`/api/scan` 三处在 async 处理函数里同步调用 LLM（降级链超时重试最长约 18 分钟），期间事件循环被阻塞，所有请求（含实时采集入库）排队超时、SQLite 写锁饥饿。三处调用全部改为 `anyio.to_thread.run_sync` 线程池执行（2026-11-19 实测事故复盘）

## [0.1.0] - 2026-11-18

首个公开版本。

### 新增

- **工作简报**：三栏聚合（今日待办 / 需你回复 / 通知）+ 闭环进展，LLM 摘要失败自动降级本地规则
- **事件提取引擎**：正则 + 打分，识别 `@我`、直呼、上下文祈使句三类指派；混杂群双条件过滤；玩梗排除
- **闭环检测**：事件后回溯用户回复，已应答的不再重复提醒
- **实时采集**：`/api/live/ingest` 标准接入点，SSE 推送，指纹去重保证幂等
- **Moect 集成**：`scripts/moect_live.py` 桥接 Moect 明文快照；隐藏桌面隔离、单实例锁、水位回看、快照恒定一份
- **去年今日**：按北京时间匹配一年前的同一天，支持在线接口与离线 HTML
- **全文检索**：SQLite FTS5 索引
- **定时调度**：每天 8 点生成日报 + 整点滚动检查，有新事件才通知
- **邮件桥**：简报自动发送到指定邮箱（可选）
- **多格式导入**：MessageHub 标准 / Moect / QQ Chat Exporter / 通用 CSV、TXT
- **环境自检**：`python scripts/moect_live.py --check` 逐项检查 Moect、快照、服务、配置

### 隐私与安全

- 个人身份与工作群白名单抽到 `config/personal.json`，代码中不含真实姓名与群名
- 全部路径自动探测，不写死绝对路径，换机器可直接复现
- 默认只采群聊，不读私聊；只监听回环地址
- pre-commit 钩子拦截 `.env`、密钥、含聊天正文的产物
- CI 扫描硬编码凭据、Windows 用户路径、微信账号 ID

### 测试

- 96 项测试，强制隔离到临时库；测试使用虚构身份，不依赖本地配置

[Unreleased]: https://github.com/Eagle-Xu0213/MessageHub/compare/v2.1.1...HEAD
[2.1.1]: https://github.com/Eagle-Xu0213/MessageHub/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/Eagle-Xu0213/MessageHub/compare/v2.0.2...v2.1.0
[2.0.2]: https://github.com/Eagle-Xu0213/MessageHub/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/Eagle-Xu0213/MessageHub/compare/v1.3.0...v2.0.1
[1.3.0]: https://github.com/Eagle-Xu0213/MessageHub/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/Eagle-Xu0213/MessageHub/compare/v0.1.0...v1.2.0
[0.1.0]: https://github.com/Eagle-Xu0213/MessageHub/releases/tag/v0.1.0
