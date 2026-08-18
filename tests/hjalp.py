"""Testhjälp: giltiga personnummer, Excel-filer och seed-data."""
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.importers.gemensamt import hash_personnummer, luhn_kontrollsiffra
from app.models import (
    Bokning,
    BokningKalla,
    BokningStatus,
    Konsult,
    Kund,
    Kvalifikation,
)


def giltigt_personnummer(index: int, fodd: date = date(1990, 1, 1)) -> str:
    """Ett Luhn-giltigt 12-siffrigt personnummer, unikt per index."""
    nnn = f"{index:03d}"
    stomme = fodd.strftime("%y%m%d") + nnn
    return fodd.strftime("%Y%m%d") + nnn + str(luhn_kontrollsiffra(stomme))


def skriv_xlsx(sokvag: Path, kolumner: list[str], rader: list[list]) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(kolumner)
    for rad in rader:
        ws.append(rad)
    wb.save(sokvag)
    return sokvag


def ny_konsult(
    session: Session,
    namn: str = "Test Konsult",
    index: int = 1,
    aktiv: bool = True,
    form: str = "Timanställd",
) -> Konsult:
    konsult = Konsult(
        namn=namn,
        telefon=f"+4670123{index:04d}",
        personnummer_hash=hash_personnummer(giltigt_personnummer(index)),
        aktiv=aktiv,
        anstallningsform=form,
    )
    session.add(konsult)
    session.flush()
    return konsult


def ny_kund(session: Session, namn: str = "Budbee Södertälje", ort: str = "Södertälje") -> Kund:
    kund = Kund(namn=namn, ort=ort)
    session.add(kund)
    session.flush()
    return kund


def ny_kvalifikation(
    session: Session,
    konsult: Konsult,
    kund: Kund,
    intro: date | None = date(2025, 1, 1),
) -> Kvalifikation:
    kvalifikation = Kvalifikation(
        konsult_id=konsult.id, kund_id=kund.id, introduktionsdatum=intro
    )
    session.add(kvalifikation)
    session.flush()
    return kvalifikation


def ny_bokning(
    session: Session,
    konsult: Konsult,
    kund: Kund,
    starttid: datetime,
    sluttid: datetime,
    kalla: BokningKalla = BokningKalla.IMPORTERAD,
    status: BokningStatus = BokningStatus.BOKAD,
) -> Bokning:
    bokning = Bokning(
        konsult_id=konsult.id,
        kund_id=kund.id,
        starttid=starttid,
        sluttid=sluttid,
        status=status,
        kalla=kalla,
    )
    session.add(bokning)
    session.flush()
    return bokning
