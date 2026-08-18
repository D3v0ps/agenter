"""Verktygsfunktioner för utskick och inkommande svar.

Kärnan skickar inga SMS — den registrerar att utskick gjorts och tar emot
svar. Själva sändningen (och AI-tolkning av fritextsvar) ligger i senare
lager; svarstolkningen här är medvetet strikt och deterministisk."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import skriv_audit
from app.models import ForfraganStatus, Konsult, Utskick
from app.repositories.kvalificering import kvalificerade_konsulter
from app.services.gemensamt import HittadesInte, hamta_forfragan, i_transaktion
from app.services.tilldelning import _tilldela
from app.statusmaskin import OgiltigOvergang, byt_status
from app.tid import nu_utc

_JA = frozenset({"ja", "j", "yes", "ok", "okej", "absolut", "japp"})
_NEJ = frozenset({"nej", "n", "no", "inte", "kan ej", "kan inte"})


def tolka_svar(svarstext: str) -> str:
    """Deterministisk tolkning av en svarstext: 'ja', 'nej' eller 'okant'.
    Endast entydiga svar räknas; allt annat är 'okant' och leder inte till
    någon tilldelning."""
    s = svarstext.strip().lower().rstrip("!.?")
    if s in _JA:
        return "ja"
    if s in _NEJ:
        return "nej"
    return "okant"


def lista_kvalificerade(session: Session, forfragan_id: int) -> list[dict]:
    """Lista konsulter som är kvalificerade för förfrågan: aktiv anställning,
    genomförd introduktion hos kunden och ingen bokning som överlappar
    tidsfönstret (alla bokningskällor räknas).

    HÅRD REGEL: ordningen är alltid slumpmässig — ingen rangordning,
    poängsättning eller sortering på lämplighet förekommer.

    Returnerar [{"konsult_id", "namn", "telefon"}]. Enbart läsning.
    """
    forfragan = hamta_forfragan(session, forfragan_id)
    konsulter = kvalificerade_konsulter(
        session, forfragan.kund_id, forfragan.starttid, forfragan.sluttid
    )
    return [
        {"konsult_id": k.id, "namn": k.namn, "telefon": k.telefon} for k in konsulter
    ]


def registrera_utskick(
    session: Session,
    forfragan_id: int,
    konsult_ids: list[int],
    meddelandetext: str,
    aktor: str = "system",
) -> dict:
    """Registrera att ett erbjudande skickats till angivna konsulter.

    Tillåtet i status godkänd (första utskicket — förfrågan går till
    utskickad), utskickad eller delvis_fylld (kompletterande utskick, t.ex.
    efter ett avhopp). Konsulter som redan fått utskick på förfrågan hoppas
    över. Allt auditloggas.

    Returnerar {"status", "skickade": [{"utskick_id", "konsult_id"}],
    "hoppade_over": [konsult_id, ...]}.
    """
    if not konsult_ids:
        raise ValueError("minst en konsult krävs för ett utskick")
    with i_transaktion(session):
        forfragan = hamta_forfragan(session, forfragan_id)
        if forfragan.status == ForfraganStatus.GODKAND:
            byt_status(session, forfragan, ForfraganStatus.UTSKICKAD, aktor)
        elif forfragan.status not in (
            ForfraganStatus.UTSKICKAD,
            ForfraganStatus.DELVIS_FYLLD,
        ):
            raise OgiltigOvergang(forfragan.status, ForfraganStatus.UTSKICKAD)

        skickade: list[dict] = []
        hoppade: list[int] = []
        for konsult_id in konsult_ids:
            if session.get(Konsult, konsult_id) is None:
                raise HittadesInte(f"konsult {konsult_id} finns inte")
            redan = session.scalar(
                select(Utskick.id).where(
                    Utskick.forfragan_id == forfragan_id,
                    Utskick.konsult_id == konsult_id,
                )
            )
            if redan is not None:
                hoppade.append(konsult_id)
                continue
            utskick = Utskick(
                forfragan_id=forfragan_id,
                konsult_id=konsult_id,
                meddelandetext=meddelandetext,
            )
            session.add(utskick)
            session.flush()
            skriv_audit(
                session,
                aktor,
                "utskick_registrerat",
                efter={
                    "utskick_id": utskick.id,
                    "forfragan_id": forfragan_id,
                    "konsult_id": konsult_id,
                    "meddelandetext": meddelandetext,
                },
            )
            skickade.append({"utskick_id": utskick.id, "konsult_id": konsult_id})
    return {
        "status": forfragan.status.value,
        "skickade": skickade,
        "hoppade_over": hoppade,
    }


def registrera_svar(
    session: Session, utskick_id: int, svarstext: str, aktor: str = "system"
) -> dict:
    """Registrera ett inkommet svar på ett utskick.

    Svarstexten lagras rått på utskicket och tolkas deterministiskt
    (tolka_svar). Vid JA görs ett atomärt tilldelningsförsök — först till
    kvarn; kommer JA:et när platserna redan är tagna blir utfallet
    tilldelad=False med orsak "fullt_besatt". Allt auditloggas.

    Returnerar {"utskick_id", "konsult_id", "svar": "ja|nej|okant",
    "tilldelad": bool, "orsak": str | None}.
    """
    with i_transaktion(session):
        utskick = session.get(Utskick, utskick_id)
        if utskick is None:
            raise HittadesInte(f"utskick {utskick_id} finns inte")
        utskick.svar = svarstext
        utskick.svarstid = nu_utc()
        tolkning = tolka_svar(svarstext)
        skriv_audit(
            session,
            aktor,
            "svar_registrerat",
            efter={
                "utskick_id": utskick.id,
                "forfragan_id": utskick.forfragan_id,
                "konsult_id": utskick.konsult_id,
                "svar": svarstext,
                "tolkning": tolkning,
            },
        )
        resultat = {
            "utskick_id": utskick.id,
            "konsult_id": utskick.konsult_id,
            "svar": tolkning,
            "tilldelad": False,
            "orsak": None,
        }
        if tolkning == "ja":
            utfall = _tilldela(session, utskick.forfragan_id, utskick.konsult_id, aktor)
            resultat.update(
                tilldelad=utfall.tilldelad,
                orsak=utfall.orsak,
                plats_id=utfall.plats_id,
                bokning_id=utfall.bokning_id,
            )
    return resultat
