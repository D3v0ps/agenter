# Miljonbemanning — deterministisk kärna (lager 1)

Kärnan i Miljonbemannings akutbemanningssystem: kunder skickar akuta
passförfrågningar ("vi behöver 3 personer idag med start kl 16"), systemet
fyller platserna via SMS-broadcast till kvalificerade konsulter — **först
till kvarn**. Det här repot är den deterministiska kärnan; ett AI-agentlager
kopplas på senare och kan bara agera genom kärnans verktygsfunktioner.

Läs mer: [docs/PROJEKT.md](docs/PROJEKT.md) (arkitektur och hårda regler)
och [docs/BESLUT.md](docs/BESLUT.md) (beslutslogg).

## Kom igång på tio minuter

Förutsättningar: **Python 3.12**, **Docker** (för Postgres).

```bash
# 1. Starta databasen (skapar även testdatabasen miljonbemanning_test)
docker compose up -d

# 2. Skapa virtuell miljö och installera beroenden
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Kör migrationerna
alembic upgrade head

# 4. Kör testerna (kräver testdatabasen — samtidighetstesterna körs mot
#    riktig Postgres, aldrig SQLite)
pytest

# 5. Kör demon: hela Budbee-scenariot steg för steg med läsbar utskrift.
#    VARNING: rensar och seedar om databasen miljonbemanning.
python -m cli.demo
```

Standardvärdena för databas-URL:er och hash-nyckel fungerar direkt mot
docker-compose-databasen. Vill du ändra något: se `.env.exempel` och
exportera motsvarande miljövariabler. **PNR_HASH_NYCKEL måste bytas och
läggas i en secret manager före produktion** — en förlorad nyckel bryter
dublettkontrollen för personnummer permanent.

## Importera data från Excel

```bash
# Konsulter — kolumner: Namn, Telefon, Personnummer
# (valfritt: Anställningsform, Aktiv). Telefon normaliseras till +46 E.164,
# personnummer Luhn-valideras och lagras endast som HMAC-hash.
# Tolerant: saknade kolumner rapporteras, radfel listas per rad medan
# övriga rader importeras.
python -m cli.importera_konsulter konsulter.xlsx

# Befintliga bokningar — kolumner: Kund, Starttid, Sluttid samt Telefon
# eller Personnummer. Naiva tider tolkas som Europe/Stockholm.
# ATOMÄR: vid radfel rullas allt tillbaka och kommandot avslutas med
# felkod — ATL-kontrollen kan inte litas på mot en ofullständig
# passhistorik.
python -m cli.importera_bokningar bokningar.xlsx
```

## Projektstruktur

```
app/
  models/          SQLAlchemy-modeller (schema + DB-nivåskydd)
  repositories/    Databasfrågor (bl.a. kvalificering med slumpad ordning)
  services/        Verktygsfunktionerna — enda ingången för framtida lager:
                   skapa_forfragan, godkann_forfragan, lista_kvalificerade,
                   registrera_utskick, registrera_svar, tilldela_plats,
                   slapp_plats, stang_forfragan, forfragan_status,
                   kontrollera_atl
  importers/       Toleranta Excel-importer
  statusmaskin.py  Förfrågans tillståndsmaskin (enda vägen att byta status)
  atl_config.py    ATL-gränser — SKA stämmas av mot Bemanningsavtalet
                   före produktion
  main.py          Minimal FastAPI-app (webhooks tillkommer senare)
alembic/           Migrationer (inkl. exclusion constraint och
                   constraint-triggers som gör överbokning omöjlig
                   på databasnivå)
cli/               importera_konsulter, importera_bokningar, demo
tests/             Körs mot riktig Postgres; race- och DB-skyddstester
docs/              PROJEKT.md (arkitektur), BESLUT.md (beslutslogg)
```

## Hårda regler (får aldrig kringgås)

- **Ingen rangordning av konsulter** — kvalificerad pool returneras alltid i
  slumpmässig ordning; inga score-/rank-kolumner finns i schemat.
- **Personnummer endast som hash** (HMAC-SHA256), aldrig i klartext.
- **Tilldelning över begärt antal är omöjlig på databasnivå** (platsrader +
  radlås + constraint-trigger), dubbelbokning stoppas av en exclusion
  constraint.
- **Inga externa integrationer, inga LLM-anrop** i kärnan.
- **Allt auditloggas** — även nekade tilldelningsförsök.

## Vanliga kommandon

```bash
pytest                            # hela testsviten
pytest tests/test_race.py -v      # bara race-testerna
alembic check                     # modeller och migrationer i synk?
uvicorn app.main:app --reload     # starta API-skalet (hälsokontroll: /halsa)
```
