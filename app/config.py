"""Central konfiguration. Allt kan överridas via miljövariabler."""
import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/miljonbemanning",
)
DATABASE_URL_TEST = os.environ.get(
    "DATABASE_URL_TEST",
    "postgresql+psycopg://postgres:postgres@localhost:5432/miljonbemanning_test",
)

# Hemlig nyckel för HMAC-hashning av personnummer.
# MÅSTE sättas till en riktig hemlighet via miljövariabel före produktion.
PNR_HASH_NYCKEL = os.environ.get(
    "PNR_HASH_NYCKEL", "utvecklingsnyckel-byt-fore-produktion"
)

TIDSZON = "Europe/Stockholm"
