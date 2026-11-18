"""R1.1 闭环检测测试：事件后用户回复的状态分析。

用例结构来自真实使用中确认过的判读，但人名与群名全部虚构
（MY 取自 tests/fixtures/personal_test.json 的 sender_names）。
"""
import pytest

from messagehub.resolution import (
    _classify_reply, analyze_resolution, enrich_events_with_resolution,
)

MY = "测试昵称"


def _m(i, at, sender, text, group="工作群甲"):
    return {"id": i, "sent_at": at, "conversation_name": group,
            "sender_name": sender, "text": text}


# ---------------- 单条回复分类 ----------------

class TestClassifyReply:
    @pytest.mark.parametrize("text,expect", [
        ("我今天晚上不行，有课", "alt"),        # 拒绝+今晚(时间词)→改期类
        ("明天晚上应该可以", "alt"),            # 承诺新时间
        ("后天可以", "alt"),
        ("好", "ack"),
        ("好的", "ack"),
        ("嗯嗯", "ack"),
        ("好，那我取两个", "ack"),
        ("哦哦哦哦哦哦", None),               # 闲聊应答不算确认
        ("我想想", None),
        ("已经发群里了", "doing"),
        ("交了", "doing"),
        ("收到", "ack"),
        ("去不了", "decline"),
        ("没空", "decline"),
    ])
    def test_cases(self, text, expect):
        assert _classify_reply(text) == expect


# ---------------- 闭环分析 ----------------

class TestAnalyzeResolution:
    def test_replied_alt_case(self):
        # 真实案例：@我 今晚能来吗 → 不行有课 → 明天可以 → 对方定 → 我说好
        msgs = [
            _m(1, "2026-11-17T18:22+08:00", "李四", "@自动化2401张三 你今天晚上能过来吗"),
            _m(2, "2026-11-17T18:23+08:00", MY, "我今天晚上不行，有课"),
            _m(3, "2026-11-17T18:23+08:00", "李四", "那你最近有时间吗"),
            _m(4, "2026-11-17T18:24+08:00", MY, "明天晚上应该可以"),
            _m(5, "2026-11-17T18:24+08:00", "李四", "那我今天晚上过去，你明天过去"),
            _m(6, "2026-11-17T18:25+08:00", MY, "好"),
        ]
        ev = {"message_id": 1, "conversation_name": "工作群甲",
              "event_type": "reply_needed"}
        res = analyze_resolution(ev, msgs)
        assert res["reply_status"] == "answered_alt"
        assert "明天晚上应该可以" in res["reply_evidence"]

    def test_replied_ack_case(self):
        # 真实案例：取快递 → 我"好，那我取两个"
        msgs = [
            _m(1, "2026-11-05T17:44+08:00", "吴九",
               "圆通 取件码：82-7-3271 张三你就帮我拿这三个吧！"),
            _m(2, "2026-11-05T17:50+08:00", MY, "好，那我取两个"),
        ]
        ev = {"message_id": 1, "conversation_name": "混杂群甲",
              "event_type": "assignment"}
        res = analyze_resolution(ev, msgs)
        assert res["reply_status"] == "answered_ok"
        assert "取两个" in res["reply_evidence"]

    def test_unanswered_when_no_reply(self):
        msgs = [
            _m(1, "2026-11-17T17:06+08:00", "王五", "11月18日17:00 主E305 答辩"),
            _m(2, "2026-11-17T17:10+08:00", "周八", "收到"),
        ]
        ev = {"message_id": 1, "conversation_name": "工作群甲",
              "event_type": "notice"}
        res = analyze_resolution(ev, msgs)
        assert res["reply_status"] == "unanswered"

    def test_doing_overrides(self):
        msgs = [
            _m(1, "2026-11-17T12:52+08:00", "李四", "@全体成员 中午上传一下发票pdf"),
            _m(2, "2026-11-17T13:00+08:00", MY, "已经传了"),
        ]
        ev = {"message_id": 1, "conversation_name": "工作群甲",
              "event_type": "notice"}
        assert analyze_resolution(ev, msgs)["reply_status"] == "answered_ok"

    def test_window_limit(self):
        # 超出窗口的用户回复不计
        msgs = [_m(1, "2026-11-17T10:00+08:00", "甲", "麻烦交一下材料")]
        msgs += [_m(10 + i, f"2026-11-17T1{i}:00+08:00", "乙", f"水{i}") for i in range(20)]
        msgs.append(_m(99, "2026-11-17T23:00+08:00", MY, "好的"))
        ev = {"message_id": 1, "conversation_name": "工作群甲",
              "event_type": "assignment"}
        assert analyze_resolution(ev, msgs, window=15)["reply_status"] == "unanswered"


# ---------------- 批量与自排除联动 ----------------

class TestEnrich:
    def test_enrich_attaches_status(self):
        msgs = [
            _m(1, "2026-11-17T18:22+08:00", "李四", "@自动化2401张三 今晚能过来吗"),
            _m(2, "2026-11-17T18:23+08:00", MY, "不行，有课，明天可以"),
        ]
        events = [{"message_id": 1, "conversation_name": "工作群甲",
                   "event_type": "reply_needed"}]
        out = enrich_events_with_resolution(events, msgs)
        assert out[0]["reply_status"] == "answered_alt"
        assert out[0]["reply_evidence"]
