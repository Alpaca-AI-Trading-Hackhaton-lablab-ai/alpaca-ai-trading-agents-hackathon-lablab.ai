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
