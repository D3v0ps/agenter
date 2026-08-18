"""Import av befintliga bokningar (pass) från Excel med svenska kolumnnamn.

Importerade pass får källa=importerad och räknas fullt ut i både
överlappskontrollen och ATL-spärren."""
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import skriv_audit
from app.importers.gemensamt import (
    ImportRapport,
    Normaliseringsfel,
    Radfel,
    hash_personnummer,
    las_excel,
    normalisera_personnummer,
    normalisera_telefon,
    tolka_tid,
)
from app.models import Bokning, BokningKalla, BokningStatus, Konsult, Kund

KRAVDA_KOLUMNER = ("kund", "starttid", "sluttid")


def importera_bokningar(
    session: Session, sokvag: str | Path, aktor: str = "import"
) -> ImportRapport:
    """Importera befintliga bokningar från ett Excel-ark.

    Krävda kolumner: Kund, Starttid, Sluttid samt Telefon eller Personnummer
    (för att identifiera konsulten; personnummer har företräde).

    - Naiva tider tolkas som Europe/Stockholm och lagras i UTC.
    - Okänd konsult ger radfel.
    - Okänd kund skapas automatiskt (med tom ort) och auditloggas.
    - Rad som exakt matchar en befintlig aktiv bokning hoppas över.
    - Rad som överlappar en annan aktiv bokning för samma konsult ger radfel
      (databasens exclusion constraint gör dubbelbokning omöjlig).
    """
    rapport = ImportRapport()
    kolumner, rader = las_excel(sokvag)
    rapport.saknade_kolumner = [k for k in KRAVDA_KOLUMNER if k not in kolumner]
    har_telefon = "telefon" in kolumner
    har_pnr = "personnummer" in kolumner
    if not har_telefon and not har_pnr:
        rapport.saknade_kolumner.append("telefon eller personnummer")
    if not rapport.genomford:
        return rapport

    for radnr, rad in rader:
        try:
            konsult = _hitta_konsult(session, rad, har_pnr, har_telefon)
            kundnamn = str(rad.get("kund") or "").strip()
            if not kundnamn:
                raise Normaliseringsfel("kund saknas")
            starttid = tolka_tid(rad.get("starttid"))
            sluttid = tolka_tid(rad.get("sluttid"))
            if sluttid <= starttid:
                raise Normaliseringsfel("sluttid är inte efter starttid")

            with session.begin_nested():
                kund = session.scalar(select(Kund).where(Kund.namn == kundnamn))
                if kund is None:
                    kund = Kund(namn=kundnamn, ort="")
                    session.add(kund)
                    session.flush()
                    skriv_audit(
                        session,
                        aktor,
                        "kund_skapad_vid_import",
                        efter={"kund_id": kund.id, "namn": kundnamn},
                    )
                    rapport.skapade_kunder.append(kundnamn)

            dubblett = session.scalar(
                select(Bokning).where(
                    Bokning.konsult_id == konsult.id,
                    Bokning.kund_id == kund.id,
                    Bokning.starttid == starttid,
                    Bokning.sluttid == sluttid,
                    Bokning.status == BokningStatus.BOKAD,
                )
            )
            if dubblett is not None:
                rapport.hoppade_over += 1
                continue

            with session.begin_nested():
                bokning = Bokning(
                    konsult_id=konsult.id,
                    kund_id=kund.id,
                    starttid=starttid,
                    sluttid=sluttid,
                    status=BokningStatus.BOKAD,
                    kalla=BokningKalla.IMPORTERAD,
                )
                session.add(bokning)
                session.flush()
                skriv_audit(
                    session,
                    aktor,
                    "bokning_importerad",
                    efter={
                        "bokning_id": bokning.id,
                        "konsult_id": konsult.id,
                        "kund_id": kund.id,
                        "starttid": starttid.isoformat(),
                        "sluttid": sluttid.isoformat(),
                    },
                )
                rapport.skapade += 1
        except Normaliseringsfel as fel:
            rapport.radfel.append(Radfel(radnr, str(fel)))
        except IntegrityError:
            rapport.radfel.append(
                Radfel(radnr, "överlappar en befintlig bokning för konsulten")
            )

    session.commit()
    return rapport


def _hitta_konsult(
    session: Session, rad: dict[str, object], har_pnr: bool, har_telefon: bool
) -> Konsult:
    konsult = None
    if har_pnr and rad.get("personnummer") is not None:
        pnr_hash = hash_personnummer(normalisera_personnummer(rad["personnummer"]))
        konsult = session.scalar(
            select(Konsult).where(Konsult.personnummer_hash == pnr_hash)
        )
    elif har_telefon and rad.get("telefon") is not None:
        telefon = normalisera_telefon(rad["telefon"])
        konsult = session.scalar(select(Konsult).where(Konsult.telefon == telefon))
    else:
        raise Normaliseringsfel("telefon eller personnummer saknas på raden")
    if konsult is None:
        raise Normaliseringsfel("okänd konsult (importera konsulter först)")
    return konsult
