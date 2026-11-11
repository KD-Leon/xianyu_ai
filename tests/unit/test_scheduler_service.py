from __future__ import annotations

from src.services.process_service import ProcessService
from src.services.scheduler_service import SchedulerService


def test_scheduler_sets_misfire_grace_time(tmp_path):
    """事件循环卡顿超过 1 秒时不应丢弃当次 cron 运行。"""
    service = SchedulerService(ProcessService(pid_dir=str(tmp_path)))

    defaults = service.scheduler._job_defaults

    assert defaults["misfire_grace_time"] == 60
    assert defaults["coalesce"] is True
    assert defaults["max_instances"] == 1
