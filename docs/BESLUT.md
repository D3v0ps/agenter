# Beslutslogg

Löpande logg över vägval. Nyaste beslut läggs längst ned. Varje post anger
vad som valdes, varför, och vad som övervägdes men valdes bort.

## 2026-08-18 — Etapp 1

**B1. Excel-läsning med openpyxl.**
Excel-import kräver ett bibliotek; openpyxl är det enda tillägget utöver den
låsta stacken. *Bortvalt:* pandas (stort beroende för att läsa två enkla ark).

**B2. CLI med argparse (standardbiblioteket).**
Inga extra ramverk. *Bortvalt:* Typer/Click — trevligare API men onödigt
beroende för tre kommandon.

**B3. Personnummer hashas med HMAC-SHA256 med hemlig nyckel.**
Ren osaltad SHA-256 är knäckbar genom uttömmande generering av alla giltiga
personnummer (~10⁷ kandidater per födelsedecennium). HMAC med nyckel utanför
databasen gör uppslagning omöjlig utan nyckeln, men är deterministisk så att
dubblettkontroll vid import fungerar. Nyckeln läses från miljövariabeln
`PNR_HASH_NYCKEL`. *Bortvalt:* slumpsaltad hash per rad (omöjliggör
dubblettkontroll), klartext (förbjudet av hård regel).

**B4. Personnummer valideras med Luhn vid import; 10 siffror normaliseras
till 12.** Sekelregel: 20xx om personen då är under 16 år är omöjligt → annars
19xx (alla konsulter antas vara minst 16 år). Samordningsnummer (dag + 60)
accepteras. *Bortvalt:* ingen validering — ett felskrivet personnummer hade
tyst skapat en falsk identitet i systemet.

**B5. Alla tidpunkter lagras som timestamptz i UTC; naiva tider i importfiler
tolkas som Europe/Stockholm.** *Bortvalt:* lokal tid i databasen (går sönder
vid sommartidsövergångar, som är exakt när nattpass-beräkningar sker).

**B6. Exclusion constraint (btree_gist) mot överlappande bokningar.**
`EXCLUDE USING gist (konsult_id WITH =, tstzrange(starttid, sluttid) WITH &&)
WHERE (status = 'bokad')` — databasen gör det omöjligt att dubbelboka en
konsult, även för framtida kodvägar och parallella förfrågningar. Importrader
som överlappar en befintlig bokning rapporteras som radfel. *Bortvalt:* enbart
applikationskontroll (kan kringgås av race mellan två förfrågningar).

**B7. Kvalifikation kräver att introduktionsdatum finns och ligger senast på
passets startdatum (lokal tid).** En kvalifikationsrad utan datum, eller med
datum efter passet, räknas inte som genomförd introduktion.

**B8. Kund auto-skapas (med tom ort) vid bokningsimport om den saknas,**
och loggas i auditloggen. Det finns inget separat importkommando för kunder,
så radfel här hade gjort förstagångsimport omöjlig. *Bortvalt:* radfel för
okänd kund.

**B9. Slumpordningen görs i databasen (`ORDER BY random()`),** inte i Python —
en enda kodväg, omöjlig att råka sortera om i applikationslagret.

**B10. Plats-tabell: en rad per begärd plats skapas när förfrågan skapas.**
Grunden för att överbokning blir strukturellt omöjlig (etapp 2: radlåsning med
`FOR UPDATE SKIP LOCKED` + constraint-trigger). *Bortvalt:* enbart radlås på
förfrågningsraden med räknarkolumn — ett skyddslager i stället för två, och
räknare kan divergera från verkligheten.

**B11. Utvecklingscontainern saknar Docker-daemon; Postgres 16 körs direkt i
containern i stället.** `docker-compose.yml` levereras och används lokalt av
utvecklare. Testerna kör alltid mot riktig Postgres, aldrig SQLite.

**B12. Import är tolerant per rad:** saknade kolumner rapporteras (utan
krasch, inget importeras), radfel samlas per rad med radnummer medan giltiga
rader importeras (savepoint per rad). Återimport av samma fil uppdaterar
befintliga konsulter (nyckel: personnummer_hash).
