"""ATL-spärren: blockerar erbjudanden som skulle bryta mot dygnsvila,
veckovila eller taket för total arbetstid per rullande sjudagarsperiod.

Metod: kandidatpasset läggs ihop med konsultens ALLA aktiva bokningar (även
importerade befintliga pass) och slås samman till sammanhängande
arbetsblock. Varje regel utvärderas sedan över glidande fönster. De
fönsterstarter som kontrolleras är blockgränserna samt blockgränserna minus
periodlängden — extremvärdena för styckvis linjära funktioner (största vila
respektive summerad arbetstid i ett fönster) ligger alltid i sådana
brytpunkter, så kontrollen är exakt och inte ett stickprov.

Endast fönster som överlappar kandidatpasset kan blockera: ett redan
befintligt (t.ex. importerat) regelbrott ska inte hindra ett obesläktat pass
långt senare."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.atl_config import (
    DYGNSPERIOD_TIMMAR,
    DYGNSVILA_TIMMAR,
    MAX_ARBETSTID_TIMMAR_PER_VECKOPERIOD,
    VECKOPERIOD_DAGAR,
    VECKOVILA_TIMMAR,
)
from app.models import Bokning, BokningStatus, Konsult
from app.services.gemensamt import HittadesInte
from app.tid import till_utc

Intervall = tuple[datetime, datetime]


@dataclass
class AtlBrott:
    regel: str  # "dygnsvila" | "veckovila" | "veckoarbetstid"
    beskrivning: str

    def som_dict(self) -> dict:
        return {"regel": self.regel, "beskrivning": self.beskrivning}


@dataclass
class AtlResultat:
    ok: bool
    brott: list[AtlBrott]

    def som_dict(self) -> dict:
        return {"ok": self.ok, "brott": [b.som_dict() for b in self.brott]}


def kontrollera_atl(
    session: Session, konsult_id: int, starttid: datetime, sluttid: datetime
) -> dict:
    """Kontrollera om ett tänkt pass för konsulten skulle bryta mot
    ATL-gränserna i app/atl_config.py: dygnsvila (minst 11 h per
    24-timmarsperiod), veckovila (minst 36 h per sjudagarsperiod) och taket
    för total arbetstid per rullande sjudagarsperiod (48 h).

    Räknar mot konsultens ALLA aktiva bokningar, även importerade
    befintliga pass. Enbart läsning — ändrar ingenting.

    Returnerar {"ok": bool, "brott": [{"regel", "beskrivning"}]}.
    """
    if session.get(Konsult, konsult_id) is None:
        raise HittadesInte(f"konsult {konsult_id} finns inte")
    starttid, sluttid = till_utc(starttid), till_utc(sluttid)
    if sluttid <= starttid:
        raise ValueError("sluttid måste vara efter starttid")
    return berakna_atl(session, konsult_id, starttid, sluttid).som_dict()


def berakna_atl(
    session: Session, konsult_id: int, starttid: datetime, sluttid: datetime
) -> AtlResultat:
    """Intern kärna — används av kontrollera_atl, registrera_utskick och
    tilldela_plats. Tiderna ska vara tidszonsmedvetna (UTC)."""
    marginal = timedelta(days=VECKOPERIOD_DAGAR + 1)
    bokningar = session.scalars(
        select(Bokning).where(
            Bokning.konsult_id == konsult_id,
            Bokning.status == BokningStatus.BOKAD,
            Bokning.starttid < sluttid + marginal,
            Bokning.sluttid > starttid - marginal,
        )
    ).all()
    block = _sla_samman(
        [(b.starttid, b.sluttid) for b in bokningar] + [(starttid, sluttid)]
    )
    kandidat = (starttid, sluttid)

    brott: list[AtlBrott] = []
    brott += _kontrollera_vila(
        block,
        kandidat,
        period=timedelta(hours=DYGNSPERIOD_TIMMAR),
        kravd_vila=timedelta(hours=DYGNSVILA_TIMMAR),
        regel="dygnsvila",
    )
    brott += _kontrollera_vila(
        block,
        kandidat,
        period=timedelta(days=VECKOPERIOD_DAGAR),
        kravd_vila=timedelta(hours=VECKOVILA_TIMMAR),
        regel="veckovila",
    )
    brott += _kontrollera_arbetstid(
        block,
        kandidat,
        period=timedelta(days=VECKOPERIOD_DAGAR),
        tak=timedelta(hours=MAX_ARBETSTID_TIMMAR_PER_VECKOPERIOD),
    )
    return AtlResultat(ok=not brott, brott=brott)


def _sla_samman(intervall: list[Intervall]) -> list[Intervall]:
    """Slå samman överlappande/kant-i-kant-intervall till arbetsblock."""
    resultat: list[Intervall] = []
    for start, slut in sorted(intervall):
        if resultat and start <= resultat[-1][1]:
            resultat[-1] = (resultat[-1][0], max(resultat[-1][1], slut))
        else:
            resultat.append((start, slut))
    return resultat


def _fonsterstarter(
    block: list[Intervall], kandidat: Intervall, period: timedelta
) -> list[datetime]:
    """Alla fönsterstarter värda att kontrollera: blockgränser och
    blockgränser minus periodlängden, begränsat till fönster som
    överlappar kandidatpasset."""
    kandidatstart, kandidatslut = kandidat
    punkter: set[datetime] = set()
    for start, slut in block:
        for granspunkt in (start, slut):
            for t in (granspunkt, granspunkt - period):
                # fönstret [t, t+period] måste överlappa kandidatpasset
                if t < kandidatslut and t + period > kandidatstart:
                    punkter.add(t)
    return sorted(punkter)


def _storsta_vila_i_fonster(
    block: list[Intervall], fs: datetime, fe: datetime
) -> timedelta:
    """Största sammanhängande vila inom fönstret [fs, fe]."""
    vila = timedelta(0)
    punkt = fs
    for start, slut in block:
        if slut <= fs or start >= fe:
            continue
        klippt_start = max(start, fs)
        if klippt_start > punkt:
            vila = max(vila, klippt_start - punkt)
        punkt = max(punkt, min(slut, fe))
    if fe > punkt:
        vila = max(vila, fe - punkt)
    return vila


def _arbetstid_i_fonster(
    block: list[Intervall], fs: datetime, fe: datetime
) -> timedelta:
    summa = timedelta(0)
    for start, slut in block:
        overlapp = min(slut, fe) - max(start, fs)
        if overlapp > timedelta(0):
            summa += overlapp
    return summa


def _fmt(td: timedelta) -> str:
    minuter = int(td.total_seconds() // 60)
    return f"{minuter // 60} h {minuter % 60:02d} min"


def _kontrollera_vila(
    block: list[Intervall],
    kandidat: Intervall,
    *,
    period: timedelta,
    kravd_vila: timedelta,
    regel: str,
) -> list[AtlBrott]:
    for t in _fonsterstarter(block, kandidat, period):
        vila = _storsta_vila_i_fonster(block, t, t + period)
        if vila < kravd_vila:
            return [
                AtlBrott(
                    regel,
                    f"största sammanhängande vila är {_fmt(vila)} i perioden "
                    f"{t.isoformat()} – {(t + period).isoformat()}, "
                    f"minst {_fmt(kravd_vila)} krävs",
                )
            ]
    return []


def _kontrollera_arbetstid(
    block: list[Intervall],
    kandidat: Intervall,
    *,
    period: timedelta,
    tak: timedelta,
) -> list[AtlBrott]:
    for t in _fonsterstarter(block, kandidat, period):
        arbetstid = _arbetstid_i_fonster(block, t, t + period)
        if arbetstid > tak:
            return [
                AtlBrott(
                    "veckoarbetstid",
                    f"total arbetstid är {_fmt(arbetstid)} i perioden "
                    f"{t.isoformat()} – {(t + period).isoformat()}, "
                    f"taket är {_fmt(tak)}",
                )
            ]
    return []
