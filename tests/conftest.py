"""Testerna körs alltid mot riktig Postgres (aldrig SQLite) — samtidighets-
och triggerbeteende är en del av det som testas. Schemat byggs genom att
köra Alembic-migrationerna, så att triggers och constraints testas exakt
som de ser ut i produktion."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL_TEST

ROT = Path(__file__).resolve().parent.parent

ALLA_TABELLER = (
    "auditlogg, utskick, plats, bokning, forfragan, kvalifikation, konsult, kund"
)


@pytest.fixture(scope="session")
def motor():
    motor = create_engine(DATABASE_URL_TEST, pool_size=25, max_overflow=10)
    with motor.begin() as k:
        k.execute(text("DROP EXTENSION IF EXISTS btree_gist CASCADE"))
        k.execute(text("DROP SCHEMA public CASCADE"))
        k.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(ROT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL_TEST)
    command.upgrade(cfg, "head")
    yield motor
    motor.dispose()


def _rensa(motor) -> None:
    with motor.begin() as k:
        k.execute(text(f"TRUNCATE {ALLA_TABELLER} RESTART IDENTITY CASCADE"))


@pytest.fixture()
def sessionfabrik(motor):
    """Ren databas + sessionsfabrik. Race-testerna skapar en session per tråd."""
    _rensa(motor)
    return sessionmaker(bind=motor, expire_on_commit=False)


@pytest.fixture()
def session(sessionfabrik):
    with sessionfabrik() as s:
        yield s
