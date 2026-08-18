"""Grundschema: konsult, kund, kvalifikation, bokning, forfragan, plats,
utskick, auditlogg.

Databasnivåskydd som ingår här:
- Exclusion constraint ex_bokning_overlapp: samma konsult kan aldrig ha två
  överlappande aktiva bokningar, oavsett vilken kodväg som skriver.
- Partiellt unikt index på plats: en konsult kan bara hålla en plats per
  förfrågan.

Revision ID: 0001
Revises:
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "konsult",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("namn", sa.String(length=200), nullable=False),
        sa.Column("telefon", sa.String(length=20), nullable=False),
        sa.Column("personnummer_hash", sa.String(length=64), nullable=False),
        sa.Column("aktiv", sa.Boolean(), nullable=False),
        sa.Column("anstallningsform", sa.String(length=100), nullable=False),
        sa.UniqueConstraint("telefon", name="uq_konsult_telefon"),
        sa.UniqueConstraint("personnummer_hash", name="uq_konsult_personnummer_hash"),
    )

    op.create_table(
        "kund",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("namn", sa.String(length=200), nullable=False),
        sa.Column("ort", sa.String(length=200), nullable=False),
        sa.UniqueConstraint("namn", name="uq_kund_namn"),
    )

    op.create_table(
        "kvalifikation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("konsult_id", sa.Integer(), sa.ForeignKey("konsult.id"), nullable=False),
        sa.Column("kund_id", sa.Integer(), sa.ForeignKey("kund.id"), nullable=False),
        sa.Column("introduktionsdatum", sa.Date(), nullable=True),
        sa.UniqueConstraint("konsult_id", "kund_id", name="uq_kvalifikation"),
    )

    op.create_table(
        "bokning",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("konsult_id", sa.Integer(), sa.ForeignKey("konsult.id"), nullable=False),
        sa.Column("kund_id", sa.Integer(), sa.ForeignKey("kund.id"), nullable=False),
        sa.Column("starttid", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sluttid", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("bokad", "avbokad", name="bokning_status"),
            nullable=False,
        ),
        sa.Column(
            "kalla",
            sa.Enum("intern", "importerad", name="bokning_kalla"),
            nullable=False,
        ),
        sa.CheckConstraint("sluttid > starttid", name="ck_bokning_tidsordning"),
    )
    op.create_index("ix_bokning_konsult_start", "bokning", ["konsult_id", "starttid"])
    # DB-nivåskydd: en konsult kan aldrig dubbelbokas på överlappande tider.
    op.execute(
        "ALTER TABLE bokning ADD CONSTRAINT ex_bokning_overlapp "
        "EXCLUDE USING gist (konsult_id WITH =, tstzrange(starttid, sluttid) WITH &&) "
        "WHERE (status = 'bokad')"
    )

    op.create_table(
        "forfragan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kund_id", sa.Integer(), sa.ForeignKey("kund.id"), nullable=False),
        sa.Column(
            "inkommen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("antal_begarda", sa.Integer(), nullable=False),
        sa.Column("starttid", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sluttid", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "mottagen",
                "godkand",
                "utskickad",
                "delvis_fylld",
                "fylld",
                "stangd",
                "eskalerad",
                name="forfragan_status",
            ),
            nullable=False,
        ),
        sa.Column("originaltext", sa.Text(), nullable=False),
        sa.CheckConstraint("sluttid > starttid", name="ck_forfragan_tidsordning"),
        sa.CheckConstraint("antal_begarda > 0", name="ck_forfragan_antal"),
    )
    op.create_index("ix_forfragan_kund_status", "forfragan", ["kund_id", "status"])

    op.create_table(
        "plats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("forfragan_id", sa.Integer(), sa.ForeignKey("forfragan.id"), nullable=False),
        sa.Column("konsult_id", sa.Integer(), sa.ForeignKey("konsult.id"), nullable=True),
        sa.Column("bokning_id", sa.Integer(), sa.ForeignKey("bokning.id"), nullable=True),
        sa.Column("tilldelad", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_plats_forfragan_konsult",
        "plats",
        ["forfragan_id", "konsult_id"],
        unique=True,
        postgresql_where=sa.text("konsult_id IS NOT NULL"),
    )

    op.create_table(
        "utskick",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("forfragan_id", sa.Integer(), sa.ForeignKey("forfragan.id"), nullable=False),
        sa.Column("konsult_id", sa.Integer(), sa.ForeignKey("konsult.id"), nullable=False),
        sa.Column(
            "skickad",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("meddelandetext", sa.Text(), nullable=False),
        sa.Column("svar", sa.Text(), nullable=True),
        sa.Column("svarstid", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("forfragan_id", "konsult_id", name="uq_utskick"),
    )

    op.create_table(
        "auditlogg",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "tidpunkt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("aktor", sa.String(length=200), nullable=False),
        sa.Column("handelse", sa.String(length=100), nullable=False),
        sa.Column("fore", postgresql.JSONB(), nullable=True),
        sa.Column("efter", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_auditlogg_tidpunkt", "auditlogg", ["tidpunkt"])


def downgrade() -> None:
    op.drop_table("auditlogg")
    op.drop_table("utskick")
    op.drop_table("plats")
    op.drop_table("forfragan")
    op.drop_table("bokning")
    op.drop_table("kvalifikation")
    op.drop_table("kund")
    op.drop_table("konsult")
    op.execute("DROP TYPE IF EXISTS forfragan_status")
    op.execute("DROP TYPE IF EXISTS bokning_kalla")
    op.execute("DROP TYPE IF EXISTS bokning_status")
