from __future__ import annotations

import calendar
import datetime as dt

from chinese_calendar import is_workday


def count_workdays_cn(year: int, month: int) -> int:
    if month < 1 or month > 12:
        raise ValueError(f"invalid month: {month}")

    days_in_month = calendar.monthrange(year, month)[1]
    count = 0
    for d in range(1, days_in_month + 1):
        day = dt.date(year, month, d)
        if is_workday(day):
            count += 1
    return count

