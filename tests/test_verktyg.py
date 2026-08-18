"""Serviceskiktets verktygsfunktioner: hela flödet från förfrågan till
stängning, felvägar och auditspår."""
from datetime import datetime, timezone

import pytest
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
    HittadesInte,
    OgiltigOvergang,
    forfragan_status,
    godkann_forfragan,
    lista_kvalificerade,
    registrera_svar,
    registrera_utskick,
    skapa_forfragan,
    slapp_plats,
    stang_forfragan,
    tilldela_plats,
)
from tests.hjalp import ny_bokning, ny_konsult, ny_kund, ny_kvalifikation

START = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
SLUT = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)


def _seed(session, antal_konsulter=5):
    kund = ny_kund(session)
    konsulter = []
    for i in range(1, antal_konsulter + 1):
        konsult = ny_konsult(session, f"Konsult {i}", index=i)
        ny_kvalifikation(session, konsult, kund)
        konsulter.append(konsult)
    session.commit()
    return kund, konsulter


def test_hela_flodet(session):
    kund, konsulter = _seed(session)

    skapad = skapa_forfragan(
        session,
        kund_id=kund.id,
        antal_begarda=2,
        starttid=START,
        sluttid=SLUT,
        originaltext="Vi behöver 2 personer idag 16-22",
    )
    fid = skapad["forfragan_id"]
    assert skapad["status"] == "mottagen"
    antal_platser = session.scalar(
        select(func.count()).select_from(Plats).where(Plats.forfragan_id == fid)
    )
    assert antal_platser == 2

    assert godkann_forfragan(session, fid)["status"] == "godkand"

    pool = lista_kvalificerade(session, fid)
    assert len(pool) == 5

    utskick = registrera_utskick(
        session, fid, [k["konsult_id"] for k in pool], "Pass ikväll 16-22, svara JA"
    )
    assert utskick["status"] == "utskickad"
    assert len(utskick["skickade"]) == 5

    # samma konsult igen hoppas över
    igen = registrera_utskick(session, fid, [pool[0]["konsult_id"]], "påminnelse")
    assert igen["hoppade_over"] == [pool[0]["konsult_id"]]
    assert igen["skickade"] == []

    uid = {u["konsult_id"]: u["utskick_id"] for u in utskick["skickade"]}
    ordning = [k["konsult_id"] for k in pool]

    nej = registrera_svar(session, uid[ordning[0]], "NEJ")
    assert nej["svar"] == "nej" and nej["tilldelad"] is False

    okant = registrera_svar(session, uid[ordning[1]], "kanske imorgon?")
    assert okant["svar"] == "okant" and okant["tilldelad"] is False

    ja1 = registrera_svar(session, uid[ordning[2]], "JA!")
    assert ja1["tilldelad"] is True
    assert session.get(Forfragan, fid).status == ForfraganStatus.DELVIS_FYLLD

    ja2 = registrera_svar(session, uid[ordning[3]], "Ja")
    assert ja2["tilldelad"] is True
    assert session.get(Forfragan, fid).status == ForfraganStatus.FYLLD

    ja3 = registrera_svar(session, uid[ordning[4]], "ja")
    assert ja3["tilldelad"] is False
    assert ja3["orsak"] == "fullt_besatt"

    interna = list(
        session.scalars(
            select(Bokning).where(
                Bokning.kalla == BokningKalla.INTERN,
                Bokning.status == BokningStatus.BOKAD,
            )
        )
    )
    assert len(interna) == 2
    assert all(b.starttid == START and b.sluttid == SLUT for b in interna)

    # avhopp: platsen öppnas igen och bokningen avbokas
    avhopp = slapp_plats(session, fid, ja1["konsult_id"], orsak="sjuk")
    assert avhopp["status"] == "delvis_fylld"
    avbokade = session.scalar(
        select(func.count())
        .select_from(Bokning)
        .where(Bokning.status == BokningStatus.AVBOKAD)
    )
    assert avbokade == 1

    # konsulten som blev utan tar den lediga platsen
    ny_tilldelning = tilldela_plats(session, fid, ja3["konsult_id"])
    assert ny_tilldelning["tilldelad"] is True
    assert session.get(Forfragan, fid).status == ForfraganStatus.FYLLD

    assert stang_forfragan(session, fid)["status"] == "stangd"

    rapport = forfragan_status(session, fid)
    assert rapport["status"] == "stangd"
    assert rapport["antal_tilldelade"] == 2
    assert len(rapport["utskick"]) == 5
    assert rapport["kund"] == "Budbee Södertälje"

    handelser = set(session.scalars(select(Auditlogg.handelse).distinct()))
    assert {
        "forfragan_skapad",
        "forfragan_statusbyte",
        "utskick_registrerat",
        "svar_registrerat",
        "plats_tilldelad",
        "plats_slappt",
        "bokning_avbokad",
    } <= handelser


def test_dubbelt_ja_fran_samma_konsult(session):
    kund, konsulter = _seed(session, antal_konsulter=1)
    fid = skapa_forfragan(
        session, kund_id=kund.id, antal_begarda=2, starttid=START, sluttid=SLUT
    )["forfragan_id"]
    godkann_forfragan(session, fid)
    utskick = registrera_utskick(session, fid, [konsulter[0].id], "Jobba?")
    uid = utskick["skickade"][0]["utskick_id"]

    forsta = registrera_svar(session, uid, "JA")
    assert forsta["tilldelad"] is True
    andra = registrera_svar(session, uid, "JA")  # dubblett-SMS
    assert andra["tilldelad"] is False
    assert andra["orsak"] == "redan_tilldelad"


def test_overlappande_bokning_blockerar_tilldelning(session):
    """Kvalificeringen filtrerar upptagna konsulter, men verktyget kan
    anropas direkt — databasens exclusion constraint är sista försvarslinjen."""
    kund, konsulter = _seed(session, antal_konsulter=1)
    ny_bokning(
        session,
        konsulter[0],
        kund,
        datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc),
    )
    session.commit()
    fid = skapa_forfragan(
        session, kund_id=kund.id, antal_begarda=1, starttid=START, sluttid=SLUT
    )["forfragan_id"]
    godkann_forfragan(session, fid)
    registrera_utskick(session, fid, [konsulter[0].id], "Jobba?")

    utfall = tilldela_plats(session, fid, konsulter[0].id)
    assert utfall["tilldelad"] is False
    assert utfall["orsak"] == "overlappande_bokning"
    assert session.get(Forfragan, fid).status == ForfraganStatus.UTSKICKAD


def test_tilldelning_i_fel_status(session):
    kund, konsulter = _seed(session, antal_konsulter=1)
    fid = skapa_forfragan(
        session, kund_id=kund.id, antal_begarda=1, starttid=START, sluttid=SLUT
    )["forfragan_id"]
    utfall = tilldela_plats(session, fid, konsulter[0].id)
    assert utfall["tilldelad"] is False
    assert utfall["orsak"] == "forfragan_i_status_mottagen"


def test_utskick_i_fel_status(session):
    kund, konsulter = _seed(session, antal_konsulter=1)
    fid = skapa_forfragan(
        session, kund_id=kund.id, antal_begarda=1, starttid=START, sluttid=SLUT
    )["forfragan_id"]
    with pytest.raises(OgiltigOvergang):
        registrera_utskick(session, fid, [konsulter[0].id], "för tidigt")


def test_terminala_tillstand(session):
    kund, _ = _seed(session, antal_konsulter=1)
    fid = skapa_forfragan(
        session, kund_id=kund.id, antal_begarda=1, starttid=START, sluttid=SLUT
    )["forfragan_id"]
    stang_forfragan(session, fid)
    with pytest.raises(OgiltigOvergang):
        godkann_forfragan(session, fid)
    with pytest.raises(OgiltigOvergang):
        stang_forfragan(session, fid)


def test_eskalering(session):
    kund, konsulter = _seed(session, antal_konsulter=1)
    fid = skapa_forfragan(
        session, kund_id=kund.id, antal_begarda=1, starttid=START, sluttid=SLUT
    )["forfragan_id"]
    godkann_forfragan(session, fid)
    registrera_utskick(session, fid, [konsulter[0].id], "Jobba?")
    utfall = stang_forfragan(session, fid, eskalera=True, orsak="ingen svarade i tid")
    assert utfall["status"] == "eskalerad"


def test_okanda_id_ger_hittades_inte(session):
    kund, _ = _seed(session, antal_konsulter=1)
    with pytest.raises(HittadesInte):
        skapa_forfragan(
            session, kund_id=99999, antal_begarda=1, starttid=START, sluttid=SLUT
        )
    with pytest.raises(HittadesInte):
        forfragan_status(session, 99999)
    fid = skapa_forfragan(
        session, kund_id=kund.id, antal_begarda=1, starttid=START, sluttid=SLUT
    )["forfragan_id"]
    with pytest.raises(HittadesInte):
        slapp_plats(session, fid, 99999)


def test_ogiltiga_argument(session):
    kund, _ = _seed(session, antal_konsulter=1)
    with pytest.raises(ValueError):
        skapa_forfragan(
            session, kund_id=kund.id, antal_begarda=0, starttid=START, sluttid=SLUT
        )
    with pytest.raises(ValueError):
        skapa_forfragan(
            session, kund_id=kund.id, antal_begarda=1, starttid=SLUT, sluttid=START
        )
