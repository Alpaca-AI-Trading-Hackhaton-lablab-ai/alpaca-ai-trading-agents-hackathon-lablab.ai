"""Central config + runtime control state for the execution edge.

Single source of truth for the hardening flags. Env vars seed the defaults;
the arm/kill switches are also mutable at runtime (flipped from the UI via
/control) so a demo can arm execution without editing the environment.
Everything here is paper-only — arming never enables live trading.
"""

import os


def _flag(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _pct(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


# Env-seeded defaults.
EXECUTE_ENABLED_DEFAULT = _flag("EXECUTE_ENABLED", False)
KILL_SWITCH_DEFAULT = _flag("KILL_SWITCH", False)
MAX_SYMBOL_EXPOSURE_PCT = _pct("MAX_SYMBOL_EXPOSURE_PCT", 0.10)
MAX_TOTAL_EXPOSURE_PCT = _pct("MAX_TOTAL_EXPOSURE_PCT", 0.30)

# ReAct (deep research / decision reasoning) — proposal side only, opt-in.
DEEP_RESEARCH_DEFAULT = _flag("DEEP_RESEARCH_DEFAULT", False)
RESEARCH_MAX_TURNS = _int("RESEARCH_MAX_TURNS", 3)
DECISION_MAX_TURNS = _int("DECISION_MAX_TURNS", 3)
REACT_TOOL_TIMEOUT_S = _int("REACT_TOOL_TIMEOUT_S", 8)

# Groq Free chat models (consideraciones-groq.md, 1 Sep 2026).
# Limits are per model (~200K TPD); splitting roles across IDs stretches the budget.
GROQ_DEFAULT = "openai/gpt-oss-120b"
GROQ_DEFAULT_SENTIMENT = "openai/gpt-oss-20b"
GROQ_DEFAULT_DECISION = "openai/gpt-oss-120b"

GROQ_ALLOWLIST = (
    {
        "id": "openai/gpt-oss-120b",
        "label": "GPT-OSS 120B",
        "role_hint": "decision",
    },
    {
        "id": "openai/gpt-oss-20b",
        "label": "GPT-OSS 20B",
        "role_hint": "sentiment",
    },
    {
        "id": "qwen/qwen3.6-27b",
        "label": "Qwen 3.6 27B",
        "role_hint": None,
    },
    {
        "id": "qwen/qwen3.8-27b",
        "label": "Qwen 3.8 27B",
        "role_hint": "decision",
    },
)
GROQ_ALLOWLIST_IDS = {item["id"] for item in GROQ_ALLOWLIST}

GROQ_MODEL = os.getenv("GROQ_MODEL", GROQ_DEFAULT).strip() or GROQ_DEFAULT
GROQ_MODEL_SENTIMENT = (
    os.getenv("GROQ_MODEL_SENTIMENT", GROQ_DEFAULT_SENTIMENT).strip()
    or GROQ_DEFAULT_SENTIMENT
)
GROQ_MODEL_DECISION = (
    os.getenv("GROQ_MODEL_DECISION", GROQ_DEFAULT_DECISION).strip()
    or GROQ_DEFAULT_DECISION
)


class UnknownModelError(ValueError):
    """Raised when a request asks for a model outside the Free allowlist."""


def _coerce_model(value, fallback):
    if value in GROQ_ALLOWLIST_IDS:
        return value
    if fallback in GROQ_ALLOWLIST_IDS:
        return fallback
    return GROQ_DEFAULT


def _require_model(value, role):
    if value in GROQ_ALLOWLIST_IDS:
        return value
    raise UnknownModelError(f"unknown {role} model: {value}")


def resolve_models(sentiment_model=None, decision_model=None):
    """Env defaults, then optional per-request overrides. Invalid *env* IDs
    coerce to the role default; invalid *request* IDs raise UnknownModelError
    so the API can return 400 instead of calling Groq with a garbage id."""
    sent_default = _coerce_model(GROQ_MODEL_SENTIMENT, GROQ_DEFAULT_SENTIMENT)
    dec_default = _coerce_model(GROQ_MODEL_DECISION, GROQ_DEFAULT_DECISION)
    if sentiment_model:
        sent_default = _require_model(sentiment_model.strip(), "sentiment")
    if decision_model:
        dec_default = _require_model(decision_model.strip(), "decision")
    return {"sentiment": sent_default, "decision": dec_default}


def models_catalog():
    return {"allowlist": list(GROQ_ALLOWLIST), "defaults": resolve_models()}


def deep_research_default():
    return DEEP_RESEARCH_DEFAULT

# Runtime control state (mutable, in-process). Seeded from env at import time.
_state = {
    "armed": EXECUTE_ENABLED_DEFAULT,
    "kill": KILL_SWITCH_DEFAULT,
}


def is_armed():
    """True when an ALLOW verdict should actually submit a paper order."""
    return bool(_state["armed"])


def set_armed(value):
    _state["armed"] = bool(value)
    return _state["armed"]


def is_kill():
    """True when the kill switch is engaged (the gate blocks everything)."""
    return bool(_state["kill"])


def set_kill(value):
    _state["kill"] = bool(value)
    return _state["kill"]


def control_state():
    return {
        "armed": is_armed(),
        "kill": is_kill(),
        "execute_enabled_default": EXECUTE_ENABLED_DEFAULT,
    }
