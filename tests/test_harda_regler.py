"""Vakttester för de hårda reglerna — går sönder om någon smyger in
rangordning eller klartextpersonnummer i schemat."""
from app.models import Bas

FORBJUDNA_KOLUMNNAMN = ("score", "rank", "poang", "poäng", "betyg", "prio", "lamplighet", "lämplighet")


def test_inga_rangordningskolumner_i_schemat():
    for tabell in Bas.metadata.tables.values():
        for kolumn in tabell.columns:
            for forbjudet in FORBJUDNA_KOLUMNNAMN:
                assert forbjudet not in kolumn.name.lower(), (
                    f"Kolumnen {tabell.name}.{kolumn.name} bryter mot den hårda "
                    f"regeln om att aldrig rangordna konsulter."
                )


def test_personnummer_endast_som_hash():
    for tabell in Bas.metadata.tables.values():
        for kolumn in tabell.columns:
            if "personnummer" in kolumn.name.lower():
                assert kolumn.name.lower().endswith("_hash"), (
                    f"Kolumnen {tabell.name}.{kolumn.name} får inte lagra "
                    f"personnummer i klartext."
                )
