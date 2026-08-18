"""Datamodell för den deterministiska kärnan.

Hårda regler som speglas i schemat:
- Inga score-/rank-kolumner får förekomma någonstans (juridiskt designval).
- Personnummer lagras endast som HMAC-SHA256-hash (kolumnen personnummer_hash).
- Överbokning är omöjlig på databasnivå: en förfrågan har exakt
  antal_begarda platsrader, och en constraint-trigger (migration 0002) vaktar
  antalet. En exclusion constraint på bokning (migration 0001) gör
  överlappande bokningar för samma konsult omöjliga.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Bas(DeclarativeBase):
    pass


def _enumvarden(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


class Konsult(Bas):
    __tablename__ = "konsult"

    id: Mapped[int] = mapped_column(primary_key=True)
    namn: Mapped[str] = mapped_column(String(200))
    telefon: Mapped[str] = mapped_column(String(20), unique=True)  # E.164, +46...
    personnummer_hash: Mapped[str] = mapped_column(String(64), unique=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True)
    anstallningsform: Mapped[str] = mapped_column(String(100), default="")

    kvalifikationer: Mapped[list[Kvalifikation]] = relationship(back_populates="konsult")


class Kund(Bas):
    __tablename__ = "kund"

    id: Mapped[int] = mapped_column(primary_key=True)
    namn: Mapped[str] = mapped_column(String(200), unique=True)
    ort: Mapped[str] = mapped_column(String(200), default="")


class Kvalifikation(Bas):
    """Konsulten får arbeta hos kunden. Introduktion är genomförd när
    introduktionsdatum är satt och ligger senast på passets startdatum."""

    __tablename__ = "kvalifikation"
    __table_args__ = (UniqueConstraint("konsult_id", "kund_id", name="uq_kvalifikation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    konsult_id: Mapped[int] = mapped_column(ForeignKey("konsult.id"))
    kund_id: Mapped[int] = mapped_column(ForeignKey("kund.id"))
    introduktionsdatum: Mapped[date | None] = mapped_column(Date, nullable=True)

    konsult: Mapped[Konsult] = relationship(back_populates="kvalifikationer")
    kund: Mapped[Kund] = relationship()


class BokningStatus(enum.Enum):
    BOKAD = "bokad"
    AVBOKAD = "avbokad"


class BokningKalla(enum.Enum):
    INTERN = "intern"          # pass skapade i detta system
    IMPORTERAD = "importerad"  # befintliga pass importerade från Excel


class Bokning(Bas):
    """Ett arbetspass. Utöver kontrollerna nedan skyddas tabellen av en
    exclusion constraint (ex_bokning_overlapp, se migration 0001) som gör det
    omöjligt att boka samma konsult på överlappande tider."""

    __tablename__ = "bokning"
    __table_args__ = (
        CheckConstraint("sluttid > starttid", name="ck_bokning_tidsordning"),
        Index("ix_bokning_konsult_start", "konsult_id", "starttid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    konsult_id: Mapped[int] = mapped_column(ForeignKey("konsult.id"))
    kund_id: Mapped[int] = mapped_column(ForeignKey("kund.id"))
    starttid: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sluttid: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[BokningStatus] = mapped_column(
        Enum(BokningStatus, name="bokning_status", values_callable=_enumvarden),
        default=BokningStatus.BOKAD,
    )
    kalla: Mapped[BokningKalla] = mapped_column(
        Enum(BokningKalla, name="bokning_kalla", values_callable=_enumvarden)
    )

    konsult: Mapped[Konsult] = relationship()
    kund: Mapped[Kund] = relationship()


class ForfraganStatus(enum.Enum):
    MOTTAGEN = "mottagen"
    GODKAND = "godkand"
    UTSKICKAD = "utskickad"
    DELVIS_FYLLD = "delvis_fylld"
    FYLLD = "fylld"
    STANGD = "stangd"
    ESKALERAD = "eskalerad"


class Forfragan(Bas):
    __tablename__ = "forfragan"
    __table_args__ = (
        CheckConstraint("sluttid > starttid", name="ck_forfragan_tidsordning"),
        CheckConstraint("antal_begarda > 0", name="ck_forfragan_antal"),
        Index("ix_forfragan_kund_status", "kund_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kund_id: Mapped[int] = mapped_column(ForeignKey("kund.id"))
    inkommen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    antal_begarda: Mapped[int] = mapped_column()
    starttid: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sluttid: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ForfraganStatus] = mapped_column(
        Enum(ForfraganStatus, name="forfragan_status", values_callable=_enumvarden),
        default=ForfraganStatus.MOTTAGEN,
    )
    originaltext: Mapped[str] = mapped_column(Text, default="")

    kund: Mapped[Kund] = relationship()
    platser: Mapped[list[Plats]] = relationship(back_populates="forfragan")


class Plats(Bas):
    """En rad per begärd plats i en förfrågan — det strukturella taket för
    tilldelning. Tilldelning sker genom att en ledig rad (konsult_id IS NULL)
    tas med SELECT ... FOR UPDATE SKIP LOCKED. Det kan aldrig finnas fler
    tilldelningar än rader; migration 0002 lägger en constraint-trigger som
    dessutom vaktar att antalet rader aldrig överstiger antal_begarda."""

    __tablename__ = "plats"
    __table_args__ = (
        Index(
            "uq_plats_forfragan_konsult",
            "forfragan_id",
            "konsult_id",
            unique=True,
            postgresql_where=text("konsult_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    forfragan_id: Mapped[int] = mapped_column(ForeignKey("forfragan.id"))
    konsult_id: Mapped[int | None] = mapped_column(ForeignKey("konsult.id"), nullable=True)
    bokning_id: Mapped[int | None] = mapped_column(ForeignKey("bokning.id"), nullable=True)
    tilldelad: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    forfragan: Mapped[Forfragan] = relationship(back_populates="platser")
    konsult: Mapped[Konsult | None] = relationship()


class Utskick(Bas):
    """Ett registrerat (SMS-)utskick till en konsult för en förfrågan.
    Själva SMS-sändningen ligger utanför detta projekt; svar är den råa
    svarstexten och tolkas av serviceskiktet."""

    __tablename__ = "utskick"
    __table_args__ = (
        UniqueConstraint("forfragan_id", "konsult_id", name="uq_utskick"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    forfragan_id: Mapped[int] = mapped_column(ForeignKey("forfragan.id"))
    konsult_id: Mapped[int] = mapped_column(ForeignKey("konsult.id"))
    skickad: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    meddelandetext: Mapped[str] = mapped_column(Text)
    svar: Mapped[str | None] = mapped_column(Text, nullable=True)
    svarstid: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    forfragan: Mapped[Forfragan] = relationship()
    konsult: Mapped[Konsult] = relationship()


class Auditlogg(Bas):
    __tablename__ = "auditlogg"
    __table_args__ = (Index("ix_auditlogg_tidpunkt", "tidpunkt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tidpunkt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    aktor: Mapped[str] = mapped_column(String(200))
    handelse: Mapped[str] = mapped_column(String(100))
    fore: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    efter: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
