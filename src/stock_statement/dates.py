"""证券业务使用中国标准时间的自然日期。"""
from datetime import date, datetime, timedelta, timezone


def china_today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()
