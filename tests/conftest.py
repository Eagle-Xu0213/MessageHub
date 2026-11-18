"""pytest 全局隔离（必须最先加载）。

历史事故：旧测试直接连接 ``runtime/messagehub.db`` 并在每个用例前
``DELETE FROM messages``，运行一次 pytest 会清空 21 万条真实消息。
本文件强制把测试库指向系统临时目录下的独立 SQLite 文件；
若有人显式把 MESSAGEHUB_DB_PATH 指回生产库，除非设置
MESSAGEHUB_ALLOW_PROD_DB=1，否则直接拒绝启动。

另外强制测试使用 tests/fixtures/personal_test.json 这套虚构身份与群名，
不读本机 config/personal.json——否则测试结果会随使用者的真实配置漂移，
开源后别人跑测试也不会因为「没有个人配置」而全部失败。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROD_DB = (ROOT / "runtime" / "messagehub.db").resolve()

# 系统临时目录在部分 Windows 环境会拒绝访问（WinError 5），统一改到项目内
_pytest_tmp = ROOT / "runtime" / "pytest_tmp"
_pytest_tmp.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_pytest_tmp)

requested = os.environ.get("MESSAGEHUB_DB_PATH")
if requested and Path(requested).resolve() == PROD_DB:
    if os.environ.get("MESSAGEHUB_ALLOW_PROD_DB") != "1":
        raise RuntimeError(
            "拒绝运行测试：MESSAGEHUB_DB_PATH 指向生产库，pytest 会清空真实消息。"
            "请改用临时库，或显式设置 MESSAGEHUB_ALLOW_PROD_DB=1 并自行承担风险。"
        )
elif not requested:
    # 必须与 --basetemp 分开：pytest 会清理 basetemp 目录，测试库放在里面会被删掉，
    # 导致 "no such table: messages"
    _test_db_dir = ROOT / "runtime" / "pytest_db"
    _test_db_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MESSAGEHUB_DB_PATH"] = str(_test_db_dir / "messagehub_test.db")

# 测试不能扫描真实 imports/（里面是 90MB 真实导出，会误当测试数据重导）
_test_imports = Path(tempfile.gettempdir()) / "messagehub_pytest_imports"
_test_imports.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MESSAGEHUB_WATCH_DIR", str(_test_imports))

# 虚构的个人配置：必须在 import messagehub 之前设置（personal.py 在导入时加载）
os.environ["MESSAGEHUB_PERSONAL_CONFIG"] = str(
    ROOT / "tests" / "fixtures" / "personal_test.json"
)

# 测试不调用真实 LLM / SMTP
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("SMTP_PASS", None)

sys.path.insert(0, str(ROOT / "src"))
