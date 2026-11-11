from __future__ import annotations

from src.infrastructure.persistence.sqlite_bootstrap import _load_json_file


def test_load_json_file_isolates_corrupt_file(tmp_path):
    """旧配置文件损坏时应隔离现场并返回 None，而不是让启动崩溃。"""
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert _load_json_file(path) is None
    assert not path.exists()
    assert (tmp_path / "config.json.corrupt").exists()


def test_load_json_file_reads_valid_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1}', encoding="utf-8")

    assert _load_json_file(path) == {"a": 1}
    assert path.exists()
