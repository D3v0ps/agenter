"""Auditloggning. Varje tillståndsändring i systemet skrivs hit (hård regel).

Före-/efter-bilder lagras som JSONB. Personnummer (även hashade) hör inte
hemma i loggen — skicka aldrig in dem."""
import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import Auditlogg


def _json_sakra(varde: dict[str, Any] | None) -> dict[str, Any] | None:
    if varde is None:
        return None
    return json.loads(json.dumps(varde, default=str))


def skriv_audit(
    session: Session,
    aktor: str,
    handelse: str,
    fore: dict[str, Any] | None = None,
    efter: dict[str, Any] | None = None,
) -> None:
    session.add(
        Auditlogg(
            aktor=aktor,
            handelse=handelse,
            fore=_json_sakra(fore),
            efter=_json_sakra(efter),
        )
    )
