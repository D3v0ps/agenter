"""Tester för kvalificeringsfrågan: aktiv anställning, genomförd introduktion,
ingen överlappande bokning — och ALLTID slumpmässig ordning."""
from datetime import date, datetime, timezone

from app.models import BokningStatus
from app.repositories.kvalificering import kvalificerade_konsulter
from tests.hjalp import ny_bokning, ny_konsult, ny_kund, ny_kvalifikation

START = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)  # 16:00 svensk sommartid
SLUT = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)   # 22:00


def test_kvalificering_filtrerar_ratt(session):
    kund = ny_kund(session)

    ok = ny_konsult(session, "Kvalificerad", index=1)
    inaktiv = ny_konsult(session, "Inaktiv", index=2, aktiv=False)
    utan_kvalifikation = ny_konsult(session, "Utan kvalifikation", index=3)
    utan_intro = ny_konsult(session, "Utan introduktion", index=4)
    sen_intro = ny_konsult(session, "Sen introduktion", index=5)
    upptagen = ny_konsult(session, "Upptagen", index=6)
    kant_i_kant = ny_konsult(session, "Kant i kant", index=7)
    avbokad = ny_konsult(session, "Avbokat pass", index=8)

    for konsult in (ok, inaktiv, utan_intro, sen_intro, upptagen, kant_i_kant, avbokad):
        intro: date | None = date(2025, 1, 1)
        if konsult is utan_intro:
            intro = None
        if konsult is sen_intro:
            intro = date(2026, 12, 1)  # efter passet
        ny_kvalifikation(session, konsult, kund, intro=intro)

    # Överlappande importerad bokning utesluter.
    ny_bokning(session, upptagen, kund, datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc), datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc))
    # Bokning som slutar exakt när passet börjar utesluter INTE.
    ny_bokning(session, kant_i_kant, kund, datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc), START)
    # Överlappande men AVBOKAD bokning utesluter INTE.
    ny_bokning(session, avbokad, kund, START, SLUT, status=BokningStatus.AVBOKAD)
    session.commit()

    resultat = kvalificerade_konsulter(session, kund.id, START, SLUT)
    assert {k.id for k in resultat} == {ok.id, kant_i_kant.id, avbokad.id}


def test_kvalificering_annan_kunds_intro_raknas_inte(session):
    kund = ny_kund(session)
    annan_kund = ny_kund(session, namn="Annan AB", ort="Solna")
    konsult = ny_konsult(session, index=1)
    ny_kvalifikation(session, konsult, annan_kund)
    session.commit()
    assert kvalificerade_konsulter(session, kund.id, START, SLUT) == []


def test_kvalificering_slumpmassig_ordning(session):
    """HÅRD REGEL: ordningen ska vara slumpmässig — samma pool, olika ordning."""
    kund = ny_kund(session)
    for i in range(1, 9):
        konsult = ny_konsult(session, f"Konsult {i}", index=i)
        ny_kvalifikation(session, konsult, kund)
    session.commit()

    ordningar = set()
    mangder = set()
    for _ in range(6):
        resultat = kvalificerade_konsulter(session, kund.id, START, SLUT)
        ordningar.add(tuple(k.id for k in resultat))
        mangder.add(frozenset(k.id for k in resultat))
    assert len(mangder) == 1        # alltid samma pool ...
    assert len(ordningar) >= 2      # ... men inte samma ordning
