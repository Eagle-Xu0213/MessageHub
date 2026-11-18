"""P0 阶段核心测试：导入、去重、任务提取、HTTP API"""
import pytest
import json
import sys
from pathlib import Path

# 添加 src 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from messagehub.app import get_db
from messagehub.pipeline.importer import make_fingerprint


class TestFingerprint:
    """测试消息指纹生成（去重核心）"""

    def test_same_message_same_fingerprint(self):
        """相同内容生成相同指纹"""
        msg1 = {
            "source_platform": "wechat",
            "conversation_id": "group1",
            "sender_name": "张三",
            "sent_at": "2026-11-13T10:00:00+08:00",
            "text": "明天下午 3 点开会",
        }
        msg2 = {
            "source_platform": "wechat",
            "conversation_id": "group1",
            "sender_name": "张三",
            "sent_at": "2026-11-13T10:00:00+08:00",
            "text": "明天下午 3 点开会",
        }
        assert make_fingerprint(msg1) == make_fingerprint(msg2)

    def test_different_time_different_fingerprint(self):
        """不同时间生成不同指纹"""
        msg1 = {
            "source_platform": "wechat",
            "conversation_id": "group1",
            "sender_name": "张三",
            "sent_at": "2026-11-13T10:00:00+08:00",
            "text": "测试消息",
        }
        msg2 = {
            "source_platform": "wechat",
            "conversation_id": "group1",
            "sender_name": "张三",
            "sent_at": "2026-11-13T10:05:00+08:00",
            "text": "测试消息",
        }
        fp1 = make_fingerprint(msg1)
        fp2 = make_fingerprint(msg2)
        # 当前实现精确到分钟（[:17]），不同分钟 → 不同指纹
        assert fp1 != fp2

class TestImportCycle:
    """测试完整导入流程"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试前清空数据库"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages")
        cursor.execute("DELETE FROM tasks")
        conn.commit()
        conn.close()
    
    def test_import_sample_jsonl(self):
        """导入 sample_messages.jsonl"""
        fixtures_dir = Path(__file__).parent / "fixtures"
        jsonl_path = fixtures_dir / "sample_messages.jsonl"
        
        if not jsonl_path.exists():
            pytest.skip("Sample fixture not found")
        
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            lines = f.read().strip().split('\n')
        
        assert len(lines) > 0
        
        for line in lines:
            msg = json.loads(line)
            fp = make_fingerprint(msg)
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO messages (source_platform, source_method, conversation_id, conversation_name, sender_name, sent_at, text, fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (msg["source_platform"], msg.get("source_method", "manual_import"), msg["conversation_id"], msg.get("conversation_name", "unknown"), msg["sender_name"], msg["sent_at"], msg["text"], fp)
            )
            conn.commit()
            conn.close()
        
        # 验证导入数量
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        count = cursor.fetchone()[0]
        conn.close()
        
        assert count == 7  # sample 中有 7 条消息
    
    def test_duplicate_import_no_expansion(self):
        """幂等性：同一文件导入三次不膨胀"""
        fixtures_dir = Path(__file__).parent / "fixtures"
        jsonl_path = fixtures_dir / "sample_messages.jsonl"
        
        if not jsonl_path.exists():
            pytest.skip("Sample fixture not found")
        
        # 导入 3 次
        for iteration in range(3):
            with open(jsonl_path, 'r', encoding='utf-8') as f:
                lines = f.read().strip().split('\n')
            
            conn = get_db()
            cursor = conn.cursor()
            for line in lines:
                msg = json.loads(line)
                fp = make_fingerprint(msg)
                cursor.execute(
                    "INSERT OR IGNORE INTO messages (source_platform, source_method, conversation_id, conversation_name, sender_name, sent_at, text, fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (msg["source_platform"], msg.get("source_method", "manual_import"), msg["conversation_id"], msg.get("conversation_name", "unknown"), msg["sender_name"], msg["sent_at"], msg["text"], fp)
                )
            conn.commit()
            conn.close()
        
        # 最终数量应为 7，不是 21
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        count = cursor.fetchone()[0]
        conn.close()
        
        assert count == 7, f"幂等失败：期望 7 条，实际{count}条"
