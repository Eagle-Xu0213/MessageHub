"""个人身份与工作群配置：从 config/personal.json 加载，代码里不再硬编码真人信息。

为什么单独抽出来
----------------
工作事件提取（rules.py）、闭环检测（resolution.py）、简报生成（brief.py）
都需要知道「我是谁」——我的昵称、真名、我在群里的发送名；还需要知道
「哪些群算工作群」。这些是使用者私有的配置，不是代码逻辑，写死在源码里
会导致：①换个人用就得改代码；②开源时把真名和真实群名一起泄出去。

加载顺序（后者覆盖前者）：
1. 内置默认值（空/通用占位，保证无配置文件也能跑）
2. config/personal.json（本地真实配置，已 gitignore）
3. 环境变量 MESSAGEHUB_PERSONAL_CONFIG 指向的路径（可选，用于多套配置切换）

未配置时的行为：不提取任何事件（因为不知道哪些群是工作群），
但导入、检索等不依赖个人身份的功能照常工作。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Set

# 本模块被 config.py 在模块级调用，不能反向 import config（循环导入）。
# 项目根 = src/messagehub/personal.py 的上两级。
BASE_DIR = Path(__file__).resolve().parents[2]

# 本地私有配置（不进版本库）
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "personal.json"
# 仓库内的模板（进版本库，供使用者复制）
EXAMPLE_CONFIG_PATH = BASE_DIR / "config" / "personal.example.json"

_DEFAULTS: Dict[str, Any] = {
    "identity": {
        "nicknames": [],
        "real_name": "",
        "short_name": "",
        "short_name_exclusions": ["会", "园", "村", "馆", "运"],
        "sender_names": [],
    },
    "groups": {
        "work": [],
        "watch": [],
        "excluded": [],
    },
    "group_ids": {"work": [], "watch": [], "excluded": []},
    # 私聊联系人分级（2026-11-18 新增）：工作=发来即工作内容；
    # watch=工作生活混合（需任务语义才报）；excluded=纯生活不检测。
    # 值为联系人「备注或昵称」（Moect 采集桥写入的 conversation_name 同源）。
    "contacts": {
        "work": [],
        "watch": [],
        "excluded": [],
    },
    "aliases": {},
    "brief": {
        "persona": "一位大学生",
    },
}

# 已知顶层键，用于校验拼写错误（写错键名会被静默忽略，很难排查）
_KNOWN_KEYS = set(_DEFAULTS) | {"_comment", "_说明"}


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """浅合并一层 dict（本配置只有两层，够用且不会误删未覆盖的键）。"""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for key, val in (override or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key].update(val)
        else:
            out[key] = val
    return out


def _load() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(_DEFAULTS))  # 深拷贝

    # 顺序即优先级：后加载的覆盖先加载的。
    # 本地 personal.json 是常态；环境变量用于测试/多套配置切换，优先级更高。
    paths: List[Path] = [DEFAULT_CONFIG_PATH]
    env_path = os.environ.get("MESSAGEHUB_PERSONAL_CONFIG")
    if env_path:
        paths.append(Path(env_path))

    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            # 配置坏了不能让整个应用起不来，但必须让人看得见
            print(f"[personal] 配置读取失败，已跳过 {path}：{e}")
            continue
        if not isinstance(data, dict):
            print(f"[personal] 配置格式错误（应为 JSON 对象），已跳过 {path}")
            continue
        unknown = set(data) - _KNOWN_KEYS
        if unknown:
            print(f"[personal] {path} 中有未知配置项：{sorted(unknown)}（已忽略）")
        cfg = _merge(cfg, data)
    return cfg


_CONFIG = _load()


def _as_set(values: Any) -> Set[str]:
    """把配置里的列表转成集合，顺手去掉空串和首尾空格。"""
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(v).strip() for v in values if str(v).strip()}


def _bound_ids(values: Any, names: Set[str]) -> Set[str]:
    if not isinstance(values, dict):
        return set()
    return {str(gid) for gid, name in values.items() if name in names}


# ------------------------- 身份 -------------------------

_identity = _CONFIG.get("identity", {})

# 微信昵称变体：@我 时可能出现任意一个
MY_NICKNAMES: Set[str] = _as_set(_identity.get("nicknames"))
# 真实姓名（群昵称里可能带班级前缀，如「@自动化2401张三」）
MY_REALNAME: str = str(_identity.get("real_name") or "").strip()
# 短名/称呼（被直呼时的匹配词，如「张三老师」里的「张三」）
MY_SHORTNAME: str = str(_identity.get("short_name") or "").strip()
# 短名后面跟这些字时不算称呼（防「世博会/世博园」这类复合词误判）
SHORTNAME_EXCLUSIONS: Set[str] = _as_set(
    _identity.get("short_name_exclusions") or []
)
# 我自己发消息时显示的昵称，用于闭环检测（判断「我有没有回复」）
MY_SENDER_NAMES: Set[str] = _as_set(_identity.get("sender_names"))

# 兼容旧引用：rules.py 里用的就是这个集合
if not MY_SENDER_NAMES and MY_NICKNAMES:
    MY_SENDER_NAMES = set(MY_NICKNAMES)

# 是否配置了身份（没配就不该提取「给我的」事件）
IDENTITY_CONFIGURED = bool(MY_NICKNAMES or MY_REALNAME)


# ------------------------- 群分层 -------------------------

_groups = _CONFIG.get("groups", {})
WORK_GROUPS: Set[str] = _as_set(_groups.get("work"))
WATCH_GROUPS: Set[str] = _as_set(_groups.get("watch"))
EXCLUDED_GROUPS: Set[str] = _as_set(_groups.get("excluded"))
_group_ids = _CONFIG.get("group_ids", {}) or {}
WORK_GROUP_IDS = _bound_ids(_group_ids.get("work"), WORK_GROUPS)
WATCH_GROUP_IDS = _bound_ids(_group_ids.get("watch"), WATCH_GROUPS)
EXCLUDED_GROUP_IDS = _bound_ids(_group_ids.get("excluded"), EXCLUDED_GROUPS)

# ------------------------- 私聊联系人分层 -------------------------
# 三级语义与群一致：work=发来即工作（非噪声即报）；watch=混合（需任务语义）；
# excluded=纯生活（不检测）。空的 contacts 段 = 不检测任何私聊（默认保守）。

_contacts = _CONFIG.get("contacts", {}) or {}
WORK_CONTACTS: Set[str] = _as_set(_contacts.get("work"))
WATCH_CONTACTS: Set[str] = _as_set(_contacts.get("watch"))
EXCLUDED_CONTACTS: Set[str] = _as_set(_contacts.get("excluded"))

# 群 ID → 可读群名（Moect 导出只带 chatroom id）
CONVERSATION_ALIASES: Dict[str, str] = {
    str(k): str(v)
    for k, v in (_CONFIG.get("aliases") or {}).items()
}

# 简报 LLM 提示词里的人设描述
BRIEF_PERSONA: str = str(
    (_CONFIG.get("brief") or {}).get("persona") or "一位大学生"
).strip()


def active_config_path() -> Path:
    """返回当前生效的配置文件路径（写入保存时用）。

    环境变量指向的路径优先（多套配置切换场景）；否则是 config/personal.json。
    路径不一定存在——调用方需自行处理首次创建。
    """
    env_path = os.environ.get("MESSAGEHUB_PERSONAL_CONFIG")
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


def reload() -> None:
    """重新读取配置（改完 personal.json 后无需重启进程，调用本函数即可）。

    注意：只重算本模块的模块级常量，其他模块 `from personal import X`
    拿到的仍是旧值。生产路径（rules.extract_events）走的是函数内引用，
    改配置后重启服务最稳妥。
    """
    global _CONFIG, MY_NICKNAMES, MY_REALNAME, MY_SHORTNAME, MY_SENDER_NAMES
    global IDENTITY_CONFIGURED, WORK_GROUPS, WATCH_GROUPS, EXCLUDED_GROUPS
    global WORK_GROUP_IDS, WATCH_GROUP_IDS, EXCLUDED_GROUP_IDS
    global WORK_CONTACTS, WATCH_CONTACTS, EXCLUDED_CONTACTS
    global CONVERSATION_ALIASES, BRIEF_PERSONA

    _CONFIG = _load()
    _identity = _CONFIG.get("identity", {})
    MY_NICKNAMES = _as_set(_identity.get("nicknames"))
    MY_REALNAME = str(_identity.get("real_name") or "").strip()
    MY_SHORTNAME = str(_identity.get("short_name") or "").strip()
    MY_SENDER_NAMES = _as_set(_identity.get("sender_names")) or set(MY_NICKNAMES)
    IDENTITY_CONFIGURED = bool(MY_NICKNAMES or MY_REALNAME)

    _groups = _CONFIG.get("groups", {})
    WORK_GROUPS = _as_set(_groups.get("work"))
    WATCH_GROUPS = _as_set(_groups.get("watch"))
    EXCLUDED_GROUPS = _as_set(_groups.get("excluded"))
    _group_ids = _CONFIG.get("group_ids", {}) or {}
    WORK_GROUP_IDS = _bound_ids(_group_ids.get("work"), WORK_GROUPS)
    WATCH_GROUP_IDS = _bound_ids(_group_ids.get("watch"), WATCH_GROUPS)
    EXCLUDED_GROUP_IDS = _bound_ids(_group_ids.get("excluded"), EXCLUDED_GROUPS)

    _contacts = _CONFIG.get("contacts", {}) or {}
    WORK_CONTACTS = _as_set(_contacts.get("work"))
    WATCH_CONTACTS = _as_set(_contacts.get("watch"))
    EXCLUDED_CONTACTS = _as_set(_contacts.get("excluded"))

    CONVERSATION_ALIASES = {
        str(k): str(v) for k, v in (_CONFIG.get("aliases") or {}).items()
    }
    BRIEF_PERSONA = str(
        (_CONFIG.get("brief") or {}).get("persona") or "一位大学生"
    ).strip()
