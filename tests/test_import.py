"""Tester för de toleranta Excel-importerna. Körs mot riktig Postgres."""
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.importers.bokningar import importera_bokningar
from app.importers.konsulter import importera_konsulter
from app.models import Auditlogg, Bokning, BokningKalla, Konsult, Kund
from tests.hjalp import giltigt_personnummer, skriv_xlsx

KONSULTKOLUMNER = ["Namn", "Telefon", "Personnummer", "Anställningsform", "Aktiv"]


def test_import_konsulter(session, tmp_path):
    fil = skriv_xlsx(
        tmp_path / "konsulter.xlsx",
        KONSULTKOLUMNER,
        [
            ["Anna Andersson", "070-111 22 33", giltigt_personnummer(1), "Timanställd", "ja"],
            ["Bo Berg", "+46 70 222 33 44", giltigt_personnummer(2), "Ambulerande", "nej"],
        ],
    )
    rapport = importera_konsulter(session, fil)
    assert rapport.genomford
    assert rapport.skapade == 2
    assert not rapport.radfel

    anna = session.scalar(select(Konsult).where(Konsult.namn == "Anna Andersson"))
    assert anna.telefon == "+46701112233"
    assert anna.aktiv is True
    bo = session.scalar(select(Konsult).where(Konsult.namn == "Bo Berg"))
    assert bo.aktiv is False

    # HÅRD REGEL: personnummer aldrig i klartext — bara 64 tecken hex-hash.
    for konsult in session.scalars(select(Konsult)):
        assert len(konsult.personnummer_hash) == 64
        assert giltigt_personnummer(1) not in konsult.personnummer_hash

    antal_audit = session.scalar(
        select(func.count()).select_from(Auditlogg).where(Auditlogg.handelse == "konsult_importerad")
    )
    assert antal_audit == 2


def test_import_konsulter_saknad_kolumn_rapporteras(session, tmp_path):
    fil = skriv_xlsx(
        tmp_path / "utan_telefon.xlsx",
        ["Namn", "Personnummer"],
        [["Anna Andersson", giltigt_personnummer(1)]],
    )
    rapport = importera_konsulter(session, fil)
    assert rapport.saknade_kolumner == ["telefon"]
    assert not rapport.genomford
    assert session.scalar(select(func.count()).select_from(Konsult)) == 0


def test_import_konsulter_radfel_stoppar_inte_ovriga(session, tmp_path):
    fil = skriv_xlsx(
        tmp_path / "delvis.xlsx",
        KONSULTKOLUMNER,
        [
            ["Anna Andersson", "070-111 22 33", giltigt_personnummer(1), "", ""],
            ["Fel Person", "banan", giltigt_personnummer(2), "", ""],
            ["Cesar Croneld", "070-333 44 55", "199001010000", "", ""],  # fel kontrollsiffra
        ],
    )
    rapport = importera_konsulter(session, fil)
    assert rapport.skapade == 1
    assert [fel.rad for fel in rapport.radfel] == [3, 4]
    assert session.scalar(select(func.count()).select_from(Konsult)) == 1


def test_import_konsulter_ateimport_uppdaterar(session, tmp_path):
    fil = skriv_xlsx(
        tmp_path / "k.xlsx",
        KONSULTKOLUMNER,
        [["Anna Andersson", "070-111 22 33", giltigt_personnummer(1), "Timanställd", "ja"]],
    )
    forsta = importera_konsulter(session, fil)
    assert forsta.skapade == 1

    fil2 = skriv_xlsx(
        tmp_path / "k2.xlsx",
        KONSULTKOLUMNER,
        [["Anna Ny-Andersson", "070-999 88 77", giltigt_personnummer(1), "Timanställd", "ja"]],
    )
    andra = importera_konsulter(session, fil2)
    assert andra.skapade == 0
    assert andra.uppdaterade == 1
    assert session.scalar(select(func.count()).select_from(Konsult)) == 1
    anna = session.scalar(select(Konsult))
    assert anna.namn == "Anna Ny-Andersson"
    assert anna.telefon == "+46709998877"


def _seed_konsult(session, tmp_path):
    fil = skriv_xlsx(
        tmp_path / "seed.xlsx",
        KONSULTKOLUMNER,
        [["Anna Andersson", "070-111 22 33", giltigt_personnummer(1), "", ""]],
    )
    importera_konsulter(session, fil)


BOKNINGSKOLUMNER = ["Telefon", "Kund", "Starttid", "Sluttid"]


def test_import_bokningar(session, tmp_path):
    _seed_konsult(session, tmp_path)
    fil = skriv_xlsx(
        tmp_path / "bokningar.xlsx",
        BOKNINGSKOLUMNER,
        [
            ["0701112233", "Budbee Södertälje", datetime(2026, 1, 15, 8, 0), datetime(2026, 1, 15, 16, 0)],
        ],
    )
    rapport = importera_bokningar(session, fil)
    assert rapport.genomford
    assert rapport.skapade == 1
    assert rapport.skapade_kunder == ["Budbee Södertälje"]
    assert rapport.radfel == []

    bokning = session.scalar(select(Bokning))
    # naiv svensk vintertid 08:00 → 07:00 UTC
    assert bokning.starttid == datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)
    assert bokning.kalla == BokningKalla.IMPORTERAD
    assert session.scalar(select(Kund).where(Kund.namn == "Budbee Södertälje")) is not None


def test_import_bokningar_radfel_rullar_tillbaka_allt(session, tmp_path):
    """Bokningsimporten är atomär: ett radfel (okänd konsult) rullar tillbaka
    ALLT — även giltiga rader och auto-skapade kunder — och rapporten varnar
    om att ATL-kontrollen inte kan litas på förrän importen är hel."""
    _seed_konsult(session, tmp_path)
    fil = skriv_xlsx(
        tmp_path / "bokningar_fel.xlsx",
        BOKNINGSKOLUMNER,
        [
            ["0701112233", "Budbee Södertälje", datetime(2026, 1, 15, 8, 0), datetime(2026, 1, 15, 16, 0)],
            ["0709999999", "Budbee Södertälje", datetime(2026, 1, 16, 8, 0), datetime(2026, 1, 16, 16, 0)],
        ],
    )
    rapport = importera_bokningar(session, fil)
    assert rapport.aterrullad is True
    assert not rapport.genomford
    assert rapport.skapade == 0
    assert len(rapport.radfel) == 1
    assert "okänd konsult" in rapport.radfel[0].fel
    assert "kan inte litas på" in rapport.sammanfattning()

    assert session.scalar(select(func.count()).select_from(Bokning)) == 0
    # även den auto-skapade kunden rullades tillbaka
    assert session.scalar(select(Kund).where(Kund.namn == "Budbee Södertälje")) is None


def test_import_bokningar_saknade_kolumner(session, tmp_path):
    fil = skriv_xlsx(tmp_path / "b.xlsx", ["Kund", "Starttid"], [["X", datetime(2026, 1, 1)]])
    rapport = importera_bokningar(session, fil)
    assert "sluttid" in rapport.saknade_kolumner
    assert "telefon eller personnummer" in rapport.saknade_kolumner
    assert not rapport.genomford


def test_import_bokningar_overlapp_ger_radfel_och_aterrullning(session, tmp_path):
    _seed_konsult(session, tmp_path)
    fil = skriv_xlsx(
        tmp_path / "overlapp.xlsx",
        BOKNINGSKOLUMNER,
        [
            ["0701112233", "Budbee Södertälje", datetime(2026, 1, 15, 8, 0), datetime(2026, 1, 15, 16, 0)],
            ["0701112233", "Budbee Södertälje", datetime(2026, 1, 15, 12, 0), datetime(2026, 1, 15, 20, 0)],
        ],
    )
    rapport = importera_bokningar(session, fil)
    assert rapport.aterrullad is True
    assert len(rapport.radfel) == 1
    assert "överlappar" in rapport.radfel[0].fel
    assert session.scalar(select(func.count()).select_from(Bokning)) == 0


def test_import_bokningar_dubblett_hoppas_over(session, tmp_path):
    _seed_konsult(session, tmp_path)
    rader = [["0701112233", "Budbee Södertälje", datetime(2026, 1, 15, 8, 0), datetime(2026, 1, 15, 16, 0)]]
    fil = skriv_xlsx(tmp_path / "b1.xlsx", BOKNINGSKOLUMNER, rader)
    assert importera_bokningar(session, fil).skapade == 1
    fil2 = skriv_xlsx(tmp_path / "b2.xlsx", BOKNINGSKOLUMNER, rader)
    rapport = importera_bokningar(session, fil2)
    assert rapport.skapade == 0
    assert rapport.hoppade_over == 1
