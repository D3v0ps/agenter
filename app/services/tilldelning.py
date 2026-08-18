"""Atomär tilldelning och släpp av platser.

Samtidighetsskyddet i tre lager (defense in depth):
1. Radlåsning: en ledig platsrad tas med SELECT ... FOR UPDATE SKIP LOCKED —
   samtidiga JA konkurrerar om ett ändligt antal rader, aldrig om en räknare.
2. Partiellt unikt index: samma konsult kan inte hålla två platser på samma
   förfrågan.
3. Constraint-trigger (migration 0002) + exclusion constraint (migration
   0001): även en buggig framtida kodväg kan inte överboka en förfrågan
   eller dubbelboka en konsult.
"""
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import skriv_audit
from app.models import (
    Bokning,
    BokningKalla,
    BokningStatus,
    Forfragan,
    ForfraganStatus,
    Plats,
)
from app.services.gemensamt import HittadesInte, i_transaktion
from app.statusmaskin import byt_status
from app.tid import nu_utc

# Fylld ingår: ett sent JA ska få det konsekventa svaret "fullt_besatt"
# (via radsökningen), inte ett statusfel.
_TILLDELNINGSBARA = (
    ForfraganStatus.UTSKICKAD,
    ForfraganStatus.DELVIS_FYLLD,
    ForfraganStatus.FYLLD,
)


@dataclass
class TilldelningsUtfall:
    tilldelad: bool
    orsak: str | None = None
    plats_id: int | None = None
    bokning_id: int | None = None

    def som_dict(self) -> dict:
        return {
            "tilldelad": self.tilldelad,
            "orsak": self.orsak,
            "plats_id": self.plats_id,
            "bokning_id": self.bokning_id,
        }


def _nekad(
    session: Session, forfragan_id: int, konsult_id: int, aktor: str, orsak: str
) -> TilldelningsUtfall:
    skriv_audit(
        session,
        aktor,
        "tilldelning_nekad",
        efter={"forfragan_id": forfragan_id, "konsult_id": konsult_id, "orsak": orsak},
    )
    return TilldelningsUtfall(False, orsak)


def _tilldela(
    session: Session, forfragan_id: int, konsult_id: int, aktor: str
) -> TilldelningsUtfall:
    """Kärnan i tilldelningen. Körs inom anroparens transaktion."""
    forfragan = session.get(Forfragan, forfragan_id)
    if forfragan is None:
        raise HittadesInte(f"förfrågan {forfragan_id} finns inte")
    if forfragan.status not in _TILLDELNINGSBARA:
        return _nekad(
            session,
            forfragan_id,
            konsult_id,
            aktor,
            f"forfragan_i_status_{forfragan.status.value}",
        )

    redan = session.scalar(
        select(Plats.id).where(
            Plats.forfragan_id == forfragan_id, Plats.konsult_id == konsult_id
        )
    )
    if redan is not None:
        return _nekad(session, forfragan_id, konsult_id, aktor, "redan_tilldelad")

    # Först till kvarn: ta en ledig platsrad, hoppa över rader som en
    # samtidig transaktion redan håller låsta.
    plats = session.execute(
        select(Plats)
        .where(Plats.forfragan_id == forfragan_id, Plats.konsult_id.is_(None))
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if plats is None:
        return _nekad(session, forfragan_id, konsult_id, aktor, "fullt_besatt")

    try:
        with session.begin_nested():
            bokning = Bokning(
                konsult_id=konsult_id,
                kund_id=forfragan.kund_id,
                starttid=forfragan.starttid,
                sluttid=forfragan.sluttid,
                status=BokningStatus.BOKAD,
                kalla=BokningKalla.INTERN,
            )
            session.add(bokning)
            session.flush()
            plats.konsult_id = konsult_id
            plats.bokning_id = bokning.id
            plats.tilldelad = nu_utc()
            session.flush()  # unikt index + constraint-trigger körs här
    except IntegrityError as fel:
        return _nekad(session, forfragan_id, konsult_id, aktor, _tolka_databassparr(fel))

    # Lås förfrågningsraden och räkna om status. Låset serialiserar
    # statusberäkningen; räkningen ser då både egna och committade rader.
    forfragan = session.execute(
        select(Forfragan)
        .where(Forfragan.id == forfragan_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    fyllda = session.scalar(
        select(func.count())
        .select_from(Plats)
        .where(Plats.forfragan_id == forfragan_id, Plats.konsult_id.is_not(None))
    )
    ny_status = (
        ForfraganStatus.FYLLD
        if fyllda >= forfragan.antal_begarda
        else ForfraganStatus.DELVIS_FYLLD
    )
    if forfragan.status != ny_status:
        byt_status(session, forfragan, ny_status, aktor)
    skriv_audit(
        session,
        aktor,
        "plats_tilldelad",
        efter={
            "forfragan_id": forfragan_id,
            "plats_id": plats.id,
            "konsult_id": konsult_id,
            "bokning_id": bokning.id,
        },
    )
    return TilldelningsUtfall(True, plats_id=plats.id, bokning_id=bokning.id)


def _tolka_databassparr(fel: IntegrityError) -> str:
    namn = ""
    diag = getattr(fel.orig, "diag", None)
    if diag is not None:
        namn = diag.constraint_name or ""
    if "ex_bokning_overlapp" in namn:
        return "overlappande_bokning"
    if "uq_plats" in namn:
        return "redan_tilldelad"
    return "databassparr"


def tilldela_plats(
    session: Session, forfragan_id: int, konsult_id: int, aktor: str = "system"
) -> dict:
    """Tilldela en plats på förfrågan till konsulten — atomärt, först till
    kvarn. Vid N platser och fler samtidiga JA än N tilldelas exakt N,
    aldrig fler: lediga platsrader tas med radlås (FOR UPDATE SKIP LOCKED)
    och databasens constraint-trigger gör överbokning omöjlig även utanför
    denna kodväg. Lyckad tilldelning skapar en intern bokning och
    uppdaterar förfrågans status (utskickad/delvis_fylld → fylld när sista
    platsen tas). Allt auditloggas.

    Returnerar {"tilldelad": bool, "orsak": str | None, "plats_id",
    "bokning_id"}. Orsaker vid nekad tilldelning: "fullt_besatt",
    "redan_tilldelad", "overlappande_bokning",
    "forfragan_i_status_<status>".
    """
    with i_transaktion(session):
        utfall = _tilldela(session, forfragan_id, konsult_id, aktor)
    return utfall.som_dict()


def slapp_plats(
    session: Session,
    forfragan_id: int,
    konsult_id: int,
    *,
    orsak: str = "",
    aktor: str = "system",
) -> dict:
    """Konsulten drar sig ur: platsen öppnas igen och den interna bokningen
    avbokas. En fylld förfrågan går tillbaka till delvis_fylld och kan
    fyllas på nytt. (En redan stängd förfrågan behåller sin status, men
    platsen släpps och bokningen avbokas — avhopp måste alltid kunna
    registreras.) Allt auditloggas.

    Returnerar {"forfragan_id", "konsult_id", "status"}.
    """
    with i_transaktion(session):
        plats = session.execute(
            select(Plats)
            .where(Plats.forfragan_id == forfragan_id, Plats.konsult_id == konsult_id)
            .with_for_update()
        ).scalar_one_or_none()
        if plats is None:
            raise HittadesInte(
                f"konsult {konsult_id} har ingen plats på förfrågan {forfragan_id}"
            )
        if plats.bokning_id is not None:
            bokning = session.get(Bokning, plats.bokning_id)
            if bokning is not None and bokning.status == BokningStatus.BOKAD:
                bokning.status = BokningStatus.AVBOKAD
                skriv_audit(
                    session,
                    aktor,
                    "bokning_avbokad",
                    fore={"bokning_id": bokning.id, "status": "bokad"},
                    efter={"bokning_id": bokning.id, "status": "avbokad", "orsak": orsak},
                )
        plats.konsult_id = None
        plats.bokning_id = None
        plats.tilldelad = None
        session.flush()

        forfragan = session.execute(
            select(Forfragan)
            .where(Forfragan.id == forfragan_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        if forfragan.status == ForfraganStatus.FYLLD:
            byt_status(
                session,
                forfragan,
                ForfraganStatus.DELVIS_FYLLD,
                aktor,
                extra={"orsak": orsak} if orsak else None,
            )
        skriv_audit(
            session,
            aktor,
            "plats_slappt",
            efter={
                "forfragan_id": forfragan_id,
                "plats_id": plats.id,
                "konsult_id": konsult_id,
                "orsak": orsak,
            },
        )
    return {
        "forfragan_id": forfragan_id,
        "konsult_id": konsult_id,
        "status": forfragan.status.value,
    }
