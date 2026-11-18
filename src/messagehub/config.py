"""全局配置：路径、时区、群名映射。全项目唯一定义点。

所有模块从这里取 DB 路径与时区常量，禁止再各自 resolve parents[N]
或重新定义 friendly_name（审计报告 §3.3）。
"""
from __future__ import annotations

import os
from datetime import timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

# 允许通过环境变量把数据库指向别处（pytest 必须指向临时库，避免清空生产数据）
DB_PATH = Path(os.environ.get("MESSAGEHUB_DB_PATH") or (BASE_DIR / "runtime" / "messagehub.db"))
TEMPLATES_DIR = BASE_DIR / "src" / "messagehub" / "templates"
STATIC_DIR = BASE_DIR / "src" / "messagehub" / "static"
WATCH_DIR = Path(os.environ.get("MESSAGEHUB_WATCH_DIR") or (BASE_DIR / "imports"))  # 自动扫描目录

# 北京时间（全项目统一用这个，不再依赖 pytz）
CN_TZ = timezone(timedelta(hours=8))


def now_cn_iso() -> str:
    """当前北京时间 ISO 字符串（秒级）。"""
    from datetime import datetime
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


# 群聊显示名映射：Moect 导出只带 chatroom id，需要补可读名称。
# 真实映射在 config/personal.json 的 aliases 段（已 gitignore），
# 这里只保留空表作为默认值，避免把真实群 ID 写进源码。
CONVERSATION_ALIASES: dict = {}


def _load_aliases() -> None:
    """从 personal.json 与 runtime/live_config.json 合并群名映射。

    personal.json 优先（用户显式配置），live_config.json 作为补充
    （采集器运行期写入，可免改代码扩充）。
    """
    from messagehub import personal
    CONVERSATION_ALIASES.update(personal.CONVERSATION_ALIASES)

    cfg_file = BASE_DIR / "runtime" / "live_config.json"
    try:
        import json
        extra = (json.loads(cfg_file.read_text(encoding="utf-8"))
                 .get("conversation_aliases") or {})
        for k, v in extra.items():
            CONVERSATION_ALIASES.setdefault(str(k), str(v))
    except Exception:
        pass


_load_aliases()


def friendly_name(conversation_id: str, conversation_name: str) -> str:
    """把 @chatroom 之类的原始 id 转成可读群名。

    全项目唯一定义点（原 app.py 与 work_brief.py 各有一份且兜底不同）。
    兜底行为取 work_brief 版本：给出可识别的占位名而非空字符串。
    """
    name = (conversation_name or "").strip()
    if name and not name.endswith("@chatroom") and name != "unknown":
        return name
    cid = (conversation_id or "").replace("@chatroom", "")
    if cid in CONVERSATION_ALIASES:
        return CONVERSATION_ALIASES[cid]
    if name in CONVERSATION_ALIASES:
        return CONVERSATION_ALIASES[name]
    if cid and cid != "unknown":
        return f"未命名群（{cid[:12]}）"
    return name or "未标注来源"
