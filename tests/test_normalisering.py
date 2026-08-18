"""Enhetstester för normalisering av telefon, personnummer och tider.
Kräver ingen databas."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.importers.gemensamt import (
    Normaliseringsfel,
    hash_personnummer,
    normalisera_personnummer,
    normalisera_telefon,
    tolka_tid,
)
from tests.hjalp import giltigt_personnummer


@pytest.mark.parametrize(
    "ratt, forvantat",
    [
        ("070-123 45 67", "+46701234567"),
        ("0046 70 123 45 67", "+46701234567"),
        ("+46 70 123 45 67", "+46701234567"),
        ("+46(0)70-123.45.67", "+46701234567"),
        (701234567.0, "+46701234567"),  # numerisk Excel-cell utan inledande nolla
        (46701234567, "+46701234567"),
        ("08-123 456", "+468123456"),
    ],
)
def test_normalisera_telefon(ratt, forvantat):
    assert normalisera_telefon(ratt) == forvantat


@pytest.mark.parametrize(
    "ratt",
    ["abc", "12", None, "", "070-123", "070-123 45 6", "+46 70 123 45 6"],
)
def test_normalisera_telefon_ogiltigt(ratt):
    # de två sista: trunkerade mobilnummer (mobil kräver 9 siffror efter +46)
    with pytest.raises(Normaliseringsfel):
        normalisera_telefon(ratt)


def test_normalisera_personnummer_12_siffror():
    pnr = giltigt_personnummer(1)
    assert normalisera_personnummer(pnr) == pnr
    assert normalisera_personnummer(pnr[:8] + "-" + pnr[8:]) == pnr


def test_normalisera_personnummer_10_siffror_sekel():
    pnr = giltigt_personnummer(1, fodd=date(1990, 1, 1))
    assert normalisera_personnummer(pnr[2:]) == pnr  # 90xxxx → 1990
    ung = giltigt_personnummer(2, fodd=date(2006, 5, 1))
    assert normalisera_personnummer(ung[2:]) == ung  # 06xxxx → 2006


def _sexton_ar_sedan() -> date:
    idag = date.today()
    try:
        return idag.replace(year=idag.year - 16)
    except ValueError:  # 29 februari
        return idag.replace(year=idag.year - 16, day=28)


def test_sekelregeln_ar_dagexakt():
    # fyllde 16 exakt i dag → 20xx
    nyss_16 = giltigt_personnummer(3, fodd=_sexton_ar_sedan())
    assert normalisera_personnummer(nyss_16[2:]) == nyss_16
    assert nyss_16.startswith("20")
    # fyller 16 i morgon → kan inte vara en 20xx-konsult → 19xx
    fyller_imorgon = _sexton_ar_sedan() + timedelta(days=1)
    pnr = giltigt_personnummer(4, fodd=fyller_imorgon)
    assert normalisera_personnummer(pnr[2:]) == "19" + pnr[2:]


def test_normalisera_personnummer_ogiltigt():
    pnr = giltigt_personnummer(1)
    fel_kontrollsiffra = pnr[:11] + str((int(pnr[11]) + 1) % 10)
    with pytest.raises(Normaliseringsfel):
        normalisera_personnummer(fel_kontrollsiffra)
    with pytest.raises(Normaliseringsfel):
        normalisera_personnummer("123")
    with pytest.raises(Normaliseringsfel):
        normalisera_personnummer("19901340" + "0017")  # ogiltig månad


def test_personnummer_aldrig_i_felmeddelanden():
    """HÅRD REGEL: radfelstexter skrivs till konsol/loggar och får aldrig
    innehålla det inmatade personnumret."""
    pnr = giltigt_personnummer(1)
    fel_kontrollsiffra = pnr[:11] + str((int(pnr[11]) + 1) % 10)
    for ogiltigt in (fel_kontrollsiffra, "19901340" + "0017", "1234567890123"):
        with pytest.raises(Normaliseringsfel) as fel:
            normalisera_personnummer(ogiltigt)
        assert ogiltigt not in str(fel.value)
        assert ogiltigt[2:8] not in str(fel.value)  # inte heller delsträngar


def test_hash_personnummer_deterministisk_med_nyckel():
    pnr = giltigt_personnummer(1)
    assert hash_personnummer(pnr) == hash_personnummer(pnr)
    assert hash_personnummer(pnr, "nyckel-a") != hash_personnummer(pnr, "nyckel-b")
    assert len(hash_personnummer(pnr)) == 64
    assert pnr not in hash_personnummer(pnr)


def test_tolka_tid_vintertid():
    utc = tolka_tid(datetime(2026, 1, 15, 8, 0))
    assert utc == datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)


def test_tolka_tid_sommartid():
    utc = tolka_tid("2026-07-15 08:00")
    assert utc == datetime(2026, 7, 15, 6, 0, tzinfo=timezone.utc)


def test_tolka_tid_ogiltig():
    with pytest.raises(Normaliseringsfel):
        tolka_tid("igår kväll")


def test_tolka_tid_obefintligt_klockslag_ger_fel():
    # 2026-03-29 02:30 finns inte — klockan hoppar 02:00 → 03:00
    with pytest.raises(Normaliseringsfel, match="finns inte"):
        tolka_tid(datetime(2026, 3, 29, 2, 30))


def test_tolka_tid_tvetydigt_klockslag_ger_fel():
    # 2026-10-25 02:30 inträffar två gånger — klockan ställs tillbaka
    with pytest.raises(Normaliseringsfel, match="tvetydig"):
        tolka_tid("2026-10-25 02:30")
