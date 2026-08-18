"""Tidshantering. Lagring sker alltid i UTC (timestamptz);
naiva tider från importfiler tolkas som Europe/Stockholm."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import TIDSZON

STOCKHOLM = ZoneInfo(TIDSZON)


def till_utc(dt: datetime) -> datetime:
    """Naiv tid tolkas som Stockholm; tidszonsmedveten tid konverteras."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=STOCKHOLM)
    return dt.astimezone(timezone.utc)


def till_lokal(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(STOCKHOLM)


def nu_utc() -> datetime:
    return datetime.now(timezone.utc)
