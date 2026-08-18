"""Utökat DB-nivåskydd för platsrader (åtgärdar fynd från
flerlinsgranskningen):

1. En platsrad kan aldrig flyttas mellan förfrågningar. UPDATE-grenen i
   vakta_plats_antal räknar utan radlås på förfrågningsraden, vilket bara är
   säkert när forfragan_id är oföränderlig — nu upprätthåller triggern det.
2. En TILLDELAD platsrad kan inte raderas. Platsraden är den enda kopplingen
   mellan en intern bokning och dess förfrågan; raderas den blir bokningen
   föräldralös och platsen ser ledig ut — förfrågan kan då i praktiken
   överbemannas. Släpp platsen via slapp_plats (avbokar bokningen) först.
   Lediga platsrader kan fortfarande raderas (sänker bara kapaciteten).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-18
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_FUNKTION_MED_FLYTTSKYDD = """
CREATE OR REPLACE FUNCTION vakta_plats_antal() RETURNS trigger AS $$
DECLARE
    max_antal integer;
    antal_rader integer;
    antal_tilldelade integer;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.forfragan_id IS DISTINCT FROM OLD.forfragan_id THEN
        RAISE EXCEPTION
            'platsrader får inte flyttas mellan förfrågningar (plats %%)', NEW.id;
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT antal_begarda INTO max_antal
        FROM forfragan WHERE id = NEW.forfragan_id FOR UPDATE;
    ELSE
        SELECT antal_begarda INTO max_antal
        FROM forfragan WHERE id = NEW.forfragan_id;
    END IF;
    IF max_antal IS NULL THEN
        RAISE EXCEPTION 'förfrågan %% finns inte', NEW.forfragan_id;
    END IF;
    SELECT count(*) INTO antal_rader
    FROM plats WHERE forfragan_id = NEW.forfragan_id;
    IF antal_rader > max_antal THEN
        RAISE EXCEPTION
            'överbokning stoppad: %% platsrader för förfrågan %% (max %%)',
            antal_rader, NEW.forfragan_id, max_antal;
    END IF;
    SELECT count(*) INTO antal_tilldelade
    FROM plats
    WHERE forfragan_id = NEW.forfragan_id AND konsult_id IS NOT NULL;
    IF antal_tilldelade > max_antal THEN
        RAISE EXCEPTION
            'överbokning stoppad: %% tilldelade platser för förfrågan %% (max %%)',
            antal_tilldelade, NEW.forfragan_id, max_antal;
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;
"""

_FUNKTION_UTAN_FLYTTSKYDD = """
CREATE OR REPLACE FUNCTION vakta_plats_antal() RETURNS trigger AS $$
DECLARE
    max_antal integer;
    antal_rader integer;
    antal_tilldelade integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT antal_begarda INTO max_antal
        FROM forfragan WHERE id = NEW.forfragan_id FOR UPDATE;
    ELSE
        SELECT antal_begarda INTO max_antal
        FROM forfragan WHERE id = NEW.forfragan_id;
    END IF;
    IF max_antal IS NULL THEN
        RAISE EXCEPTION 'förfrågan %% finns inte', NEW.forfragan_id;
    END IF;
    SELECT count(*) INTO antal_rader
    FROM plats WHERE forfragan_id = NEW.forfragan_id;
    IF antal_rader > max_antal THEN
        RAISE EXCEPTION
            'överbokning stoppad: %% platsrader för förfrågan %% (max %%)',
            antal_rader, NEW.forfragan_id, max_antal;
    END IF;
    SELECT count(*) INTO antal_tilldelade
    FROM plats
    WHERE forfragan_id = NEW.forfragan_id AND konsult_id IS NOT NULL;
    IF antal_tilldelade > max_antal THEN
        RAISE EXCEPTION
            'överbokning stoppad: %% tilldelade platser för förfrågan %% (max %%)',
            antal_tilldelade, NEW.forfragan_id, max_antal;
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_FUNKTION_MED_FLYTTSKYDD.replace("%%", "%"))
    op.execute(
        """
        CREATE OR REPLACE FUNCTION vakta_plats_radering() RETURNS trigger AS $$
        BEGIN
            IF OLD.konsult_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'en tilldelad platsrad får inte raderas (plats %) — '
                    'släpp platsen via slapp_plats först', OLD.id;
            END IF;
            RETURN OLD;
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_vakta_plats_radering
        BEFORE DELETE ON plats
        FOR EACH ROW EXECUTE FUNCTION vakta_plats_radering();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_vakta_plats_radering ON plats")
    op.execute("DROP FUNCTION IF EXISTS vakta_plats_radering()")
    op.execute(_FUNKTION_UTAN_FLYTTSKYDD.replace("%%", "%"))
