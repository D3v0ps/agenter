"""Gemensamma delar för serviceskiktet."""
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Forfragan


class HittadesInte(Exception):
    """Ett angivet id pekar inte på någon existerande rad."""


@contextmanager
def i_transaktion(session: Session):
    """Kör blocket och committa; rulla tillbaka vid fel och kasta vidare.
    Varje verktygsfunktion är en egen atomär transaktion."""
    try:
        yield
        session.commit()
    except BaseException:
        session.rollback()
        raise


def hamta_forfragan(
    session: Session, forfragan_id: int, *, las: bool = False
) -> Forfragan:
    """Hämta en förfrågan. Med las=True tas radlås (FOR UPDATE) och raden
    läses om från databasen — MÅSTE användas av varje funktion som ändrar
    förfrågans status, annars kan en parallellt committad övergång (t.ex.
    stängd) skrivas över med ett inaktuellt värde."""
    if las:
        forfragan = session.execute(
            select(Forfragan)
            .where(Forfragan.id == forfragan_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
    else:
        forfragan = session.get(Forfragan, forfragan_id)
    if forfragan is None:
        raise HittadesInte(f"förfrågan {forfragan_id} finns inte")
    return forfragan
