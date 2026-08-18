"""Motor- och sessionsfabriker. Ingen global motor — anroparen väljer databas."""
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL


def skapa_motor(url: str | None = None, **kwargs) -> Engine:
    kwargs.setdefault("pool_size", 20)
    kwargs.setdefault("max_overflow", 10)
    return create_engine(url or DATABASE_URL, **kwargs)


def skapa_sessionfabrik(motor: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=motor, expire_on_commit=False)
