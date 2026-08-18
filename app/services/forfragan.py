"""Verktygsfunktioner för förfrågans livscykel.

Alla funktioner här är tänkta att exponeras som MCP-verktyg för ett framtida
AI-agentlager. De tar en Session, är atomära (committar själva eller rullar
tillbaka), returnerar JSON-vänliga dictar och auditloggar varje ändring."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import skriv_audit
from app.models import Forfragan, ForfraganStatus, Konsult, Kund, Plats, Utskick
from app.services.gemensamt import HittadesInte, hamta_forfragan, i_transaktion
from app.statusmaskin import byt_status
from app.tid import till_utc


def skapa_forfragan(
    session: Session,
    *,
    kund_id: int,
    antal_begarda: int,
    starttid: datetime,
    sluttid: datetime,
    originaltext: str = "",
    aktor: str = "system",
) -> dict:
    """Skapa en ny passförfrågan i status 'mottagen'.

    Skapar exakt antal_begarda platsrader — det strukturella taket som gör
    tilldelning över begärt antal omöjlig på databasnivå. Naiva tider tolkas
    som Europe/Stockholm.

    Returnerar {"forfragan_id", "status", "antal_begarda"}.
    """
    if antal_begarda < 1:
        raise ValueError("antal_begarda måste vara minst 1")
    starttid, sluttid = till_utc(starttid), till_utc(sluttid)
    if sluttid <= starttid:
        raise ValueError("sluttid måste vara efter starttid")
    with i_transaktion(session):
        if session.get(Kund, kund_id) is None:
            raise HittadesInte(f"kund {kund_id} finns inte")
        forfragan = Forfragan(
            kund_id=kund_id,
            antal_begarda=antal_begarda,
            starttid=starttid,
            sluttid=sluttid,
            status=ForfraganStatus.MOTTAGEN,
            originaltext=originaltext,
        )
        session.add(forfragan)
        session.flush()
        for _ in range(antal_begarda):
            session.add(Plats(forfragan_id=forfragan.id))
        session.flush()
        skriv_audit(
            session,
            aktor,
            "forfragan_skapad",
            efter={
                "forfragan_id": forfragan.id,
                "kund_id": kund_id,
                "antal_begarda": antal_begarda,
                "starttid": starttid.isoformat(),
                "sluttid": sluttid.isoformat(),
                "originaltext": originaltext,
                "status": ForfraganStatus.MOTTAGEN.value,
            },
        )
    return {
        "forfragan_id": forfragan.id,
        "status": forfragan.status.value,
        "antal_begarda": antal_begarda,
    }


def godkann_forfragan(session: Session, forfragan_id: int, aktor: str = "system") -> dict:
    """Godkänn en mottagen förfrågan (mottagen → godkänd).

    Godkännandet är den mänskliga/agentstyrda grinden innan utskick får
    registreras. Returnerar {"forfragan_id", "status"}.
    """
    with i_transaktion(session):
        forfragan = hamta_forfragan(session, forfragan_id, las=True)
        byt_status(session, forfragan, ForfraganStatus.GODKAND, aktor)
    return {"forfragan_id": forfragan.id, "status": forfragan.status.value}


def stang_forfragan(
    session: Session,
    forfragan_id: int,
    *,
    eskalera: bool = False,
    orsak: str = "",
    aktor: str = "system",
) -> dict:
    """Avsluta en förfrågan: status 'stängd', eller 'eskalerad' om
    eskalera=True (t.ex. när platser inte kunnat fyllas i tid och en
    människa ska ta över). Terminala tillstånd — inga vidare övergångar.

    Returnerar {"forfragan_id", "status"}.
    """
    ny = ForfraganStatus.ESKALERAD if eskalera else ForfraganStatus.STANGD
    with i_transaktion(session):
        forfragan = hamta_forfragan(session, forfragan_id, las=True)
        byt_status(session, forfragan, ny, aktor, extra={"orsak": orsak} if orsak else None)
    return {"forfragan_id": forfragan.id, "status": forfragan.status.value}


def forfragan_status(session: Session, forfragan_id: int) -> dict:
    """Läsbar statusrapport för en förfrågan: status, platser med tilldelade
    konsulter, samtliga utskick med svar. Enbart läsning — ändrar ingenting.
    """
    forfragan = hamta_forfragan(session, forfragan_id)
    platser = list(
        session.scalars(
            select(Plats).where(Plats.forfragan_id == forfragan_id).order_by(Plats.id)
        )
    )
    utskick = list(
        session.scalars(
            select(Utskick).where(Utskick.forfragan_id == forfragan_id).order_by(Utskick.id)
        )
    )
    konsultnamn = {
        k.id: k.namn
        for k in session.scalars(
            select(Konsult).where(
                Konsult.id.in_(
                    {p.konsult_id for p in platser if p.konsult_id is not None}
                    | {u.konsult_id for u in utskick}
                )
            )
        )
    }
    return {
        "forfragan_id": forfragan.id,
        "kund": session.get(Kund, forfragan.kund_id).namn,
        "status": forfragan.status.value,
        "antal_begarda": forfragan.antal_begarda,
        "starttid": forfragan.starttid.isoformat(),
        "sluttid": forfragan.sluttid.isoformat(),
        "originaltext": forfragan.originaltext,
        "antal_tilldelade": sum(1 for p in platser if p.konsult_id is not None),
        "platser": [
            {
                "plats_id": p.id,
                "konsult_id": p.konsult_id,
                "konsult": konsultnamn.get(p.konsult_id),
                "tilldelad": p.tilldelad.isoformat() if p.tilldelad else None,
            }
            for p in platser
        ],
        "utskick": [
            {
                "utskick_id": u.id,
                "konsult_id": u.konsult_id,
                "konsult": konsultnamn.get(u.konsult_id),
                "skickad": u.skickad.isoformat() if u.skickad else None,
                "svar": u.svar,
                "svarstid": u.svarstid.isoformat() if u.svarstid else None,
            }
            for u in utskick
        ],
    }
