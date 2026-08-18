# Miljonbemanning — deterministisk kärna (lager 1)

## Bakgrund

Miljonbemanning är ett bemanningsbolag. Kunder skickar akuta passförfrågningar
med några timmars varsel, t.ex. *"vi behöver 3 personer idag med start kl 16"*.
Idag ringer en konsultchef runt manuellt. Målsystemet fyller platserna via
SMS-broadcast till kvalificerade konsulter, **först till kvarn**.

## Arkitektur i två lager

```
┌──────────────────────────────────────────────┐
│  Lager 2: AI-agentlager (SENARE projekt)     │
│  Tolkar inkommande förfrågningar, för dialog │
│  – kan ENDAST agera via lager 1:s verktyg    │
└──────────────────┬───────────────────────────┘
                   │  verktygsfunktioner (exponeras senare som MCP-verktyg)
┌──────────────────▼───────────────────────────┐
│  Lager 1: deterministisk kärna (DETTA repo)  │
│  Tillståndsmaskin, atomär tilldelning,       │
│  ATL-spärr, auditlogg, import                │
└──────────────────┬───────────────────────────┘
                   │  SQLAlchemy + Alembic
┌──────────────────▼───────────────────────────┐
│  PostgreSQL                                  │
│  Sista försvarslinjen: constraints, triggers │
│  och radlåsning gör felaktiga tillstånd      │
│  omöjliga även för buggig kod ovanpå         │
└──────────────────────────────────────────────┘
```

Kärnan är **sista försvarslinjen, inte ett förslag**: allt som är förbjudet ska
vara omöjligt på databasnivå, inte bara ogjort i applikationslogiken. Ett
AI-agentlager som kopplas på senare kan alltså aldrig överboka, hoppa över
auditloggning eller kringgå ATL-spärren, oavsett hur det anropar verktygen.

I detta projekt ingår **inte**: SMS-integration, webbgränssnitt för
slutanvändare, anrop till språkmodeller eller integrationer mot externa system
(Intelliplan, Workbuster, SMS-leverantörer). All indata kommer via import
eller API.

## Hårda regler

- **Ingen rangordning av konsulter.** Ingen poängsättning, ingen sortering på
  lämplighet, inga score-/rank-kolumner i schemat. Den kvalificerade poolen
  returneras alltid i slumpmässig ordning (`ORDER BY random()` i Postgres).
  Detta är ett medvetet juridiskt designval.
- **Personnummer lagras endast som HMAC-SHA256-hash**, aldrig i klartext —
  inte heller i auditloggen.
- **Tilldelning över begärt antal är omöjlig på databasnivå** (platsrader +
  constraint-trigger), inte bara i applikationslogik.
- **Varje tillståndsändring skrivs till auditloggen.**

## Datamodell

| Tabell        | Innehåll |
|---------------|----------|
| `konsult`     | namn, telefon (E.164), personnummer_hash, aktiv, anställningsform |
| `kund`        | namn, ort |
| `kvalifikation` | konsult ↔ kund med introduktionsdatum (genomförd introduktion) |
| `bokning`     | konsult, kund, starttid, sluttid, status, källa (`intern`/`importerad`) |
| `forfragan`   | kund, inkommen, antal_begärda, starttid, sluttid, status, originaltext |
| `plats`       | en rad per begärd plats i en förfrågan — strukturellt tak för tilldelning |
| `utskick`     | förfrågan ↔ konsult, skickad, meddelandetext, svar, svarstid |
| `auditlogg`   | tidpunkt, aktör, händelse, före, efter (JSONB) |

Alla tidpunkter lagras som `timestamptz` (UTC). Indata utan tidszon tolkas som
`Europe/Stockholm`.

`plats`-tabellen är central för samtidighetsskyddet: när en förfrågan skapas
skapas exakt `antal_begärda` platsrader. En tilldelning tar en ledig rad med
`SELECT … FOR UPDATE SKIP LOCKED` — det kan aldrig finnas fler tilldelningar
än rader, och en constraint-trigger hindrar att extra rader smyger in.
Ett partiellt unikt index (`förfrågan_id, konsult_id`) plus idempotent
tilldelning gör att dubbla JA från samma konsult returnerar den befintliga
tilldelningen i stället för att ta en ny plats, och tilldelningen verifierar
i samma transaktion att konsulten saknar överlappande bokning (skydd mot
dubbelbokning mellan parallella förfrågningar, utöver ATL).

Importsemantik: konsultimporten är tolerant per rad (radfel rapporteras,
övriga rader importeras), medan bokningsimporten är **atomär** — vid radfel
rullas allt tillbaka och kommandot avslutas med felkod, eftersom
ATL-kontrollen inte kan litas på mot en ofullständig passhistorik.

## Förfrågans tillståndsmaskin

```
mottagen → godkänd → utskickad → delvis_fylld ⇄ fylld
    └─────────┴──────────┴─────────────┴────→ stängd
    └─────────┴──────────┴─────────────┴────→ eskalerad   (ej från fylld)
```

Ogiltiga övergångar kastar `OgiltigOvergang`. Avhopp från en fylld förfrågan
öppnar platsen igen (`fylld → delvis_fylld`).

## Verktygsfunktioner (serviceskiktet)

`skapa_forfragan`, `godkann_forfragan`, `lista_kvalificerade`,
`registrera_utskick`, `registrera_svar`, `tilldela_plats`, `slapp_plats`,
`stang_forfragan`, `forfragan_status`, `kontrollera_atl` — se
`app/services/`. Dessa är de enda ingångarna för framtida lager.

## ATL-spärren

`kontrollera_atl` blockerar erbjudanden som skulle bryta mot dygnsvila
(≥ 11 h per 24-timmarsperiod), veckovila (≥ 36 h per sjudagarsperiod) eller
taket för total arbetstid per rullande sjudagarsperiod (48 h). Kontrollen
räknar mot **alla** bokningar, även importerade befintliga pass. Gränserna
ligger i `app/atl_config.py` och ska stämmas av mot Bemanningsavtalet innan
produktion.
