"""SQLAlchemy engine + schema for the PoC (one paper profile).

Postgres in docker-compose is the runtime target. SQLite is allowed in tests
(`sqlite://` with a StaticPool). DATABASE_URL is required at FastAPI startup.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

load_dotenv()

ACCOUNT_ID = 1
EDITABLE_AGENTS = ("sentiment", "decision", "technical", "features")


class Base(DeclarativeBase):
    pass


class AccountSettings(Base):
    __tablename__ = "account_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alpaca_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    alpaca_secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    groq_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    tavily_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class AgentSettings(Base):
    __tablename__ = "agent_settings"

    agent_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    deep: Mapped[bool] = mapped_column(Boolean, default=False)
    indicators: Mapped[list | None] = mapped_column(JSON, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class InvocationLog(Base):
    __tablename__ = "invocation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    symbol: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)


_engine = None
SessionLocal = None


def _now():
    return datetime.now(timezone.utc)


def is_connected():
    return SessionLocal is not None


def connect(url=None):
    """Create engine, ping, create tables, seed the singleton account row."""
    global _engine, SessionLocal
    load_dotenv()
    url = (url or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is required. Start Postgres with `docker compose up -d` "
            "and set DATABASE_URL (see .env.example; compose publishes Postgres on 5433)."
        )
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, **kwargs)
    with _engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    Base.metadata.create_all(_engine)
    SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    with SessionLocal() as session:
        row = session.get(AccountSettings, ACCOUNT_ID)
        if row is None:
            session.add(AccountSettings(id=ACCOUNT_ID))
            session.commit()
    return SessionLocal


def session():
    if SessionLocal is None:
        raise RuntimeError("database is not connected")
    return SessionLocal()


def close():
    global _engine, SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    SessionLocal = None
