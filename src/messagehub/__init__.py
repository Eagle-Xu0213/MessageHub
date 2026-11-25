"""MessageHub — 个人工作消息中枢。

数据流：Moect 明文快照 / 导出文件 → 统一解析（adapters/registry）→
指纹去重入库（pipeline/importer）→ SQLite + SSE 实时流（pipeline/live）。
实时采集由 scripts/moect_live.py 常驻轮询并 POST /api/live/ingest。
"""

__version__ = "3.2.0"
