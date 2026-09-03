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
    Float,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
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


class ConditionalOrder(Base):
    __tablename__ = "conditional_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    trigger: Mapped[dict] = mapped_column(JSON)
    plan: Mapped[dict] = mapped_column(JSON)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    triggered_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ScheduleSettings(Base):
    __tablename__ = "schedule_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    max_credit: Mapped[float] = mapped_column(Float, default=500.0)
    universe: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_run_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_action: Mapped[str] = mapped_column(
        String(32), default="stop_cancel_flatten"
    )
    wound_down: Mapped[bool] = mapped_column(Boolean, default=False)
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
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    est_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)


class ApiUsage(Base):
    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    window_id: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    remaining_reported: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reset_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class ApiBudget(Base):
    __tablename__ = "api_budgets"
    __table_args__ = (UniqueConstraint("provider", "limit_type", name="uq_budget_provider_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="window")
    limit_type: Mapped[str] = mapped_column(String(16))
    limit_value: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    warn_pct: Mapped[int] = mapped_column(Integer, default=80)
    action: Mapped[str] = mapped_column(String(24), default="block_degrade")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


_engine = None
SessionLocal = None


def _now():
    return datetime.now(timezone.utc)


def is_connected():
    return SessionLocal is not None


def _ensure_columns(engine):
    """create_all will not ALTER existing Postgres tables. Add new columns."""
    insp = inspect(engine)
    wanted = {
        "schedule_settings": {
            "window_start": "TIMESTAMP WITH TIME ZONE",
            "window_end": "TIMESTAMP WITH TIME ZONE",
            "end_action": "VARCHAR(32) DEFAULT 'stop_cancel_flatten'",
            "wound_down": "BOOLEAN DEFAULT FALSE",
        },
        "invocation_logs": {
            "prompt_tokens": "INTEGER",
            "completion_tokens": "INTEGER",
            "total_tokens": "INTEGER",
            "credits": "INTEGER",
            "est_cost_usd": "NUMERIC(12, 6)",
        },
    }
    dialect = engine.dialect.name
    for table, cols in wanted.items():
        if table not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name in existing:
                continue
            col_ddl = ddl
            if dialect == "sqlite":
                col_ddl = (
                    ddl.replace("TIMESTAMP WITH TIME ZONE", "DATETIME")
                    .replace("BOOLEAN DEFAULT FALSE", "BOOLEAN DEFAULT 0")
                    .replace("NUMERIC(12, 6)", "FLOAT")
                )
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {col_ddl}"))


def _seed_budgets(session):
    if session.query(ApiBudget).count() > 0:
        return
    now = _now()
    session.add_all(
        [
            ApiBudget(
                provider="groq",
                scope="window",
                limit_type="tokens",
                limit_value=200_000,
                warn_pct=80,
                action="block_degrade",
                updated_at=now,
            ),
            ApiBudget(
                provider="tavily",
                scope="window",
                limit_type="credits",
                limit_value=100,
                warn_pct=80,
                action="block_degrade",
                updated_at=now,
            ),
            ApiBudget(
                provider="alpaca",
                scope="window",
                limit_type="requests",
                limit_value=10_000,
                warn_pct=80,
                action="block_degrade",
                updated_at=now,
            ),
        ]
    )


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
    _ensure_columns(_engine)
    SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    with SessionLocal() as session:
        row = session.get(AccountSettings, ACCOUNT_ID)
        if row is None:
            session.add(AccountSettings(id=ACCOUNT_ID))
            session.commit()
        sched = session.get(ScheduleSettings, ACCOUNT_ID)
        if sched is None:
            session.add(
                ScheduleSettings(
                    id=ACCOUNT_ID,
                    enabled=False,
                    interval_seconds=1800,
                    max_credit=500.0,
                    universe=["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"],
                    end_action="stop_cancel_flatten",
                    wound_down=False,
                )
            )
            session.commit()
        _seed_budgets(session)
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
