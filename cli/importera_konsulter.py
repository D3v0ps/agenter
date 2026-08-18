"""CLI: importera konsulter från Excel.

Användning: python -m cli.importera_konsulter fil.xlsx [--aktor namn]
"""
import argparse
import sys

from app.config import PNR_NYCKEL_AR_STANDARD
from app.db import skapa_motor, skapa_sessionfabrik
from app.importers.konsulter import importera_konsulter


def huvud(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Importera konsulter från Excel (kolumner: Namn, Telefon, "
        "Personnummer; valfritt: Anställningsform, Aktiv)."
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
        rapport = importera_konsulter(session, args.fil, aktor=args.aktor)
    print(rapport.sammanfattning())
    return 0 if rapport.genomford else 1


if __name__ == "__main__":
    sys.exit(huvud())
