"""Sista försvarslinjen: överbokning ska vara omöjlig PÅ DATABASNIVÅ.
Testerna går förbi serviceskiktet med rå SQL och verifierar att databasen
själv säger nej."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.services import (
    godkann_forfragan,
    registrera_utskick,
    skapa_forfragan,
    tilldela_plats,
)
from tests.hjalp import ny_konsult, ny_kund, ny_kvalifikation

START = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
SLUT = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)


def _forfragan(session, antal: int) -> int:
    kund = ny_kund(session)
    session.commit()
    return skapa_forfragan(
        session, kund_id=kund.id, antal_begarda=antal, starttid=START, sluttid=SLUT
    )["forfragan_id"]


def test_extra_platsrad_stoppas_av_trigger(session):
    fid = _forfragan(session, antal=2)
    with pytest.raises(DBAPIError, match="överbokning stoppad"):
        session.execute(
            text("INSERT INTO plats (forfragan_id) VALUES (:fid)"), {"fid": fid}
        )
    session.rollback()


def test_antal_begarda_kan_inte_sankas_under_platsraderna(session):
    fid = _forfragan(session, antal=3)
    with pytest.raises(DBAPIError, match="kan inte sänkas"):
        session.execute(
            text("UPDATE forfragan SET antal_begarda = 1 WHERE id = :fid"),
            {"fid": fid},
        )
    session.rollback()


def test_dubbel_tilldelning_av_samma_konsult_stoppas_av_index(session):
    kund = ny_kund(session)
    konsult = ny_konsult(session, index=1)
    ny_kvalifikation(session, konsult, kund)
    session.commit()
    fid = skapa_forfragan(
        session, kund_id=kund.id, antal_begarda=2, starttid=START, sluttid=SLUT
    )["forfragan_id"]
    godkann_forfragan(session, fid)
    registrera_utskick(session, fid, [konsult.id], "Jobba?")
    assert tilldela_plats(session, fid, konsult.id)["tilldelad"] is True

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE plats SET konsult_id = :kid "
                "WHERE forfragan_id = :fid AND konsult_id IS NULL"
            ),
            {"kid": konsult.id, "fid": fid},
        )
    session.rollback()


def test_overlappande_bokning_stoppas_av_exclusion_constraint(session):
    kund = ny_kund(session)
    konsult = ny_konsult(session, index=1)
    session.flush()
    session.execute(
        text(
            "INSERT INTO bokning (konsult_id, kund_id, starttid, sluttid, status, kalla) "
            "VALUES (:kid, :kuid, :s1, :s2, 'bokad', 'importerad')"
        ),
        {"kid": konsult.id, "kuid": kund.id, "s1": START, "s2": SLUT},
    )
    with pytest.raises(IntegrityError, match="ex_bokning_overlapp"):
        session.execute(
            text(
                "INSERT INTO bokning (konsult_id, kund_id, starttid, sluttid, status, kalla) "
                "VALUES (:kid, :kuid, :s1, :s2, 'bokad', 'intern')"
            ),
            {
                "kid": konsult.id,
                "kuid": kund.id,
                "s1": datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc),
                "s2": datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc),
            },
        )
    session.rollback()
