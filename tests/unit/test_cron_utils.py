from datetime import datetime
from zoneinfo import ZoneInfo

from src.core.cron_utils import build_cron_trigger, validate_cron_expression

TZ = ZoneInfo("Asia/Shanghai")
# 2026-03-04 是周三，作为下一个触发时间的计算基准
BASE_TIME = datetime(2026, 3, 4, 12, 0, tzinfo=TZ)


def test_validate_cron_expression_normalizes_alias():
    assert validate_cron_expression("@daily") == "0 0 * * *"


def test_validate_cron_expression_accepts_six_fields():
    assert validate_cron_expression("0 0 8 * * *") == "0 0 8 * * *"


def test_build_cron_trigger_accepts_alias_and_timezone():
    trigger = build_cron_trigger("@hourly", timezone="Asia/Shanghai")

    assert trigger is not None
    assert str(trigger.timezone) == "Asia/Shanghai"


def test_cron_numeric_weekday_uses_crontab_semantics():
    """1 必须是周一，而不是 APScheduler 原生语义下的周二。"""
    trigger = build_cron_trigger("0 9 * * 1", timezone=TZ)

    next_time = trigger.get_next_fire_time(None, BASE_TIME)

    assert next_time is not None
    assert next_time.weekday() == 0


def test_cron_weekend_list_maps_to_saturday_and_sunday():
    trigger = build_cron_trigger("0 10 * * 6,0", timezone=TZ)

    next_time = trigger.get_next_fire_time(None, BASE_TIME)

    assert next_time is not None
    assert next_time.weekday() == 5  # 周三之后的下一次是周六


def test_weekly_alias_fires_on_sunday():
    trigger = build_cron_trigger("@weekly", timezone=TZ)

    next_time = trigger.get_next_fire_time(None, BASE_TIME)

    assert next_time is not None
    assert next_time.weekday() == 6


def test_validate_cron_expression_rejects_invalid_value():
    try:
        validate_cron_expression("not-a-cron")
    except ValueError as exc:
        assert "支持 5 段" in str(exc)
        return

    raise AssertionError("非法 cron 应该抛出 ValueError")
