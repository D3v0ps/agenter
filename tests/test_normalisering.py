"""Enhetstester för normalisering av telefon, personnummer och tider.
Kräver ingen databas."""
from datetime import date, datetime, timezone

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


@pytest.mark.parametrize("ratt", ["abc", "12", None, "", "070-123"])
def test_normalisera_telefon_ogiltigt(ratt):
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


def test_normalisera_personnummer_ogiltigt():
    pnr = giltigt_personnummer(1)
    fel_kontrollsiffra = pnr[:11] + str((int(pnr[11]) + 1) % 10)
    with pytest.raises(Normaliseringsfel):
        normalisera_personnummer(fel_kontrollsiffra)
    with pytest.raises(Normaliseringsfel):
        normalisera_personnummer("123")
    with pytest.raises(Normaliseringsfel):
        normalisera_personnummer("19901340" + "0017")  # ogiltig månad


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
