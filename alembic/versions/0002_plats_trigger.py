"""Constraint-triggers: tilldelning över begärt antal är omöjlig på
databasnivå (hård regel), oavsett vilken kod som skriver.

- trg_vakta_plats_antal (plats, INSERT/UPDATE): antalet platsrader och
  antalet tilldelade platser kan aldrig överstiga förfrågans antal_begarda.
  Vid INSERT tas radlås på förfrågningsraden så att samtidiga INSERT
  serialiseras och räkningen är exakt; vid UPDATE räcker platsradens eget
  radlås (antalet rader är redan begränsat).
- trg_vakta_forfragan_antal (forfragan, UPDATE): antal_begarda kan inte
  sänkas under antalet befintliga platsrader.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-18
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
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
                RAISE EXCEPTION 'förfrågan % finns inte', NEW.forfragan_id;
            END IF;
            SELECT count(*) INTO antal_rader
            FROM plats WHERE forfragan_id = NEW.forfragan_id;
            IF antal_rader > max_antal THEN
                RAISE EXCEPTION
                    'överbokning stoppad: % platsrader för förfrågan % (max %)',
                    antal_rader, NEW.forfragan_id, max_antal;
            END IF;
            SELECT count(*) INTO antal_tilldelade
            FROM plats
            WHERE forfragan_id = NEW.forfragan_id AND konsult_id IS NOT NULL;
            IF antal_tilldelade > max_antal THEN
                RAISE EXCEPTION
                    'överbokning stoppad: % tilldelade platser för förfrågan % (max %)',
                    antal_tilldelade, NEW.forfragan_id, max_antal;
            END IF;
            RETURN NULL;
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_vakta_plats_antal
        AFTER INSERT OR UPDATE ON plats
        FOR EACH ROW EXECUTE FUNCTION vakta_plats_antal();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION vakta_forfragan_antal() RETURNS trigger AS $$
        DECLARE
            antal_rader integer;
        BEGIN
            IF NEW.antal_begarda < OLD.antal_begarda THEN
                SELECT count(*) INTO antal_rader
                FROM plats WHERE forfragan_id = NEW.id;
                IF NEW.antal_begarda < antal_rader THEN
                    RAISE EXCEPTION
                        'antal_begarda (%) kan inte sänkas under antalet platsrader (%)',
                        NEW.antal_begarda, antal_rader;
                END IF;
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_vakta_forfragan_antal
        BEFORE UPDATE OF antal_begarda ON forfragan
        FOR EACH ROW EXECUTE FUNCTION vakta_forfragan_antal();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_vakta_forfragan_antal ON forfragan")
    op.execute("DROP FUNCTION IF EXISTS vakta_forfragan_antal()")
    op.execute("DROP TRIGGER IF EXISTS trg_vakta_plats_antal ON plats")
    op.execute("DROP FUNCTION IF EXISTS vakta_plats_antal()")
