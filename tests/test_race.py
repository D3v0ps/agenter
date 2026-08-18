"""Race-testet (kärnkravet): minst 10 samtidiga JA-svar mot 3 platser,
mot riktig Postgres. Exakt 3 tilldelas — aldrig fler — och förfrågan
slutar i status fylld."""
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models import (
    Auditlogg,
    Bokning,
    BokningKalla,
    BokningStatus,
    Forfragan,
    ForfraganStatus,
    Plats,
)
from app.services import (
    godkann_forfragan,
    registrera_svar,
    registrera_utskick,
    skapa_forfragan,
)
from tests.hjalp import ny_konsult, ny_kund, ny_kvalifikation

ANTAL_KONSULTER = 10
ANTAL_PLATSER = 3
START = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
SLUT = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)


def test_race_exakt_tre_tilldelningar(sessionfabrik):
    with sessionfabrik() as session:
        kund = ny_kund(session)
        konsult_ids = []
        for i in range(1, ANTAL_KONSULTER + 1):
            konsult = ny_konsult(session, f"Konsult {i}", index=i)
            ny_kvalifikation(session, konsult, kund)
            konsult_ids.append(konsult.id)
        session.commit()
        fid = skapa_forfragan(
            session,
            kund_id=kund.id,
            antal_begarda=ANTAL_PLATSER,
            starttid=START,
            sluttid=SLUT,
            originaltext="race-scenario",
        )["forfragan_id"]
        godkann_forfragan(session, fid)
        utskick = registrera_utskick(session, fid, konsult_ids, "Pass ikväll, svara JA")
        utskick_ids = [u["utskick_id"] for u in utskick["skickade"]]

    barriar = threading.Barrier(ANTAL_KONSULTER)
    las = threading.Lock()
    utfall: list[dict] = []
    fel: list[Exception] = []

    def svara_ja(utskick_id: int) -> None:
        try:
            with sessionfabrik() as egen_session:
                barriar.wait()  # alla trådar svarar JA i exakt samma ögonblick
                resultat = registrera_svar(egen_session, utskick_id, "JA")
                with las:
                    utfall.append(resultat)
        except Exception as e:  # noqa: BLE001 — samlas och failar testet
            with las:
                fel.append(e)

    tradar = [
        threading.Thread(target=svara_ja, args=(uid,)) for uid in utskick_ids
    ]
    for trad in tradar:
        trad.start()
    for trad in tradar:
        trad.join(timeout=60)

    assert not fel, f"trådfel: {fel}"
    assert len(utfall) == ANTAL_KONSULTER

    tilldelade = [r for r in utfall if r["tilldelad"]]
    nekade = [r for r in utfall if not r["tilldelad"]]
    assert len(tilldelade) == ANTAL_PLATSER  # exakt N — aldrig fler
    assert len(nekade) == ANTAL_KONSULTER - ANTAL_PLATSER
    assert all(r["orsak"] == "fullt_besatt" for r in nekade)

    with sessionfabrik() as session:
        assert session.get(Forfragan, fid).status == ForfraganStatus.FYLLD
        fyllda_platser = session.scalar(
            select(func.count())
            .select_from(Plats)
            .where(Plats.forfragan_id == fid, Plats.konsult_id.is_not(None))
        )
        assert fyllda_platser == ANTAL_PLATSER
        interna_bokningar = session.scalar(
            select(func.count())
            .select_from(Bokning)
            .where(
                Bokning.kalla == BokningKalla.INTERN,
                Bokning.status == BokningStatus.BOKAD,
            )
        )
        assert interna_bokningar == ANTAL_PLATSER
        svar = session.scalar(
            select(func.count())
            .select_from(Auditlogg)
            .where(Auditlogg.handelse == "svar_registrerat")
        )
        assert svar == ANTAL_KONSULTER  # varje JA auditloggat, även nekade
        tilldelningar = session.scalar(
            select(func.count())
            .select_from(Auditlogg)
            .where(Auditlogg.handelse == "plats_tilldelad")
        )
        assert tilldelningar == ANTAL_PLATSER


def _seed_en_konsult(sessionfabrik):
    with sessionfabrik() as session:
        kund = ny_kund(session)
        konsult = ny_konsult(session, "Rask Rakel", index=1)
        ny_kvalifikation(session, konsult, kund)
        session.commit()
        return kund.id, konsult.id


def _kor_samtidigt(sessionfabrik, utskick_ids: list[int]) -> list[dict]:
    barriar = threading.Barrier(len(utskick_ids))
    las = threading.Lock()
    utfall: list[dict] = []
    fel: list[Exception] = []

    def svara(utskick_id: int) -> None:
        try:
            with sessionfabrik() as egen_session:
                barriar.wait()
                resultat = registrera_svar(egen_session, utskick_id, "JA")
                with las:
                    utfall.append(resultat)
        except Exception as e:  # noqa: BLE001
            with las:
                fel.append(e)

    tradar = [threading.Thread(target=svara, args=(uid,)) for uid in utskick_ids]
    for trad in tradar:
        trad.start()
    for trad in tradar:
        trad.join(timeout=60)
    assert not fel, f"trådfel: {fel}"
    return utfall


def test_race_dubbla_ja_fran_samma_konsult(sessionfabrik):
    """Samma konsult svarar JA två gånger samtidigt (dubblett-SMS):
    exakt EN tilldelning får ske; båda svaren returnerar samma tilldelning
    (idempotens)."""
    kund_id, konsult_id = _seed_en_konsult(sessionfabrik)
    with sessionfabrik() as session:
        fid = skapa_forfragan(
            session,
            kund_id=kund_id,
            antal_begarda=ANTAL_PLATSER,
            starttid=START,
            sluttid=SLUT,
        )["forfragan_id"]
        godkann_forfragan(session, fid)
        uid = registrera_utskick(session, fid, [konsult_id], "Svara JA")[
            "skickade"
        ][0]["utskick_id"]

    utfall = _kor_samtidigt(sessionfabrik, [uid, uid])

    assert len(utfall) == 2
    assert all(r["tilldelad"] is True for r in utfall)
    assert len({r["plats_id"] for r in utfall}) == 1  # samma plats båda gångerna

    with sessionfabrik() as session:
        fyllda = session.scalar(
            select(func.count())
            .select_from(Plats)
            .where(Plats.forfragan_id == fid, Plats.konsult_id.is_not(None))
        )
        assert fyllda == 1
        bokade = session.scalar(
            select(func.count())
            .select_from(Bokning)
            .where(
                Bokning.konsult_id == konsult_id,
                Bokning.status == BokningStatus.BOKAD,
            )
        )
        assert bokade == 1
        tilldelningar = session.scalar(
            select(func.count())
            .select_from(Auditlogg)
            .where(Auditlogg.handelse == "plats_tilldelad")
        )
        assert tilldelningar == 1


def test_race_tva_overlappande_forfragningar_samma_konsult(sessionfabrik):
    """Två förfrågningar med överlappande tid, samma konsult svarar JA på
    båda samtidigt: exakt EN tilldelning lyckas — överlappskontrollen i
    tilldelningstransaktionen plus databasens exclusion constraint stoppar
    dubbelbokningen."""
    kund_id, konsult_id = _seed_en_konsult(sessionfabrik)
    utskick_ids = []
    with sessionfabrik() as session:
        for forskjutning_h in (0, 2):  # 14-20 respektive 16-22 UTC
            fid = skapa_forfragan(
                session,
                kund_id=kund_id,
                antal_begarda=1,
                starttid=START + timedelta(hours=forskjutning_h),
                sluttid=SLUT + timedelta(hours=forskjutning_h),
            )["forfragan_id"]
            godkann_forfragan(session, fid)
            utskick_ids.append(
                registrera_utskick(session, fid, [konsult_id], "Svara JA")[
                    "skickade"
                ][0]["utskick_id"]
            )

    utfall = _kor_samtidigt(sessionfabrik, utskick_ids)

    tilldelade = [r for r in utfall if r["tilldelad"]]
    nekade = [r for r in utfall if not r["tilldelad"]]
    assert len(tilldelade) == 1
    assert len(nekade) == 1
    assert nekade[0]["orsak"] == "overlappande_bokning"

    with sessionfabrik() as session:
        bokade = session.scalar(
            select(func.count())
            .select_from(Bokning)
            .where(
                Bokning.konsult_id == konsult_id,
                Bokning.status == BokningStatus.BOKAD,
            )
        )
        assert bokade == 1
