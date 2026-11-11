from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

from src.failure_guard import FailureGuard


def test_failure_guard_opens_circuit_after_threshold_and_rate_limits(tmp_path):
    guard_path = tmp_path / "guard.json"
    cookie_path = tmp_path / "xianyu_state.json"
    cookie_path.write_text("{}", encoding="utf-8")

    guard = FailureGuard(
        path=str(guard_path),
        threshold=3,
        pause_seconds=3 * 24 * 60 * 60,
        tz_name="Asia/Shanghai",
    )

    base = datetime(2026, 3, 4, 12, 0, 0)

    r1 = guard.record_failure("task-a", "err-1", cookie_path=str(cookie_path), now=base)
    assert r1["should_notify"] is False
    assert r1["opened_circuit"] is False

    r2 = guard.record_failure("task-a", "err-2", cookie_path=str(cookie_path), now=base)
    assert r2["should_notify"] is False
    assert r2["opened_circuit"] is False

    r3 = guard.record_failure("task-a", "err-3", cookie_path=str(cookie_path), now=base)
    assert r3["should_notify"] is True
    assert r3["opened_circuit"] is True
    assert r3["paused_until"] is not None

    d0 = guard.should_skip_start("task-a", cookie_path=str(cookie_path), now=base)
    assert d0.skip is True
    assert d0.should_notify is False

    next_day = base + timedelta(days=1, minutes=1)
    d1 = guard.should_skip_start("task-a", cookie_path=str(cookie_path), now=next_day)
    assert d1.skip is True
    assert d1.should_notify is True

    d1b = guard.should_skip_start("task-a", cookie_path=str(cookie_path), now=next_day)
    assert d1b.skip is True
    assert d1b.should_notify is False


def test_failure_guard_auto_recovers_on_cookie_change(tmp_path):
    guard_path = tmp_path / "guard.json"
    cookie_path = tmp_path / "xianyu_state.json"
    cookie_path.write_text("{}", encoding="utf-8")

    guard = FailureGuard(
        path=str(guard_path),
        threshold=2,
        pause_seconds=3 * 24 * 60 * 60,
        tz_name="Asia/Shanghai",
    )

    base = datetime(2026, 3, 4, 12, 0, 0)

    guard.record_failure("task-a", "err-1", cookie_path=str(cookie_path), now=base)
    guard.record_failure("task-a", "err-2", cookie_path=str(cookie_path), now=base)

    paused = guard.should_skip_start("task-a", cookie_path=str(cookie_path), now=base)
    assert paused.skip is True

    cookie_path.write_text('{"updated": true}', encoding="utf-8")

    recovered = guard.should_skip_start(
        "task-a",
        cookie_path=str(cookie_path),
        now=base + timedelta(minutes=1),
    )
    assert recovered.skip is False


def test_update_uses_sidecar_lock_file(tmp_path):
    """锁必须加在 sidecar 文件上：Windows 上锁住数据文件会导致 os.replace 失败。"""
    guard_path = tmp_path / "guard.json"

    FailureGuard(path=str(guard_path)).record_success("task-a")

    assert guard_path.exists()
    assert (tmp_path / "guard.json.lock").exists()


def test_atomic_write_retries_transient_permission_error(tmp_path, monkeypatch):
    """模拟 Windows 上目标文件被短暂打开：替换应重试而不是直接失败。"""
    import src.failure_guard as failure_guard

    guard_path = tmp_path / "guard.json"
    real_replace = failure_guard.os.replace
    calls = {"count": 0}

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError(13, "拒绝访问")
        return real_replace(src, dst)

    monkeypatch.setattr(failure_guard.os, "replace", flaky_replace)

    FailureGuard(path=str(guard_path)).record_success("task-a")

    assert calls["count"] == 3
    assert json.loads(guard_path.read_text(encoding="utf-8"))["tasks"]["task-a"][
        "consecutive_failures"
    ] == 0


def test_concurrent_updates_do_not_lose_tasks(tmp_path):
    guard_path = tmp_path / "guard.json"
    guard = FailureGuard(path=str(guard_path), threshold=5)
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        try:
            for _ in range(5):
                guard.record_failure(name, "err")
        except BaseException as exc:  # pragma: no cover - 失败时用于定位
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(f"task-{i}",)) for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    tasks = json.loads(guard_path.read_text(encoding="utf-8"))["tasks"]
    assert set(tasks) == {f"task-{i}" for i in range(8)}
    assert all(tasks[f"task-{i}"]["consecutive_failures"] == 5 for i in range(8))
