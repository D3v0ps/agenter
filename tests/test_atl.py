"""ATL-spärren: dygnsvila (11 h/24 h), veckovila (36 h/7 dygn) och taket för
arbetstid per rullande sjudagarsperiod (48 h). Kontrollen räknar mot ALLA
aktiva bokningar, även importerade befintliga pass.

Basdatum: måndag 2026-10-05. Alla tider i UTC."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import Auditlogg, BokningKalla, BokningStatus
from app.services import (
    HittadesInte,
    godkann_forfragan,
    kontrollera_atl,
    registrera_utskick,
    skapa_forfragan,
    tilldela_plats,
)
from tests.hjalp import ny_bokning, ny_konsult, ny_kund, ny_kvalifikation


def _tid(dag: int, timme: int, minut: int = 0) -> datetime:
    """Klockslag på 'dag' dagar efter måndag 2026-10-05."""
    return datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(
        days=dag, hours=timme, minutes=minut
    )


@pytest.fixture()
def miljo(session):
    kund = ny_kund(session)
    konsult = ny_konsult(session, "Nina Nilsson", index=1)
    ny_kvalifikation(session, konsult, kund)
    session.commit()
    return session, kund, konsult


def test_nattpass_over_dygnsgrans_bryter_dygnsvila(miljo):
    session, kund, konsult = miljo
    ny_bokning(session, konsult, kund, _tid(0, 20), _tid(1, 4))  # natt 20-04
    session.commit()
    resultat = kontrollera_atl(session, konsult.id, _tid(1, 6), _tid(1, 14))
    assert resultat["ok"] is False
    assert [b["regel"] for b in resultat["brott"]] == ["dygnsvila"]


def test_exakt_11_timmars_vila_ar_ok(miljo):
    session, kund, konsult = miljo
    ny_bokning(session, konsult, kund, _tid(0, 8), _tid(0, 21))  # 13 h pass
    session.commit()
    # nästa pass börjar exakt 11 h efter 21:00 → 08:00 dagen därpå
    resultat = kontrollera_atl(session, konsult.id, _tid(1, 8), _tid(1, 16))
    assert resultat["ok"] is True
    assert resultat["brott"] == []


def test_en_minut_for_lite_dygnsvila_bryter(miljo):
    session, kund, konsult = miljo
    ny_bokning(session, konsult, kund, _tid(0, 8), _tid(0, 21))
    session.commit()
    # 10 h 59 min vila i stället för 11 h
    resultat = kontrollera_atl(session, konsult.id, _tid(1, 7, 59), _tid(1, 16))
    assert resultat["ok"] is False
    assert [b["regel"] for b in resultat["brott"]] == ["dygnsvila"]


def test_veckovila_brott_utan_36h_sammanhangande(miljo):
    session, kund, konsult = miljo
    # sex dagar i rad med korta pass (6 h) — dygnsvilan klaras (18 h/natt),
    # totalen klaras (42 h), men ingen 36-timmarsvila finns i perioden
    for dag in range(6):
        ny_bokning(session, konsult, kund, _tid(dag, 8), _tid(dag, 14))
    session.commit()
    resultat = kontrollera_atl(session, konsult.id, _tid(6, 8), _tid(6, 14))
    assert resultat["ok"] is False
    assert [b["regel"] for b in resultat["brott"]] == ["veckovila"]


def test_veckovila_ok_med_ledig_helg(miljo):
    session, kund, konsult = miljo
    for dag in range(5):  # måndag-fredag 08-16
        ny_bokning(session, konsult, kund, _tid(dag, 8), _tid(dag, 16))
    session.commit()
    # nästa måndag efter ledig helg (64 h vila)
    resultat = kontrollera_atl(session, konsult.id, _tid(7, 8), _tid(7, 16))
    assert resultat["ok"] is True


def test_ackumulering_over_48h_taket_bryter(miljo):
    session, kund, konsult = miljo
    for dag in range(5):  # måndag-fredag 08-17 → 45 h
        ny_bokning(session, konsult, kund, _tid(dag, 8), _tid(dag, 17))
    session.commit()
    # 4 h till på söndagen → 49 h inom rullande sju dygn
    resultat = kontrollera_atl(session, konsult.id, _tid(6, 8), _tid(6, 12))
    assert resultat["ok"] is False
    assert [b["regel"] for b in resultat["brott"]] == ["veckoarbetstid"]


def test_exakt_48h_ar_ok(miljo):
    session, kund, konsult = miljo
    for dag in range(4):  # måndag-torsdag 08-19 → 44 h
        ny_bokning(session, konsult, kund, _tid(dag, 8), _tid(dag, 19))
    session.commit()
    # 4 h på lördagen → exakt 48 h, taket är inte överskridet
    resultat = kontrollera_atl(session, konsult.id, _tid(5, 8), _tid(5, 12))
    assert resultat["ok"] is True


def test_avbokade_pass_raknas_inte(miljo):
    session, kund, konsult = miljo
    ny_bokning(
        session,
        konsult,
        kund,
        _tid(0, 8),
        _tid(1, 7),
        status=BokningStatus.AVBOKAD,
    )
    session.commit()
    resultat = kontrollera_atl(session, konsult.id, _tid(1, 8), _tid(1, 16))
    assert resultat["ok"] is True


def test_interna_pass_raknas_precis_som_importerade(miljo):
    session, kund, konsult = miljo
    ny_bokning(
        session, konsult, kund, _tid(0, 20), _tid(1, 4), kalla=BokningKalla.INTERN
    )
    session.commit()
    resultat = kontrollera_atl(session, konsult.id, _tid(1, 6), _tid(1, 14))
    assert resultat["ok"] is False
    assert [b["regel"] for b in resultat["brott"]] == ["dygnsvila"]


def test_kontrollera_atl_validering(miljo):
    session, _, konsult = miljo
    with pytest.raises(HittadesInte):
        kontrollera_atl(session, 99999, _tid(1, 8), _tid(1, 16))
    with pytest.raises(ValueError):
        kontrollera_atl(session, konsult.id, _tid(1, 16), _tid(1, 8))


def test_registrera_utskick_blockerar_atl_brott(session):
    kund = ny_kund(session)
    ledig = ny_konsult(session, "Ledig Larsson", index=1)
    trott = ny_konsult(session, "Trött Tapper", index=2)
    for konsult in (ledig, trott):
        ny_kvalifikation(session, konsult, kund)
    # Trött har ett importerat nattpass som slutar 02:00 samma morgon
    ny_bokning(session, trott, kund, _tid(0, 18), _tid(1, 2))
    session.commit()

    fid = skapa_forfragan(
        session,
        kund_id=kund.id,
        antal_begarda=2,
        starttid=_tid(1, 6),
        sluttid=_tid(1, 14),
    )["forfragan_id"]
    godkann_forfragan(session, fid)

    utfall = registrera_utskick(session, fid, [ledig.id, trott.id], "Pass imorgon!")
    assert utfall["status"] == "utskickad"
    assert [u["konsult_id"] for u in utfall["skickade"]] == [ledig.id]
    assert len(utfall["blockerade_atl"]) == 1
    assert utfall["blockerade_atl"][0]["konsult_id"] == trott.id
    assert utfall["blockerade_atl"][0]["brott"][0]["regel"] == "dygnsvila"

    antal_blockerade = session.scalar(
        select(func.count())
        .select_from(Auditlogg)
        .where(Auditlogg.handelse == "utskick_blockerat_atl")
    )
    assert antal_blockerade == 1

    # bältet + hängslen: direkt tilldelning stoppas också
    tilldelning = tilldela_plats(session, fid, trott.id)
    assert tilldelning["tilldelad"] is False
    assert tilldelning["orsak"] == "atl_brott"


def test_utskick_dar_alla_blockeras_andrar_inte_status(session):
    kund = ny_kund(session)
    trott = ny_konsult(session, "Trött Tapper", index=1)
    ny_kvalifikation(session, trott, kund)
    ny_bokning(session, trott, kund, _tid(0, 18), _tid(1, 2))
    session.commit()

    fid = skapa_forfragan(
        session,
        kund_id=kund.id,
        antal_begarda=1,
        starttid=_tid(1, 6),
        sluttid=_tid(1, 14),
    )["forfragan_id"]
    godkann_forfragan(session, fid)

    utfall = registrera_utskick(session, fid, [trott.id], "Pass imorgon!")
    assert utfall["skickade"] == []
    assert utfall["status"] == "godkand"  # inget utskick — ingen övergång
