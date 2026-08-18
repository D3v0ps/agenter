"""Tillståndsmaskin för Förfrågan.

    mottagen → godkänd → utskickad → delvis_fylld ⇄ fylld
        └────────┴───────────┴────────────┴──────→ stängd
        └────────┴───────────┴────────────┴──────→ eskalerad (ej från fylld)

byt_status är den ENDA tillåtna vägen att ändra en förfrågans status.
Varje övergång valideras mot tabellen nedan och skrivs till auditloggen.
Ogiltiga övergångar (inklusive självövergångar) kastar OgiltigOvergang."""
from sqlalchemy.orm import Session

from app.audit import skriv_audit
from app.models import Forfragan, ForfraganStatus

_S = ForfraganStatus

TILLATNA_OVERGANGAR: dict[ForfraganStatus, frozenset[ForfraganStatus]] = {
    _S.MOTTAGEN: frozenset({_S.GODKAND, _S.STANGD, _S.ESKALERAD}),
    _S.GODKAND: frozenset({_S.UTSKICKAD, _S.STANGD, _S.ESKALERAD}),
    _S.UTSKICKAD: frozenset({_S.DELVIS_FYLLD, _S.FYLLD, _S.STANGD, _S.ESKALERAD}),
    _S.DELVIS_FYLLD: frozenset({_S.FYLLD, _S.STANGD, _S.ESKALERAD}),
    _S.FYLLD: frozenset({_S.DELVIS_FYLLD, _S.STANGD}),
    _S.STANGD: frozenset(),
    _S.ESKALERAD: frozenset(),
}


class OgiltigOvergang(Exception):
    def __init__(self, fran: ForfraganStatus, till: ForfraganStatus):
        self.fran = fran
        self.till = till
        super().__init__(f"ogiltig statusövergång: {fran.value} → {till.value}")


def byt_status(
    session: Session,
    forfragan: Forfragan,
    ny: ForfraganStatus,
    aktor: str,
    extra: dict | None = None,
) -> None:
    """Validera och utför en statusövergång. Skrivs alltid till auditloggen."""
    if ny not in TILLATNA_OVERGANGAR[forfragan.status]:
        raise OgiltigOvergang(forfragan.status, ny)
    fore = forfragan.status
    forfragan.status = ny
    efter: dict = {"forfragan_id": forfragan.id, "status": ny.value}
    if extra:
        efter.update(extra)
    skriv_audit(
        session,
        aktor,
        "forfragan_statusbyte",
        fore={"forfragan_id": forfragan.id, "status": fore.value},
        efter=efter,
    )
