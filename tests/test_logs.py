"""Invocation logs + compact history for agents."""

import unittest
from unittest.mock import patch

from services import db, logs


class LogsTest(unittest.TestCase):
    def setUp(self):
        db.connect("sqlite:///:memory:")
        logs._buffers.clear()

    def tearDown(self):
        db.close()

    def test_run_id_buffers_until_flush(self):
        logs.record(
            run_id="run-batch",
            symbol="SPY",
            agent_id="news",
            kind="run",
            status="done",
            summary="2 articles",
        )
        logs.record(
            run_id="run-batch",
            symbol="SPY",
            agent_id="sentiment",
            kind="run",
            status="done",
            summary="NEUTRAL",
        )
        self.assertEqual(logs.query_logs(symbol="SPY"), [])
        written = logs.flush_run("run-batch")
        self.assertEqual(len(written), 2)
        self.assertEqual(len(logs.query_logs(symbol="SPY")), 2)

    def test_pipeline_one_log_commit(self):
        """One pipeline run buffers per node and commits once at flush."""

        class Stub:
            node = "news"

            def run(self, ctx):
                return {"n": 1}

            def message(self, out):
                return "ok"

        commits = []
        real = logs._commit_rows

        def counting(items):
            commits.append(len(items))
            return real(items)

        import backend

        with patch.object(backend, "build_pipeline", return_value=[Stub()]):
            with patch.object(logs, "_commit_rows", side_effect=counting):
                events = list(backend.run_pipeline(symbol="AAPL"))
        self.assertEqual(commits, [1])
        self.assertTrue(any(ev.get("node") == "__done__" for ev in events))
        self.assertEqual(len(logs.query_logs(symbol="AAPL")), 1)

    def test_record_and_query(self):
        logs.record(
            run_id="run-1",
            symbol="aapl",
            agent_id="decision",
            kind="run",
            status="done",
            summary="HOLD · mixed signals",
            payload={"action": "HOLD", "candles": [{"open": 1}], "api_key": "nope"},
        )
        logs.flush_run("run-1")
        rows = logs.query_logs(symbol="AAPL", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertNotIn("candles", rows[0]["payload"] or {})
        self.assertNotIn("api_key", rows[0]["payload"] or {})

    def test_history_text_compact(self):
        logs.record(
            symbol="SPY",
            agent_id="sentiment",
            kind="run",
            status="done",
            summary="BULLISH · 70%",
        )
        logs.record(
            symbol="SPY",
            agent_id="decision",
            kind="run",
            status="done",
            summary="BUY",
        )
        text = logs.history_text("SPY")
        self.assertIn("BULLISH", text)
        self.assertIn("BUY", text)
        self.assertIn("Recent invocations", text)
        recent = logs.recent_for_agents("SPY", limit=10)
        self.assertEqual(len(recent), 2)

    def test_compact_drops_ohlcv(self):
        packed = logs.compact_payload(
            {"candles": [1, 2, 3], "rsi": 55, "secret_key": "x"}
        )
        self.assertNotIn("candles", packed)
        self.assertNotIn("secret_key", packed)
        self.assertEqual(packed.get("rsi"), 55)


if __name__ == "__main__":
    unittest.main()
