from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

MX_TZ = ZoneInfo("America/Mexico_City")


def now_mx() -> datetime:
    """Hora actual en México. Úsala para eventos reales de captura."""
    return datetime.now(MX_TZ)


def today_mx() -> date:
    return now_mx().date()


def hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def time_no_seconds(dt: datetime):
    return dt.time().replace(second=0, microsecond=0)
