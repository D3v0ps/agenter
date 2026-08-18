"""Tidshantering. Lagring sker alltid i UTC (timestamptz);
naiva tider från importfiler tolkas som Europe/Stockholm."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import TIDSZON

STOCKHOLM = ZoneInfo(TIDSZON)


def till_utc(dt: datetime) -> datetime:
    """Naiv tid tolkas som Stockholm; tidszonsmedveten tid konverteras.

    Naiva klockslag som inte existerar (vårens sommartidshopp) eller är
    tvetydiga (höstens tillbakaställning) ger ValueError i stället för att
    tyst förskjutas — ATL-beräkningar får aldrig räkna på gissade tider."""
    if dt.tzinfo is None:
        lokal = dt.replace(tzinfo=STOCKHOLM)
        aterresa = lokal.astimezone(timezone.utc).astimezone(STOCKHOLM)
        if aterresa.replace(tzinfo=None, fold=0) != dt:
            raise ValueError(
                f"tidpunkten {dt} finns inte i Europe/Stockholm "
                f"(sommartidsomställning)"
            )
        if lokal.utcoffset() != dt.replace(tzinfo=STOCKHOLM, fold=1).utcoffset():
            raise ValueError(
                f"tidpunkten {dt} är tvetydig i Europe/Stockholm "
                f"(sommartidsomställning) — ange tid med tidszon"
            )
        dt = lokal
    return dt.astimezone(timezone.utc)


def till_lokal(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(STOCKHOLM)


def nu_utc() -> datetime:
    return datetime.now(timezone.utc)
