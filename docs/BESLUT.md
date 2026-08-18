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

## 2026-08-18 — Etapp 2

**B13. godkann_forfragan tillagd som verktygsfunktion.** Tillståndsmaskinen
har övergången mottagen → godkänd men den ursprungliga verktygslistan saknade
en funktion som utför den. Godkännandet är den mänskliga/agentstyrda grinden
innan utskick, så den behöver en egen atomär funktion. *Bortvalt:* att låta
registrera_utskick auto-godkänna (grinden hade försvunnit).

**B14. Eskalering görs via stang_forfragan(eskalera=True),** inte via en egen
verktygsfunktion — samma terminala flöde, en flagga skiljer. Eskalering är
tillåten från alla aktiva tillstånd utom fylld (en fylld förfrågan är inte
akut).

**B15. Självövergångar är ogiltiga.** byt_status kastar OgiltigOvergang även
för t.ex. godkänd → godkänd; dubbelklick och dubbelanrop ska synas, inte
tystas. Interna anropare hoppar över statusbytet när status redan är rätt.

**B16. Samtidighetsskydd i tre lager** för tilldelning:
(1) SELECT ... FOR UPDATE SKIP LOCKED på platsraderna — samtidiga JA
konkurrerar om ett ändligt antal rader, aldrig om en räknare;
(2) partiellt unikt index — samma konsult kan inte hålla två platser på samma
förfrågan; (3) constraint-trigger som räknar platsrader och tilldelningar mot
antal_begarda + trigger som hindrar att antal_begarda sänks under antalet
platsrader. *Bortvalt:* SERIALIZABLE-isolering (retry-logik i varje anrop för
något databasen kan garantera billigare), deferred constraints (felet ska
komma direkt, inte vid commit).

**B17. Statusomräkning under radlås på förfrågningsraden.** Efter en
tilldelning låses förfrågningsraden och antalet fyllda platser räknas om;
utan låset kunde två samtidiga transaktioner båda räkna "inte fullt" och
ingen sätta fylld. Låsordningen är alltid platsrad → förfrågningsrad, så
dödläge kan inte uppstå.

**B18. Svarstolkning är medvetet strikt.** Endast entydiga token (ja/j/yes/ok
m.fl.) räknas som JA, nej/n/no som NEJ, allt annat "okant" utan tilldelning.
Fritextförståelse är AI-lagrets jobb (senare projekt) — kärnan gissar aldrig.
Råtexten sparas alltid på utskicket.

**B19. Sena JA får svaret "fullt_besatt", inte ett statusfel.** Tilldelning
tillåts tekniskt även i status fylld; radsökningen ger då korrekt orsak.
Samma svar oavsett om JA:et kom mikrosekunder eller minuter för sent.

**B20. Även nekade tilldelningsförsök auditloggas** (händelsen
tilldelning_nekad med orsak) — "allt loggas" gäller också det som inte hände,
det är underlaget för att i efterhand förklara varför en konsult blev utan.

**B21. slapp_plats fungerar även på stängd förfrågan** (utan statusbyte):
avhopp måste alltid kunna registreras och bokningen avbokas, annars blockerar
en kvarglömd bokning konsultens framtida pass via exclusion-constrainten.

## 2026-08-18 — Beställarens kompletteringar av planen

**B22. Dubbla JA är idempotenta (beställarkrav).** Unikhetsgarantin
UNIQUE(förfrågan_id, konsult_id) finns som partiellt unikt index på
platstabellen. registrera_svar/tilldela_plats returnerar vid ett andra JA den
BEFINTLIGA tilldelningen (tilldelad=True, orsak="redan_tilldelad") och tar
aldrig en ny plats — även när de två JA:en kommer samtidigt (kapplöpningen
fångas av unikindexet/exclusion-constrainten och löses upp till ett
idempotent svar). Upprepningen auditloggas som tilldelning_upprepad.
Race-test: samma konsult svarar JA två gånger samtidigt → exakt en
tilldelning. *Tidigare beteende (bortvalt):* andra JA:et behandlades som
nekad tilldelning.

**B23. Ren tidsöverlapp kontrolleras i tilldelningstransaktionen
(beställarkrav).** tilldela_plats verifierar, i samma transaktion som
radlåset, att konsulten saknar överlappande aktiv bokning — utöver
ATL-reglerna. Kapplöpningen mellan två parallella förfrågningar avgörs av
databasens exclusion constraint: exakt en vinner, den andra får orsak
"overlappande_bokning". Race-test: samma konsult svarar JA på två
överlappande förfrågningar samtidigt → exakt en tilldelning.

**B24. Bokningsimporten är atomär (beställarkrav).** Vid radfel rullas HELA
importen tillbaka, kommandot avslutas med felkod och rapporten varnar
uttryckligen om att ATL-kontrollen inte kan litas på förrän importen är
komplett — en delvis importerad passhistorik ser giltig ut men räknar fel.
Konsultimporten delimporterar fortsatt per rad med radrapport (en konsult
för mycket/för lite påverkar inte lagefterlevnaden).

**B25. HMAC-nyckelhantering (beställarnotering).** En förlorad
PNR_HASH_NYCKEL bryter dublettkontrollen permanent — befintliga hashar kan
aldrig matchas mot nya importer. I produktion ska nyckeln ligga i Secret
Manager (eller motsvarande), aldrig i repot eller i en .env som committas.
Utvecklingsnyckeln i app/config.py är enbart för lokal körning.

**B26. Kvalificeringskontroll i registrera_utskick — övervägd men bortvald.**
Verktyget tar en explicit konsultlista och litar på att anroparen (idag
demo/CLI, senare agentlagret) hämtat den via lista_kvalificerade. Kärnan
skyddar det som är farligt på riktigt — överbokning, dubbelbokning och ATL —
på databasnivå; att skicka ett SMS till fel person är återkalleligt och
stoppas senast vid tilldelningen. Kan läggas till senare utan schemaändring.

**Beställarens svar på öppna frågor:** eskalering nås från alla aktiva
tillstånd (inklusive tidsfrist i utskickad; fylld räknas inte som aktivt
behov och behåller endast vägen till stängd/delvis_fylld), och avhopp efter
fylld → delvis_fylld med nytt utskick tillåtet — båda enligt implementationen
i etapp 2.

## 2026-08-18 — Etapp 4

**B27. Demon är också ett slutvillkorstest.** `python -m cli.demo` kör hela
scenariot enbart via serviceskiktets verktygsfunktioner (samma väg som det
framtida AI-lagret), verifierar själv att exakt 3 tilldelas vid de samtidiga
JA:en och avslutar med felkod annars. Demon rensar och seedar om databasen
i DATABASE_URL — den ska aldrig köras mot en databas med riktiga uppgifter.

**B28. requirements.txt i stället för installerbart paket.** Projektet körs
från repo-roten (`python -m cli...`, pytest med pythonpath). Ett
build-backend + paketering hade inte tillfört något i detta skede.
*Bortvalt:* pip install -e med setuptools-konfiguration.

**B29. Demon visar den strikta svarstolkningen öppet:** Farids fritextsvar
("Nej, kan inte idag") tolkas som "okant" och demon förklarar varför —
kärnan gissar aldrig, AI-lagret får tolka fritext senare. Att dölja detta i
demon hade gett en missvisande bild av kärnans ansvar.
