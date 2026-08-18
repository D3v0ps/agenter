"""Gemensamma delar för serviceskiktet."""
from contextlib import contextmanager

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


def hamta_forfragan(session: Session, forfragan_id: int) -> Forfragan:
    forfragan = session.get(Forfragan, forfragan_id)
    if forfragan is None:
        raise HittadesInte(f"förfrågan {forfragan_id} finns inte")
    return forfragan
