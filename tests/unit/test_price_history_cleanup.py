from __future__ import annotations

from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_connection import sqlite_connection
from src.services.price_history_service import cleanup_price_snapshots


def _insert_snapshot(conn, slug: str, snapshot_time: str) -> None:
    conn.execute(
        """
        INSERT INTO price_snapshots (
            keyword_slug, keyword, task_name, snapshot_time, snapshot_day,
            run_id, item_id, title, price, price_display, tags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            slug,
            slug,
            "task",
            snapshot_time,
            snapshot_time[:10],
            "run",
            "1",
            "item",
            1.0,
            "¥1",
            "[]",
        ),
    )


def test_cleanup_price_snapshots_removes_only_expired_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "app.sqlite3"))
    bootstrap_sqlite_storage()

    with sqlite_connection() as conn:
        _insert_snapshot(conn, "old", "2000-01-01T00:00:00")
        _insert_snapshot(conn, "new", "2999-01-01T00:00:00")
        conn.commit()

    removed = cleanup_price_snapshots(keep_days=30)

    assert removed == 1
    with sqlite_connection() as conn:
        rows = conn.execute(
            "SELECT keyword_slug FROM price_snapshots ORDER BY keyword_slug"
        ).fetchall()
    assert [row["keyword_slug"] for row in rows] == ["new"]
