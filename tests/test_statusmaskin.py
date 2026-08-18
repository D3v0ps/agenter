"""Tillståndsmaskinen: alla giltiga övergångar fungerar och auditloggas,
alla ogiltiga (inklusive självövergångar) kastar OgiltigOvergang."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import Auditlogg, Forfragan, ForfraganStatus
from app.statusmaskin import TILLATNA_OVERGANGAR, OgiltigOvergang, byt_status
from tests.hjalp import ny_kund

START = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
SLUT = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)


def _forfragan(session, kund, status: ForfraganStatus) -> Forfragan:
    forfragan = Forfragan(
        kund_id=kund.id,
        antal_begarda=1,
        starttid=START,
        sluttid=SLUT,
        status=status,
    )
    session.add(forfragan)
    session.flush()
    return forfragan


def test_giltig_kedja_auditloggas(session):
    kund = ny_kund(session)
    forfragan = _forfragan(session, kund, ForfraganStatus.MOTTAGEN)
    kedja = [
        ForfraganStatus.GODKAND,
        ForfraganStatus.UTSKICKAD,
        ForfraganStatus.DELVIS_FYLLD,
        ForfraganStatus.FYLLD,
        ForfraganStatus.DELVIS_FYLLD,  # avhopp öppnar platsen igen
        ForfraganStatus.FYLLD,
        ForfraganStatus.STANGD,
    ]
    for ny in kedja:
        byt_status(session, forfragan, ny, "test")
    session.commit()
    assert forfragan.status == ForfraganStatus.STANGD
    antal = session.scalar(
        select(func.count())
        .select_from(Auditlogg)
        .where(Auditlogg.handelse == "forfragan_statusbyte")
    )
    assert antal == len(kedja)


def test_alla_ogiltiga_overgangar_kastar(session):
    kund = ny_kund(session)
    provade = 0
    for fran in ForfraganStatus:
        for till in ForfraganStatus:
            if till in TILLATNA_OVERGANGAR[fran]:
                continue
            forfragan = _forfragan(session, kund, fran)
            with pytest.raises(OgiltigOvergang):
                byt_status(session, forfragan, till, "test")
            assert forfragan.status == fran  # oförändrad efter fel
            provade += 1
    assert provade > 20  # de terminala tillstånden + alla självövergångar m.m.


def test_terminala_tillstand_saknar_utgangar():
    assert TILLATNA_OVERGANGAR[ForfraganStatus.STANGD] == frozenset()
    assert TILLATNA_OVERGANGAR[ForfraganStatus.ESKALERAD] == frozenset()
    # eskalering är inte möjlig från fylld — en fylld förfrågan är inte akut
    assert ForfraganStatus.ESKALERAD not in TILLATNA_OVERGANGAR[ForfraganStatus.FYLLD]
