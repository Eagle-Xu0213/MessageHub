"""R1 规则引擎测试。

用例覆盖的规则来自真实使用中确认过的判读结果，但数据全部虚构
（见 tests/fixtures/personal_test.json）：真名「张三」、昵称「测试昵称」、
群名「工作群甲/混杂群甲」。这样测试不依赖任何人的真实配置。
"""
import pytest

from messagehub import personal
from messagehub.rules import (
    WORK_GROUPS, WATCH_GROUPS, EXCLUDED_GROUPS,
    cue_type_of, work_score_of, extract_event, extract_events,
)

# 与 fixtures/personal_test.json 保持一致
WORK_A = "工作群甲"
WATCH_A = "混杂群甲"
EXCLUDED_A = "排除群甲"


def _msg(group, sender, text, **kw):
    return {
        "id": kw.get("id", 1),
        "sent_at": kw.get("sent_at", "2026-11-17T10:00:00+08:00"),
        "conversation_name": group,
        "sender_name": sender,
        "text": text,
    }


def _type(group, sender, text):
    ev = extract_event(_msg(group, sender, text))
    return ev["event_type"] if ev else None


# ---------------- 群分层 ----------------

class TestGroupTiers:
    def test_work_groups_loaded_from_config(self):
        assert WORK_A in WORK_GROUPS

    def test_watch_groups_loaded_from_config(self):
        assert WATCH_A in WATCH_GROUPS

    def test_excluded_groups_not_extracted(self):
        # 排除群即使命中关键词也不报
        assert EXCLUDED_A in EXCLUDED_GROUPS
        assert _type(EXCLUDED_A, "某人", "请大家3点前提交材料") is None

    def test_unknown_group_ignored(self):
        assert _type("未登记的路人群", "路人", "有没有推理剧推荐") is None


# ---------------- cue 形态 ----------------

class TestCueDetection:
    def test_at_nickname(self):
        assert cue_type_of("@测试昵称 谢谢") == "at_me"

    def test_at_realname_with_prefix(self):
        # 群昵称带班级等前缀：@班级张三
        assert cue_type_of("@自动化2401张三 你今天晚上能过来吗") == "at_me"

    def test_direct_call_verb_follow(self):
        # 称呼后直接跟动词（可以帮我）也该命中
        assert cue_type_of("张三老师可以帮我把聘书拿到值班室嘛") == "direct_call"

    def test_direct_call_pronoun_follow(self):
        assert cue_type_of("张三你就帮我拿这三个吧") == "direct_call"

    def test_not_cue_compound_word(self):
        # short_name_exclusions 里的字紧跟短名时不认作称呼，
        # 用来规避「世博会/世博园」这类复合词误判（测试配置排除「会」「园」）
        assert cue_type_of("去张三园玩") is None
        assert cue_type_of("张三会来了吗") is None

    def test_meme_at_12306(self):
        # 与 @12306/@10086 并列的 @我 是玩梗
        assert cue_type_of("@12306 @测试昵称 @10086竭诚为您服务") is None

    def test_self_reference_ignored(self):
        # 句中出现但不是在称呼我
        assert cue_type_of("我听说张三不是在谈恋爱吗") is None


# ---------------- 工作群事件 ----------------

class TestWorkGroupEvents:
    def test_assignment_via_at_realname(self):
        assert _type(WORK_A, "李四", "张三，暂停一下这周的值班工作@张三") == "assignment"

    def test_notice_with_time_place(self):
        ev = extract_event(_msg(
            WORK_A, "王五",
            "各位同学：11月18日（周三）17：00，在主楼E305会议室，开展答辩。"))
        assert ev and ev["event_type"] == "notice"
        assert "11月18日" in ev["deadline"]

    def test_notice_signup_code(self):
        assert _type(WORK_A, "事务部", "本周签到码:6912RE") == "notice"

    def test_notice_volunteer_recruit(self):
        ev = extract_event(_msg(
            WORK_A, "赵六",
            "下周三中期答辩招募志愿者，地点在就创中心二楼，可以的戳"))
        assert ev and ev["event_type"] == "notice"

    def test_deadline_pressure_notice(self):
        ev = extract_event(_msg(
            WORK_A, "赵六",
            "以上6名选手还没有提交材料，请3点前务必提交"))
        assert ev and ev["event_type"] == "notice"

    def test_chat_in_work_group_ignored(self):
        # 工作群里的纯闲聊（无通知词无任务词）不报
        assert _type(WORK_A, "孙七", "感恩 明早学吧 今天就到这了") is None
        assert _type(WORK_A, "周八", "看看这些吧，老师") is None


# ---------------- 混杂群双条件 ----------------

class TestWatchGroupDualCondition:
    def test_cue_plus_task_is_assignment(self):
        # cue 我 + 帮忙拿 → assignment
        ev = extract_event(_msg(
            WATCH_A, "吴九",
            "圆通 取件码：82-7-3271 张三你就帮我拿这三个吧！谢谢老师！"))
        assert ev and ev["event_type"] == "assignment"

    def test_cue_plus_help_get(self):
        assert _type(WATCH_A, "李四",
                     "张三老师可以帮我把聘书和可乐拿到值班室嘛") == "assignment"

    def test_cue_without_task_ignored(self):
        # 提问但无任务语义 → 不报
        assert _type(WATCH_A, "吴九",
                     "张三老师，你知道哪个专业会学一些3d打印之类的东西吗？") is None
        # 进度询问 → 不报
        assert _type(WATCH_A, "吴九", "张三老师，你现在准备的咋样啦") is None
        # 寒暄 → 不报
        assert _type(WATCH_A, "李四", "张三老师确实需要一顿美食") is None
        assert _type(WATCH_A, "吴九", "张三老师辛苦啦") is None

    def test_meme_chain_ignored(self):
        assert _type(WATCH_A, "李四",
                     "@12306 @测试昵称 @10086竭诚为您服务") is None
        # 多@无任务语义的整句
        assert _type(WATCH_A, "郑十",
                     "@测试昵称 谢谢张三的建议！！！！！！") is None

    def test_reply_needed_when_question(self):
        # @我 + 疑问句 → 需回复
        ev = extract_event(_msg(
            WORK_A, "李四", "@自动化2401张三 你今天晚上能过来吗"))
        assert ev and ev["event_type"] == "reply_needed"


# ---------------- 噪声与边界 ----------------

class TestNoise:
    def test_emoji_only(self):
        assert extract_event(_msg(WORK_A, "孙七", "[Facepalm]")) is None

    def test_ack_words(self):
        assert extract_event(_msg(WORK_A, "孙七", "好的")) is None

    def test_empty_text(self):
        assert extract_event(_msg(WORK_A, "孙七", "")) is None

    def test_3d_print_false_positive_fixed(self):
        # "3d打印" 是技术词不是动作
        assert _type(WORK_A, "冯某", "你知道哪个专业会学一些3d打印之类的东西吗") is None


# ---------------- 批量与字段完整性 ----------------

class TestExtractEvents:
    def test_batch_and_fields(self):
        msgs = [
            _msg(WORK_A, "冯某", "@全体成员 记得中午上传一下发票pdf，截止下午5点", id=1),
            _msg(WATCH_A, "吴九", "张三你就帮我拿这三个吧", id=2),
            _msg("未登记的路人群", "某人", "根本不想读这个大学", id=3),
        ]
        events = extract_events(msgs)
        assert len(events) == 2
        for e in events:
            assert {"message_id", "sent_at", "conversation_name", "sender_name",
                    "text", "event_type", "cue_type", "deadline",
                    "work_score"} <= set(e.keys())
        assert events[0]["message_id"] == 1
        assert events[0]["event_type"] == "notice"
        assert events[1]["cue_type"] == "direct_call"

    def test_self_sent_messages_excluded(self):
        # 自己发的不产生待办
        me = "测试昵称"
        assert extract_events([
            _msg(WORK_A, me, "@全体成员 记得提交材料，截止5点", id=1),
        ]) == []


# ---------------- 未配置身份时的行为 ----------------

class TestUnconfiguredIdentity:
    def test_no_config_means_no_events(self, monkeypatch):
        """没配身份/群白名单时不该瞎猜，直接返回空。"""
        monkeypatch.setattr(personal, "IDENTITY_CONFIGURED", False)
        monkeypatch.setattr(personal, "WORK_GROUPS", set())
        monkeypatch.setattr(personal, "WATCH_GROUPS", set())
        monkeypatch.setattr("messagehub.rules.WORK_GROUPS", set())
        monkeypatch.setattr("messagehub.rules.WATCH_GROUPS", set())
        assert extract_events([
            _msg(WORK_A, "李四", "@张三 请3点前提交材料"),
        ]) == []
