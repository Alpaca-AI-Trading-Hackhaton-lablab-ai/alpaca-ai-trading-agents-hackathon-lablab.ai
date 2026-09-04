"""P2: unified execute, MCP via dispatch, critic bias, offline backtest."""

import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.decision_agent import score_setup
from services import critic
from services.backtest import run_backtest
from services.mcp_client import place_order

ROOT = Path(__file__).resolve().parents[1]


class ExecuteUnifyTest(unittest.TestCase):
    def test_bracket_execute_is_alias(self):
        text = (ROOT / "backend.py").read_text()
        start = text.index('@app.post("/bracket/execute")')
        body = text[start : start + 400]
        self.assertIn("_execute_plan(", body)
        self.assertNotIn("evaluate_gate(", body)

    def test_execute_seeds_plan_and_dispatches(self):
        text = (ROOT / "backend.py").read_text()
        start = text.index('@app.post("/execute")')
        end = text.index('@app.get("/control")')
        body = text[start:end]
        self.assertIn("seed_plan(", body)
        self.assertIn("dispatch(", body)
        self.assertIn("plan=plan", body)
        self.assertNotIn("evaluate_gate(", body)
        self.assertNotIn("execute_trade(", body)


class McpDispatchTest(unittest.TestCase):
    def test_place_order_dispatches_notional(self):
        with patch(
            "agents.execution_agent.dispatch",
            return_value={"status": "DRY_RUN"},
        ) as dispatched:
            with patch("services.alpaca_service.get_account_info", return_value={}):
                with patch(
                    "services.alpaca_service.get_positions",
                    return_value={"positions": []},
                ):
                    with patch(
                        "services.alpaca_service.get_open_orders", return_value=[]
                    ):
                        with patch(
                            "services.alpaca_service.get_market_clock",
                            return_value={"is_open": True},
                        ):
                            out = place_order("SPY", "buy", qty=5, last_price=100)
        self.assertEqual(out["status"], "DRY_RUN")
        decision = dispatched.call_args.kwargs["decision"]
        self.assertEqual(decision["position_size"], 500)
        self.assertEqual(decision["action"], "BUY")
        self.assertEqual(decision["symbol"], "SPY")

    def test_explicit_notional(self):
        with patch(
            "agents.execution_agent.dispatch",
            return_value={"status": "DRY_RUN"},
        ) as dispatched:
            with patch("services.alpaca_service.get_account_info", return_value={}):
                with patch(
                    "services.alpaca_service.get_positions",
                    return_value={"positions": []},
                ):
                    with patch(
                        "services.alpaca_service.get_open_orders", return_value=[]
                    ):
                        with patch(
                            "services.alpaca_service.get_market_clock",
                            return_value={"is_open": True},
                        ):
                            place_order("QQQ", "sell", notional=250)
        decision = dispatched.call_args.kwargs["decision"]
        self.assertEqual(decision["position_size"], 250)
        self.assertEqual(decision["action"], "SELL")

    def test_invalid_qty_no_dispatch(self):
        with patch("agents.execution_agent.dispatch") as dispatched:
            out = place_order("SPY", "buy", qty=0, last_price=100)
        dispatched.assert_not_called()
        self.assertEqual(out["status"], "NO_TRADE")

    def test_no_price_no_dispatch(self):
        with patch("agents.execution_agent.dispatch") as dispatched:
            with patch(
                "services.mcp_client._last_price", return_value=None
            ):
                out = place_order("SPY", "buy", qty=2)
        dispatched.assert_not_called()
        self.assertEqual(out["status"], "NO_TRADE")

    def test_place_order_source_has_no_mcp_server(self):
        src = inspect.getsource(place_order)
        self.assertIn("dispatch(", src)
        self.assertNotIn("call_tool", src)
        self.assertNotIn("stdio_client", src)
        self.assertNotIn("place_stock_order", src)


class CriticTest(unittest.TestCase):
    def test_no_rows_is_zero(self):
        with patch("services.logs.query_logs", return_value=[]):
            self.assertEqual(critic.score_bias("SPY"), 0.0)
        self.assertEqual(critic.score_bias(""), 0.0)
        self.assertEqual(critic.score_bias(None), 0.0)

    def test_wins_bias_capped(self):
        rows = [
            {"payload": {"pnl": 10}},
            {"payload": {"pnl": 2}},
            {"payload": {"pnl": -1}},
        ]
        with patch("services.logs.query_logs", return_value=rows):
            bias = critic.score_bias("SPY")
        self.assertGreater(bias, 0)
        self.assertLessEqual(bias, critic.BIAS_CAP)

    def test_all_losses_is_minus_one(self):
        rows = [{"payload": {"pnl": -5}}, {"payload": {"pnl": -1}}]
        with patch("services.logs.query_logs", return_value=rows):
            self.assertEqual(critic.score_bias("AAPL"), -1.0)

    def test_db_error_is_zero(self):
        with patch("services.logs.query_logs", side_effect=RuntimeError("down")):
            self.assertEqual(critic.score_bias("SPY"), 0.0)

    def test_score_setup_applies_bias(self):
        ms = {
            "symbol": "SPY",
            "technical_signal": "BUY",
            "rsi_signal": "NEUTRAL",
            "sentiment": "NEUTRAL",
        }
        with patch("services.critic.score_bias", return_value=1.0):
            scores = score_setup(ms)
        self.assertEqual(scores["buy"], 3.0)
        self.assertEqual(scores["sell"], 0.0)

    def test_score_setup_empty_stays_zero(self):
        with patch("services.critic.score_bias", return_value=0.0):
            self.assertEqual(score_setup({}), {"buy": 0.0, "sell": 0.0})


class BacktestTest(unittest.TestCase):
    def test_fixed_series_no_network(self):
        # Rising then falling — enough for compact BUY/SELL + qty math.
        bars = [{"close": 90 + i} for i in range(30)]
        bars += [{"close": 120 - i} for i in range(20)]
        with patch("services.alpaca_service.get_spy_bars") as net:
            out = run_backtest(bars, symbol="SPY", equity=100000)
        net.assert_not_called()
        self.assertEqual(out["symbol"], "SPY")
        self.assertIn("equity", out)
        self.assertIn("trades", out)
        self.assertIn("verdicts", out)
        self.assertEqual(len(out["verdicts"]), 50)
        self.assertTrue(any(v["verdict"] for v in out["verdicts"]))

    def test_source_never_submits(self):
        src = inspect.getsource(run_backtest)
        self.assertNotIn("submit_bracket", src)
        self.assertNotIn("submit_market", src)
        self.assertNotIn("submit_armed", src)
        self.assertNotIn("dispatch(", src)
        self.assertIn("evaluate_gate(", src)
        self.assertIn("seed_plan(", src)
        self.assertIn("score_setup(", src)
        self.assertIn("calculate_risk(", src)


if __name__ == "__main__":
    unittest.main()
