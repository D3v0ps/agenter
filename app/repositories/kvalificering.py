"""Kvalificeringsfrågan: vilka konsulter får erbjudas ett pass?

HÅRD REGEL: Ordningen är ALLTID slumpmässig (ORDER BY random() i Postgres).
Ingen rangordning, poängsättning eller sortering på lämplighet får förekomma
— detta är ett medvetet juridiskt designval. Ändra aldrig order_by här."""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Bokning, BokningStatus, Konsult, Kvalifikation
from app.tid import till_lokal


def kvalificerade_konsulter(
    session: Session, kund_id: int, starttid: datetime, sluttid: datetime
) -> list[Konsult]:
    """Konsulter som för givet kund + tidsfönster har:
    - aktiv anställning,
    - genomförd introduktion hos kunden (introduktionsdatum satt och senast
      på passets startdatum, lokal tid),
    - ingen överlappande bokning (alla källor, status bokad).

    Returneras i slumpmässig ordning."""
    intro_senast = till_lokal(starttid).date()

    overlappande_bokning = (
        select(Bokning.id)
        .where(
            Bokning.konsult_id == Konsult.id,
            Bokning.status == BokningStatus.BOKAD,
            Bokning.starttid < sluttid,
            Bokning.sluttid > starttid,
        )
        .exists()
    )

    stmt = (
        select(Konsult)
        .join(Kvalifikation, Kvalifikation.konsult_id == Konsult.id)
        .where(
            Konsult.aktiv.is_(True),
            Kvalifikation.kund_id == kund_id,
            Kvalifikation.introduktionsdatum.is_not(None),
            Kvalifikation.introduktionsdatum <= intro_senast,
            ~overlappande_bokning,
        )
        .order_by(func.random())  # HÅRD REGEL: slumpmässig ordning, aldrig rangordning
    )
    return list(session.scalars(stmt))
