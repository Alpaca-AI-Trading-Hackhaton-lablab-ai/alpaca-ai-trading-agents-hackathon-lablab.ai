"""Pydantic v2 response models so FastAPI 0.131 serializes JSON in Rust.

Do not set response_class=ORJSONResponse (deprecated). Return types /
response_model= are the documented fast path (pydantic-core / jiter).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

KeySource = Literal["db", "env", "missing"]


class HealthOut(BaseModel):
    message: str


class GoneOut(BaseModel):
    status: str = "gone"
    use: str = "/pipeline"
    detail: str = "This path no longer runs the agent pipeline."


class AccountOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    status: str | None = None
    mode: str | None = None
    warning: str | None = None


class QuoteOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    bid: float | None = None
    ask: float | None = None
    price: float | None = None
    mode: str | None = None
    warning: str | None = None


class NewsItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    content: str = ""
    url: str = ""
    published_date: str = ""
    score: float | None = None


class SnapshotOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    price: float | None = None
    signal: str | None = None
    trend: str | None = None
    error: str | None = None


class BarOut(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


class LinePointOut(BaseModel):
    time: int
    value: float
    color: str | None = None


class BarsOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    indicators: list[str] = Field(default_factory=list)
    candles: list[BarOut] = Field(default_factory=list)
    overlays: dict[str, list[LinePointOut]] = Field(default_factory=dict)
    oscillators: dict[str, list[LinePointOut]] = Field(default_factory=dict)
    volume: list[LinePointOut] = Field(default_factory=list)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class GroqModelOut(BaseModel):
    id: str
    label: str
    role_hint: str | None = None


class ModelsOut(BaseModel):
    allowlist: list[GroqModelOut]
    defaults: dict[str, str]


class AgentSettingsOut(BaseModel):
    model: str | None = None
    deep: bool | None = None
    indicators: list[str] | None = None


class KeySourcesOut(BaseModel):
    groq: KeySource
    tavily: KeySource
    alpaca_api_key: KeySource
    alpaca_secret_key: KeySource


class SettingsOut(BaseModel):
    keys: KeySourcesOut
    agents: dict[str, AgentSettingsOut]


class SettingsUpdate(BaseModel):
    keys: dict[str, str] | None = None
    agents: dict[str, Any] | None = None


class InvocationOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | None = None
    ts: str | None = None
    run_id: str | None = None
    symbol: str | None = None
    agent_id: str
    kind: str
    model: str | None = None
    latency_ms: int | None = None
    status: str | None = None
    summary: str | None = None
    payload: dict[str, Any] | list[Any] | None = None


class LogsOut(BaseModel):
    entries: list[InvocationOut]


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    ts: str | None = None
    symbol: str | None = None
    action: str | None = None
    verdict: str | None = None
    status: str | None = None
    notional: float | None = None
    order_id: str | None = None
    reasons: list[str] | None = None


class AuditOut(BaseModel):
    entries: list[AuditEntryOut]


class ControlOut(BaseModel):
    armed: bool
    kill: bool
    execute_enabled_default: bool


class StreamEvent(BaseModel):
    """SSE node/react payload. Extra fields (thought/tool/output) stay."""

    model_config = ConfigDict(extra="allow")

    kind: str
    node: str | None = None
    status: str | None = None
    ts: float | None = None


# Rust/jiter codecs for Redis + SSE (not stdlib json).
any_adapter: TypeAdapter[Any] = TypeAdapter(Any)
stream_adapter: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)
bars_adapter: TypeAdapter[BarsOut] = TypeAdapter(BarsOut)


def dump_json_bytes(value: Any) -> bytes:
    return any_adapter.dump_json(value)


def load_json_bytes(raw: bytes | str) -> Any:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return any_adapter.validate_json(raw)


def dump_stream_event(payload: dict) -> str:
    event = stream_adapter.validate_python(payload)
    return event.model_dump_json()
