"""Account pipeline node. Alpaca paper account read; no LLM.

Agent wrapper at the bottom. Broker reads live in services.alpaca_service.
"""

from agents.base import Agent
from services.alpaca_service import get_account_info


class AccountAgent(Agent):
    node = "account"

    def run(self, ctx):
        return get_account_info()

    def message(self, out):
        return self._err(out) or f"{out.get('mode')} · {out.get('status')}"
