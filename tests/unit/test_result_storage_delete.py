from __future__ import annotations

import asyncio

from src.services.result_storage_service import (
    delete_result_file_records,
    load_result_blacklist_keywords,
    save_result_blacklist_keywords,
)


def test_delete_result_file_records_also_removes_blacklist(tmp_path, monkeypatch):
    """删除结果文件时必须清理同名黑名单规则，否则重建后会复活旧规则。"""
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "app.sqlite3"))
    filename = "sony_a7m4_full_data.jsonl"

    asyncio.run(save_result_blacklist_keywords(filename, ["瑕疵", "维修"]))
    assert asyncio.run(load_result_blacklist_keywords(filename)) == ["瑕疵", "维修"]

    asyncio.run(delete_result_file_records(filename))

    assert asyncio.run(load_result_blacklist_keywords(filename)) == []
