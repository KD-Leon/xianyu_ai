"""
Cron 解析与校验工具。
"""
from __future__ import annotations

from typing import Optional

from apscheduler.triggers.cron import CronTrigger

CRON_ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

CRON_FORMAT_HINT = (
    "Cron 表达式无效。支持 5 段（分 时 日 月 周）、"
    "6 段（秒 分 时 日 月 周）和常见别名（@hourly/@daily/@weekly/@monthly/@yearly）。"
    "示例：*/15 * * * *、0 8 * * *、0 0 8 * * *、@daily。"
)


def normalize_cron_expression(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    normalized = " ".join(str(value).strip().split())
    if not normalized:
        return None

    return CRON_ALIASES.get(normalized.lower(), normalized)


_CRON_DOW_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def _crontab_day_value(token: str) -> int:
    token = token.strip().lower()
    if token in _CRON_DOW_NAMES:
        return _CRON_DOW_NAMES[token]
    value = int(token)
    if value == 7:  # crontab 里 0 和 7 都表示周日
        value = 0
    if not 0 <= value <= 6:
        raise ValueError(f"星期取值超出范围: {token}")
    return value


def _crontab_dow_to_apscheduler(field: str) -> str:
    """把标准 crontab 的星期字段换算成 APScheduler 3.x 的语义。

    crontab 的数字星期是 0/7=周日、1=周一…6=周六，而 APScheduler 3.x
    是 0=周一…6=周日。直接交给 ``from_crontab`` 会让所有数字星期整体
    错一天（例如 1-5 变成周二到周六）。这里先展开成具体日期集合再映射。
    """
    field = str(field or "").strip()
    if not field:
        return field
    if field == "*":
        return "*"

    days: set[int] = set()
    for part in field.split(","):
        part = part.strip().lower()
        if not part:
            continue

        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError(f"星期步长无效: {field}")
            part = part.strip()

        if part == "*":
            start, end = 0, 6
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start = _crontab_day_value(start_text)
            end = _crontab_day_value(end_text)
        else:
            start = _crontab_day_value(part)
            end = start

        if start > end:
            start, end = end, start
        days.update(range(start, end + 1, step))

    if not days:
        raise ValueError(f"无法解析星期字段: {field}")

    mapped = sorted((day + 6) % 7 for day in days)
    return ",".join(str(day) for day in mapped)


def build_cron_trigger(
    expression: str,
    *,
    timezone=None,
) -> CronTrigger:
    normalized = normalize_cron_expression(expression)
    if normalized is None:
        raise ValueError(CRON_FORMAT_HINT)

    parts = normalized.split()
    try:
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts
            return CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=_crontab_dow_to_apscheduler(day_of_week),
                timezone=timezone,
            )

        if len(parts) == 6:
            second, minute, hour, day, month, day_of_week = parts
            return CronTrigger(
                second=second,
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=_crontab_dow_to_apscheduler(day_of_week),
                timezone=timezone,
            )
    except ValueError as exc:
        raise ValueError(CRON_FORMAT_HINT) from exc

    raise ValueError(CRON_FORMAT_HINT)


def validate_cron_expression(value: Optional[str]) -> Optional[str]:
    normalized = normalize_cron_expression(value)
    if normalized is None:
        return None

    build_cron_trigger(normalized)
    return normalized
