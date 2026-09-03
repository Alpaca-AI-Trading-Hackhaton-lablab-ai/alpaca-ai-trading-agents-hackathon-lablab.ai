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
        self.assertEqual(len(written), 4)
        self.assertEqual(len(logs.query_logs(symbol="SPY")), 4)

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
        self.assertEqual(commits, [2])
        self.assertTrue(any(ev.get("node") == "__done__" for ev in events))
        self.assertEqual(len(logs.query_logs(symbol="AAPL")), 2)

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
        run_row = next(r for r in rows if r["kind"] == "run")
        self.assertEqual(run_row["symbol"], "AAPL")
        self.assertNotIn("candles", run_row["payload"] or {})
        self.assertNotIn("api_key", run_row["payload"] or {})

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

    def test_agent_history_isolated(self):
        logs.record(
            run_id="iso",
            symbol="SPY",
            agent_id="sentiment",
            kind="run",
            status="done",
            summary="BULLISH",
            payload={"dump": "SENTIMENT_ONLY"},
        )
        logs.record(
            run_id="iso",
            symbol="SPY",
            agent_id="decision",
            kind="run",
            status="done",
            summary="BUY",
            payload={"dump": "DECISION_DUMP_XYZ"},
        )
        logs.flush_run("iso")
        sent = logs.history_for_agent("SPY", "sentiment")
        blob = str(sent)
        self.assertNotIn("DECISION_DUMP_XYZ", blob)
        self.assertNotIn("BUY", blob)
        text = logs.history_text("SPY", agent_id="sentiment")
        self.assertIn("BULLISH", text)
        self.assertNotIn("BUY", text)

    def test_older_runs_summarized(self):
        for i in range(3):
            rid = f"run-{i}"
            logs.record(
                run_id=rid,
                symbol="SPY",
                agent_id="decision",
                kind="run",
                status="done",
                summary=f"action-{i}",
                payload={"full_dump": f"FULL_RUN_{i}_PAYLOAD"},
            )
            logs.flush_run(rid)
        rows = logs.history_for_agent("SPY", "decision")
        blob = str(rows)
        self.assertNotIn("FULL_RUN_0_PAYLOAD", blob)
        self.assertIn("FULL_RUN_2_PAYLOAD", blob)
        self.assertIn("FULL_RUN_1_PAYLOAD", blob)
        summaries = [r for r in rows if r["kind"] == "run_summary"]
        self.assertTrue(any("action-0" in (s.get("summary") or "") for s in summaries))
        text = logs.history_text("SPY", agent_id="decision")
        self.assertNotIn("FULL_RUN_0_PAYLOAD", text)


if __name__ == "__main__":
    unittest.main()
