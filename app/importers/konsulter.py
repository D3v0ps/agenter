"""Import av konsulter från Excel med svenska kolumnnamn."""
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
    tolka_bool,
)
from app.models import Konsult

KRAVDA_KOLUMNER = ("namn", "telefon", "personnummer")


def importera_konsulter(
    session: Session, sokvag: str | Path, aktor: str = "import"
) -> ImportRapport:
    """Importera konsulter från ett Excel-ark.

    Krävda kolumner: Namn, Telefon, Personnummer.
    Valfria kolumner: Anställningsform, Aktiv (ja/nej, standard ja).

    - Telefon normaliseras till E.164 (+46...).
    - Personnummer Luhn-valideras och lagras ENDAST som HMAC-SHA256-hash.
    - Saknas en krävd kolumn rapporteras vilka som saknas; inget importeras
      och inget kraschar.
    - Fel på enskilda rader samlas i rapporten; övriga rader importeras.
    - Befintlig konsult (samma personnummer_hash) uppdateras i stället för
      att dubbleras. Allt auditloggas.
    """
    rapport = ImportRapport()
    kolumner, rader = las_excel(sokvag)
    rapport.saknade_kolumner = [k for k in KRAVDA_KOLUMNER if k not in kolumner]
    if not rapport.genomford:
        return rapport

    for radnr, rad in rader:
        try:
            namn = str(rad.get("namn") or "").strip()
            if not namn:
                raise Normaliseringsfel("namn saknas")
            telefon = normalisera_telefon(rad.get("telefon"))
            pnr_hash = hash_personnummer(normalisera_personnummer(rad.get("personnummer")))
            aktiv = tolka_bool(rad.get("aktiv"), standard=True)
            form = str(rad.get("anställningsform") or "").strip()

            with session.begin_nested():
                befintlig = session.scalar(
                    select(Konsult).where(Konsult.personnummer_hash == pnr_hash)
                )
                if befintlig is not None:
                    fore = {
                        "konsult_id": befintlig.id,
                        "namn": befintlig.namn,
                        "telefon": befintlig.telefon,
                        "aktiv": befintlig.aktiv,
                        "anstallningsform": befintlig.anstallningsform,
                    }
                    befintlig.namn = namn
                    befintlig.telefon = telefon
                    befintlig.aktiv = aktiv
                    befintlig.anstallningsform = form
                    session.flush()
                    skriv_audit(
                        session,
                        aktor,
                        "konsult_uppdaterad_via_import",
                        fore=fore,
                        efter={
                            "konsult_id": befintlig.id,
                            "namn": namn,
                            "telefon": telefon,
                            "aktiv": aktiv,
                            "anstallningsform": form,
                        },
                    )
                    rapport.uppdaterade += 1
                else:
                    konsult = Konsult(
                        namn=namn,
                        telefon=telefon,
                        personnummer_hash=pnr_hash,
                        aktiv=aktiv,
                        anstallningsform=form,
                    )
                    session.add(konsult)
                    session.flush()
                    skriv_audit(
                        session,
                        aktor,
                        "konsult_importerad",
                        efter={
                            "konsult_id": konsult.id,
                            "namn": namn,
                            "telefon": telefon,
                            "aktiv": aktiv,
                            "anstallningsform": form,
                        },
                    )
                    rapport.skapade += 1
        except Normaliseringsfel as fel:
            rapport.radfel.append(Radfel(radnr, str(fel)))
        except IntegrityError:
            rapport.radfel.append(
                Radfel(radnr, "telefonnumret används redan av en annan konsult")
            )

    session.commit()
    return rapport
