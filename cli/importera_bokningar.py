"""CLI: importera befintliga bokningar (pass) från Excel.

Användning: python -m cli.importera_bokningar fil.xlsx [--aktor namn]
"""
import argparse
import sys

from app.config import PNR_NYCKEL_AR_STANDARD
from app.db import skapa_motor, skapa_sessionfabrik
from app.importers.bokningar import importera_bokningar


def huvud(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Importera befintliga bokningar från Excel (kolumner: Kund, "
        "Starttid, Sluttid samt Telefon eller Personnummer)."
    )
    parser.add_argument("fil", help="Sökväg till .xlsx-fil")
    parser.add_argument("--aktor", default="import", help="Aktör i auditloggen")
    args = parser.parse_args(argv)

    if PNR_NYCKEL_AR_STANDARD:
        print(
            "VARNING: PNR_HASH_NYCKEL är inte satt — utvecklingsnyckeln används "
            "för personnummerhashning. Sätt en riktig hemlighet (Secret Manager) "
            "före produktion.",
            file=sys.stderr,
        )

    motor = skapa_motor()
    with skapa_sessionfabrik(motor)() as session:
        rapport = importera_bokningar(session, args.fil, aktor=args.aktor)
    print(rapport.sammanfattning())
    # Atomär import: radfel → allt återrullat → felkod, så att skript och
    # pipelines inte fortsätter med en passhistorik som ATL inte kan lita på.
    return 0 if rapport.genomford else 1


if __name__ == "__main__":
    sys.exit(huvud())
