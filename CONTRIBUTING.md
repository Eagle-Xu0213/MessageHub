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
