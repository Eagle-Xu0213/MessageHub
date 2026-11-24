# 贡献指南

## 开发环境

```bash
git clone https://github.com/Eagle-Xu0213/MessageHub.git
cd MessageHub
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -e .
```

跑测试：

```bash
pytest -q
```

确保全部测试通过。测试全程隔离在临时库，不会碰你的真实数据。

## 提交前必做

安装 pre-commit 钩子。它会拦住误提交的 `.env`、密钥文件、含真实聊天正文的产物：

```bash
python scripts/install_hooks.py
```

**永远不要 `git add .`**。这个项目里 `runtime/`、`imports/`、`reports/` 都可能有真实聊天数据，逐个文件确认后再加。

## 改规则引擎

`src/messagehub/rules.py` 是纯正则 + 打分，改动影响面大。改之前先看 `tests/test_rules.py`——里面每个用例都对应一个真实踩过的坑。

新增规则时：

1. 先在 `tests/test_rules.py` 加用例（用虚构数据，见 `tests/fixtures/personal_test.json`）
2. 再改正则
3. 跑全量测试确认没有回归

**不要往代码里写真实的姓名、群名、群 ID。** 个人配置统一走 `config/personal.json`，代码里只读 `messagehub.personal` 模块。

## 代码约定

- 路径从 `config.py` 的 `BASE_DIR` 推导，不写绝对路径
- 数据库 schema 只在 `database.py` 定义
- 去重指纹只用 `pipeline/importer.make_fingerprint`
- `async def` 路由里禁止同步长任务（LLM 调用、全表扫描），用 `anyio.to_thread.run_sync` 包装
- 新增依赖写进 `pyproject.toml` 的 `dependencies`

## 提交信息

用 Conventional Commits 前缀：

```
feat(rules): 支持「把X发我」把字句指派
fix(live): 修复水位推进跳过未处理消息
docs: 补充 Moect 集成步骤
test: 补闭环检测窗口边界用例
refactor: 拆分 app.py 路由
chore: 更新 .gitignore
```

## 版本管理

项目遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)，版本号依次为「主版本.次版本.修订版本」。

| 改动 | 更新方式 | 示例 |
| --- | --- | --- |
| 已公开的 API、命令或行为发生不兼容变更 | 主版本加一，次版本和修订版本归零 | `2.1.0 → 3.0.0` |
| 向后兼容的功能新增，或公开功能被标记为弃用 | 次版本加一，修订版本归零 | `2.1.0 → 2.2.0` |
| 向后兼容的修复、文案或文档调整 | 修订版本加一 | `2.1.0 → 2.1.1` |

每次完成并验证改动后，基于最近公开发布的版本确定下一版本。未发布的多项改动归为一批，按其中需要提升的最高级别计算。同步更新 `pyproject.toml` 的 `version` 和 `src/messagehub/__init__.py` 的 `__version__`，并把面向用户的变更记入 `CHANGELOG.md` 的 `Unreleased`。

发布时填写发布日期，更新变更记录的比较链接，并核对 GitHub Release 的 `vX.Y.Z` 标签指向同一个已验证提交。保留已发布的版本号及标签。发现问题时用新版本修复。

## 隐私红线

PR 里出现以下内容会被直接关闭：

- 真实聊天记录、群名、人名、微信号
- `.env`、API Key、SMTP 授权码
- 导出的数据库、快照、日志
- 含聊天正文的 HTML / JSON / 截图

## 提 Issue

报 bug 请附：

- 操作系统与 Python 版本
- `python scripts/moect_live.py --check` 的输出（**先自行打码路径中的用户名**）
- 复现步骤与期望结果

功能建议请说明使用场景，不要只说「加个功能」。
