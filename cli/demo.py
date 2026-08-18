"""Demo: hela Budbee-scenariot steg för steg mot den lokala databasen.

Användning: python -m cli.demo

VARNING: kommandot RENSAR databasen (DATABASE_URL), kör migrationerna och
seedar om demodata. Kör aldrig mot en databas med riktiga uppgifter.

Scenariot (det mätbara slutvillkoret): förfrågan skapas → kvalificerade
konsulter listas → utskick registreras → svar strömmar in inklusive
samtidiga JA → exakt rätt antal tilldelas → en konsult drar sig ur →
platsen öppnas igen och fylls → statusrapport skrivs ut."""
import sys
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

from app.config import DATABASE_URL
from app.db import skapa_motor, skapa_sessionfabrik
from app.importers.gemensamt import hash_personnummer, luhn_kontrollsiffra
from app.models import (
    Auditlogg,
    Bokning,
    BokningKalla,
    BokningStatus,
    Konsult,
    Kund,
    Kvalifikation,
)
from app.services import (
    forfragan_status,
    godkann_forfragan,
    kontrollera_atl,
    lista_kvalificerade,
    registrera_svar,
    registrera_utskick,
    skapa_forfragan,
    slapp_plats,
    stang_forfragan,
)
from app.tid import STOCKHOLM, till_lokal, till_utc

ROT = Path(__file__).resolve().parent.parent
LINJE = "─" * 64


def _steg(nummer: int, titel: str) -> None:
    print(f"\n{LINJE}\n STEG {nummer} · {titel}\n{LINJE}")


def _klocka(dt: datetime) -> str:
    return till_lokal(dt).strftime("%H:%M")


def _fiktivt_personnummer(index: int) -> str:
    fodd = date(1990, 1, 1)
    stomme = fodd.strftime("%y%m%d") + f"{index:03d}"
    return fodd.strftime("%Y%m%d") + f"{index:03d}" + str(luhn_kontrollsiffra(stomme))


def _seed(sessionfabrik, passtart, passlut):
    """Fiktiva konsulter + Budbee Södertälje. Returnerar (kund_id,
    {namn: konsult_id}, {namn: förklaring till exkludering})."""
    with sessionfabrik() as s:
        budbee = Kund(namn="Budbee Södertälje", ort="Södertälje")
        coop = Kund(namn="Coop Logistik Västerås", ort="Västerås")
        s.add_all([budbee, coop])
        s.flush()

        namn_lista = [
            "Amina Ali", "Björn Berg", "Cecilia Ek", "David Dahl",
            "Elena Eriksson", "Farid Farhadi", "Gustav Grön", "Hanna Holm",
            "Ivan Islamov", "Johanna Järv",
        ]
        konsulter: dict[str, Konsult] = {}
        for i, namn in enumerate(namn_lista, start=1):
            k = Konsult(
                namn=namn,
                telefon=f"+46701234{i:03d}",
                personnummer_hash=hash_personnummer(_fiktivt_personnummer(i)),
                aktiv=(namn != "Johanna Järv"),
                anstallningsform="Timanställd",
            )
            s.add(k)
            konsulter[namn] = k
        s.flush()

        intro = passtart.date() - timedelta(days=60)
        for namn, k in konsulter.items():
            s.add(
                Kvalifikation(
                    konsult_id=k.id,
                    kund_id=budbee.id,
                    # Gustav har aldrig genomfört introduktionen hos Budbee
                    introduktionsdatum=None if namn == "Gustav Grön" else intro,
                )
            )

        # Importerade befintliga pass (räknas fullt ut i överlapp och ATL):
        # Hanna jobbar hos Coop 18–23 i kväll → överlappar förfrågan.
        s.add(
            Bokning(
                konsult_id=konsulter["Hanna Holm"].id,
                kund_id=coop.id,
                starttid=passtart + timedelta(hours=2),
                sluttid=passlut + timedelta(hours=1),
                status=BokningStatus.BOKAD,
                kalla=BokningKalla.IMPORTERAD,
            )
        )
        # Ivan gick av ett nattpass kl 06 i morse → bara 10 h vila före 16:00.
        s.add(
            Bokning(
                konsult_id=konsulter["Ivan Islamov"].id,
                kund_id=coop.id,
                starttid=passtart - timedelta(hours=18),  # 22:00 i går
                sluttid=passtart - timedelta(hours=10),   # 06:00 i dag
                status=BokningStatus.BOKAD,
                kalla=BokningKalla.IMPORTERAD,
            )
        )
        s.commit()
        forklaringar = {
            "Gustav Grön": "ej genomförd introduktion hos Budbee",
            "Hanna Holm": "har redan pass 18–23 (importerat, Coop)",
            "Johanna Järv": "inte aktiv anställning",
        }
        return budbee.id, {namn: k.id for namn, k in konsulter.items()}, forklaringar


def huvud() -> int:
    print(LINJE)
    print(" MILJONBEMANNING — demo av den deterministiska kärnan")
    print(" Scenario: Budbee Södertälje behöver 3 personer i kväll 16–22")
    print(LINJE)

    passdatum = date.today() + timedelta(days=1)
    passtart = till_utc(datetime.combine(passdatum, time(16, 0), tzinfo=STOCKHOLM))
    passlut = till_utc(datetime.combine(passdatum, time(22, 0), tzinfo=STOCKHOLM))

    print("\nFörbereder: migrerar och rensar demodatabasen, seedar testdata ...")
    motor = skapa_motor()
    cfg = Config(str(ROT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(cfg, "head")
    with motor.begin() as k:
        k.execute(
            text(
                "TRUNCATE auditlogg, utskick, plats, bokning, forfragan, "
                "kvalifikation, konsult, kund RESTART IDENTITY CASCADE"
            )
        )
    sessionfabrik = skapa_sessionfabrik(motor)
    kund_id, konsult_id, forklaringar = _seed(sessionfabrik, passtart, passlut)
    namn_pa = {v: k for k, v in konsult_id.items()}
    print(f"Klart: {len(konsult_id)} fiktiva konsulter, Budbee Södertälje som kund,")
    print("två importerade befintliga pass (Hanna i kväll, Ivan i natt).")

    with sessionfabrik() as s:
        # ── STEG 1 ────────────────────────────────────────────────────
        _steg(1, "Förfrågan tas emot")
        originaltext = (
            "Hej! Vi behöver 3 personer till terminalen i Södertälje "
            "imorgon med start kl 16, slut 22. /Budbee"
        )
        print(f'Inkommet meddelande: "{originaltext}"')
        f = skapa_forfragan(
            s,
            kund_id=kund_id,
            antal_begarda=3,
            starttid=passtart,
            sluttid=passlut,
            originaltext=originaltext,
            aktor="demo",
        )
        fid = f["forfragan_id"]
        print(f"→ Förfrågan #{fid} skapad, status: {f['status']}, "
              f"3 platsrader reserverade i databasen.")

        # ── STEG 2 ────────────────────────────────────────────────────
        _steg(2, "Konsultchefen godkänner förfrågan")
        g = godkann_forfragan(s, fid, aktor="demo")
        print(f"→ Status: {g['status']}")

        # ── STEG 3 ────────────────────────────────────────────────────
        _steg(3, "Kvalificerade konsulter listas (alltid i slumpad ordning)")
        pool = lista_kvalificerade(s, fid)
        print("Kvalificerade (aktiv + introduktion hos Budbee + ledig 16–22):")
        for k in pool:
            print(f"   • {k['namn']}  {k['telefon']}")
        pool2 = lista_kvalificerade(s, fid)
        print("Samma fråga igen — samma pool, NY slumpad ordning "
              "(juridiskt krav: ingen rangordning):")
        print("   " + ", ".join(k["namn"] for k in pool2))
        print("Utanför poolen:")
        for namn, orsak in forklaringar.items():
            print(f"   ✗ {namn} — {orsak}")

        # ── STEG 4 ────────────────────────────────────────────────────
        _steg(4, "ATL-spärren granskar erbjudandena")
        atl = kontrollera_atl(s, konsult_id["Ivan Islamov"], passtart, passlut)
        print("Ivan Islamov gick av ett nattpass kl 06 i morse. kontrollera_atl:")
        for brott in atl["brott"]:
            print(f"   ✗ {brott['regel']}: {brott['beskrivning']}")

        # ── STEG 5 ────────────────────────────────────────────────────
        _steg(5, "SMS-utskick registreras till poolen")
        meddelande = (
            "Budbee Södertälje behöver 3 pers imorgon 16–22. "
            "Först till kvarn — svara JA om du kan ta passet."
        )
        print(f'Meddelande: "{meddelande}"')
        ut = registrera_utskick(
            s, fid, [k["konsult_id"] for k in pool], meddelande, aktor="demo"
        )
        print(f"→ {len(ut['skickade'])} utskick registrerade, status: {ut['status']}")
        for blockerad in ut["blockerade_atl"]:
            print(f"   ✗ {namn_pa[blockerad['konsult_id']]} BLOCKERAD av ATL "
                  f"({blockerad['brott'][0]['regel']}) — fick inget erbjudande")
        utskick_for = {u["konsult_id"]: u["utskick_id"] for u in ut["skickade"]}

    # ── STEG 6 ────────────────────────────────────────────────────────
    _steg(6, "Svaren strömmar in — fem JA i exakt samma ögonblick")
    with sessionfabrik() as s:
        fritext = registrera_svar(
            s, utskick_for[konsult_id["Farid Farhadi"]], "Nej, kan inte idag", aktor="demo"
        )
        print(f"Farid Farhadi svarar: \"Nej, kan inte idag\" → tolkning: {fritext['svar']}")
        print("   (kärnan gissar aldrig på fritext — endast entydiga svar räknas;")
        print("    tolkning av fritext är AI-lagrets ansvar i nästa projekt)")
        nej = registrera_svar(
            s, utskick_for[konsult_id["Farid Farhadi"]], "NEJ", aktor="demo"
        )
        print(f"Farid Farhadi förtydligar: \"NEJ\" → tolkning: {nej['svar']}")

    ja_namn = ["Amina Ali", "Björn Berg", "Cecilia Ek", "David Dahl", "Elena Eriksson"]
    print(f"{', '.join(ja_namn)} svarar \"JA\" SAMTIDIGT (5 parallella trådar, 3 platser):")
    barriar = threading.Barrier(len(ja_namn))
    las = threading.Lock()
    utfall: list[tuple[str, dict]] = []

    def svara_ja(namn: str) -> None:
        with sessionfabrik() as egen:
            barriar.wait()
            resultat = registrera_svar(
                egen, utskick_for[konsult_id[namn]], "JA", aktor=namn
            )
            with las:
                utfall.append((namn, resultat))

    tradar = [threading.Thread(target=svara_ja, args=(n,)) for n in ja_namn]
    for t in tradar:
        t.start()
    for t in tradar:
        t.join(timeout=30)

    tilldelade = [(n, r) for n, r in utfall if r["tilldelad"]]
    utan_plats = [(n, r) for n, r in utfall if not r["tilldelad"]]
    for namn, r in sorted(utfall, key=lambda p: not p[1]["tilldelad"]):
        if r["tilldelad"]:
            print(f"   ✓ {namn}: JA → PLATS TILLDELAD (bokning #{r['bokning_id']})")
        else:
            print(f"   ✗ {namn}: JA → för sent, alla platser tagna ({r['orsak']})")
    if len(tilldelade) != 3:
        print(f"FEL: {len(tilldelade)} tilldelade — förväntade exakt 3!")
        return 1
    print("→ Verifierat: EXAKT 3 tilldelade — aldrig fler, oavsett samtidighet.")
    with sessionfabrik() as s:
        print(f"→ Förfrågans status: {forfragan_status(s, fid)['status']}")

    # ── STEG 7 ────────────────────────────────────────────────────────
    avhoppare = tilldelade[0][0]
    _steg(7, f"Avhopp — {avhoppare} blir sjuk och drar sig ur")
    with sessionfabrik() as s:
        slappt = slapp_plats(
            s, fid, konsult_id[avhoppare], orsak="sjukdom", aktor=avhoppare
        )
        print(f"→ Platsen öppnas igen, bokningen avbokas. Status: {slappt['status']}")

    # ── STEG 8 ────────────────────────────────────────────────────────
    ersattare = utan_plats[0][0]
    _steg(8, f"Platsen fylls igen — {ersattare} svarar JA på nytt")
    with sessionfabrik() as s:
        nytt = registrera_svar(
            s, utskick_for[konsult_id[ersattare]], "JA", aktor=ersattare
        )
        print(f"   ✓ {ersattare}: JA → PLATS TILLDELAD (bokning #{nytt['bokning_id']})")
        print(f"→ Förfrågans status: {forfragan_status(s, fid)['status']}")

    # ── STEG 9 ────────────────────────────────────────────────────────
    _steg(9, "Förfrågan stängs")
    with sessionfabrik() as s:
        st = stang_forfragan(s, fid, aktor="demo")
        print(f"→ Status: {st['status']}")

    # ── STEG 10 ───────────────────────────────────────────────────────
    _steg(10, "Statusrapport")
    with sessionfabrik() as s:
        rapport = forfragan_status(s, fid)
        print(f"Förfrågan #{rapport['forfragan_id']} — {rapport['kund']}")
        print(f"Pass: {_klocka(passtart)}–{_klocka(passlut)} den {passdatum}")
        print(f"Status: {rapport['status']}   "
              f"Bemannat: {rapport['antal_tilldelade']}/{rapport['antal_begarda']}")
        print("Platser:")
        for plats in rapport["platser"]:
            print(f"   • plats {plats['plats_id']}: {plats['konsult'] or '—'}")
        print("Utskick och svar:")
        for u in rapport["utskick"]:
            svar = f'"{u["svar"]}"' if u["svar"] else "(inget svar)"
            print(f"   • {u['konsult']}: {svar}")
        print("Auditlogg (händelse: antal):")
        for handelse, antal in s.execute(
            select(Auditlogg.handelse, func.count())
            .group_by(Auditlogg.handelse)
            .order_by(func.count().desc())
        ):
            print(f"   {antal:>3} × {handelse}")

    print(f"\n{LINJE}\n KLART — hela scenariot genomfört. Varje steg ovan är\n"
          f" auditloggat och skyddat av databasens spärrar.\n{LINJE}")
    return 0


if __name__ == "__main__":
    sys.exit(huvud())
