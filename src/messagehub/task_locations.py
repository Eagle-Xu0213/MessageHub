"""Local place recognition and reusable workbench labels."""
import re
import unicodedata


_LABEL = re.compile(r"(?:地点|地址|会场|集合点)\s*(?:[:：]|定在|设在|改到|改为|为|是)\s*([^,，。；;！!？?\n]+)")
_ACTION = re.compile(r"在\s*([^,，。；;！!？?\n]{1,80}?)\s*(?:开会|集合|签到|报到|碰头|办理|参加|领取|提交|交材料)")
_UNCERTAIN = re.compile(r"待定|未定|再定|待通知|另行通知|不确定|待确认|待安排|看情况|可能|暂定|备选|或|还是|二选一|线上|在线|视频会议|https?://|[?？/]")
_NON_PLACE = re.compile(r"^(?:这[里儿边]|那[里儿边]|原地|现场|老地方|原地点|同上|暂无|无|群里|微信|网上|家里)(?:$|附近|\s)|^(?:明天|今天|后天|(?:本|下)?周[一二三四五六日天]|上午|下午|晚上|\d{1,2}[:点月日号])|(?:网站|网页|小程序|群里)$")
_DETAILS = re.compile(r"\s*(?:请(?:于|在|提前|准时)|(?:准时)?(?:开会|集合|签到|报到|碰头|办理|参加|领取|提交|交材料)(?=\s|$)).*$")


def normalize(name):
    return " ".join(unicodedata.normalize("NFKC", name).split())


def recognize(*texts):
    """Extract explicit locations from message text; ambiguous clauses yield no labels."""
    found = {}
    for text in texts:
        for sentence in re.split(r"[。；;！!\n]", text or ""):
            if _UNCERTAIN.search(sentence):
                continue
            for clause in re.split(r"[，,]", sentence):
                if re.search(r"不(?:在|是)|取消|不要去", clause):
                    continue
                for match in list(_LABEL.finditer(clause)) or _ACTION.finditer(clause):
                    for part in re.split(r"、|\s+和\s+", _DETAILS.sub("", match[1])):
                        name = normalize(part).strip(" \"“”‘’")
                        if (0 < len(name) <= 80 and not _NON_PLACE.search(name)
                                and not re.search(r"[:：@]|^(?:的|于)|(?:之前|之后|以内|前|后)$", name)):
                            found.setdefault(name.casefold(), name)
    return list(found.values())


def init_store(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS personal_task_locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL, last_used_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS personal_task_location_links (
        task_id INTEGER NOT NULL REFERENCES personal_tasks(id) ON DELETE CASCADE,
        location_id INTEGER NOT NULL REFERENCES personal_task_locations(id) ON DELETE CASCADE,
        PRIMARY KEY (task_id, location_id))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_personal_task_location_links_location
        ON personal_task_location_links(location_id)""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(personal_tasks)")}
    if "locations_manually_set" not in columns:
        conn.execute("ALTER TABLE personal_tasks ADD COLUMN locations_manually_set INTEGER NOT NULL DEFAULT 0")


def get_or_create(conn, name, now):
    conn.execute("""INSERT INTO personal_task_locations(name,name_key,created_at)
        VALUES (?,?,?) ON CONFLICT(name_key) DO NOTHING""", (name, name.casefold(), now))
    return conn.execute("SELECT id FROM personal_task_locations WHERE name_key=?", (name.casefold(),)).fetchone()[0]


def set_links(conn, task_id, ids, now):
    previous = {row[0] for row in conn.execute(
        "SELECT location_id FROM personal_task_location_links WHERE task_id=?", (task_id,))}
    conn.executemany("DELETE FROM personal_task_location_links WHERE task_id=? AND location_id=?",
                     ((task_id, id_) for id_ in previous - ids))
    conn.executemany("INSERT INTO personal_task_location_links(task_id,location_id) VALUES (?,?)",
                     ((task_id, id_) for id_ in ids - previous))
    conn.executemany("UPDATE personal_task_locations SET last_used_at=? WHERE id=?",
                     ((now, id_) for id_ in ids - previous))


def apply_recognized(conn, task_id, now, *texts):
    names = recognize(*texts)
    if names:
        ids = {get_or_create(conn, name, now) for name in names}
        ids.update(row[0] for row in conn.execute(
            "SELECT location_id FROM personal_task_location_links WHERE task_id=?", (task_id,)))
        set_links(conn, task_id, ids, now)
