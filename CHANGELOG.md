# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### 新增

- **安装向导可选装 Moect**：setup.bat 运行中询问「要用微信实时采集吗」，确认后自动通过 winget 安装 Moect 并给出首次运行指引；跳过则进入纯手动导入模式（检测到已安装时直接跳过该步）

### 新增

- **检测范围管理页面（/select）**：把群聊与私聊统一为三级分层（工作 / 混合 / 不检测）。会话清单来自 Moect 快照与本库消息统计，按最近活跃排序，支持搜索与类型筛选；AI 自动分级只做预勾选（仅收统计特征与脱敏样本，不发送完整聊天记录），人工复核后保存即时生效
- **私聊分级检测**：`personal.json` 新增 `contacts` 三级分层——工作级联系人发来非噪声即报；混合级需带任务语义才报；未勾选/不检测的私聊完全不采集（采集白名单自动跟随勾选结果）

### 变更

- 采集器 `--include-private` 语义从「采集全部私聊」改为「按 contacts 分层放行」，未勾选任何联系人时等价于只采群聊
- 事件数据新增 `channel` 字段（group / private）区分消息来源

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

[Unreleased]: https://github.com/Eagle-Xu0213/MessageHub/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Eagle-Xu0213/MessageHub/releases/tag/v0.1.0
