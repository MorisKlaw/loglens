"""基准评测单元测试：注入构造与匹配逻辑。"""

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from loglens.bench import InjectedWindow, build_injected_log, evaluate_incidents, retimestamp, set_level
from loglens.parsers import parse_file, parse_lines

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

HDFS_LINE = "081109 203615 148 INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_1 terminating"


@dataclass
class FakeIncident:
    start: datetime
    end: datetime
    score: float = 2.0
    severity: str = "high"
    detectors: tuple = ("robust_z",)
    summary: str = "fake"
    evidence: tuple = ()


class TestInjectionHelpers(unittest.TestCase):
    def test_retimestamp_hdfs(self):
        out = retimestamp(HDFS_LINE, "hdfs", datetime(2008, 11, 10, 3, 4, 5))
        self.assertTrue(out.startswith("081110 030405 "))
        self.assertIn("PacketResponder", out)

    def test_retimestamp_linux_and_apache(self):
        linux = "Jun 14 15:16:01 combo sshd[1]: authentication failure"
        out = retimestamp(linux, "linux", datetime(2005, 7, 2, 1, 2, 3))
        self.assertTrue(out.startswith("Jul 02 01:02:03 "))
        apache = "[Sun Dec 04 04:47:44 2005] [notice] workerEnv.init() ok"
        out2 = retimestamp(apache, "apache", datetime(2005, 12, 5, 6, 7, 8))
        self.assertTrue(out2.startswith("[Mon Dec 05 06:07:08 2005]"))

    def test_set_level_hdfs_and_apache(self):
        self.assertIn(" ERROR ", set_level(HDFS_LINE, "hdfs"))
        apache = "[Sun Dec 04 04:47:44 2005] [notice] child pid 3142 exit"
        self.assertIn("[error]", set_level(apache, "apache"))


class TestEvaluation(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2008, 11, 9, 20, 0, 0)
        self.windows = [
            InjectedWindow("burst", self.t0 + timedelta(minutes=10), self.t0 + timedelta(minutes=20), 30),
            InjectedWindow("novel", self.t0 + timedelta(minutes=40), self.t0 + timedelta(minutes=50), 5),
        ]

    def test_perfect_detection(self):
        incidents = [
            FakeIncident(self.t0 + timedelta(minutes=12), self.t0 + timedelta(minutes=18)),
            FakeIncident(self.t0 + timedelta(minutes=42), self.t0 + timedelta(minutes=48)),
        ]
        m = evaluate_incidents(incidents, self.windows)
        self.assertEqual((m["tp"], m["fp"], m["fn"]), (2, 0, 0))
        self.assertAlmostEqual(m["f1"], 1.0)

    def test_missed_and_false_alarm(self):
        incidents = [FakeIncident(self.t0 + timedelta(minutes=5), self.t0 + timedelta(minutes=6))]
        m = evaluate_incidents(incidents, self.windows)
        self.assertEqual((m["tp"], m["fp"], m["fn"]), (0, 1, 2))
        self.assertEqual(m["precision"], 0.0)
        self.assertEqual(m["recall"], 0.0)

    def test_partial_and_per_kind_recall(self):
        incidents = [FakeIncident(self.t0 + timedelta(minutes=15), self.t0 + timedelta(minutes=25))]
        m = evaluate_incidents(incidents, self.windows)
        self.assertEqual(m["tp"], 1)
        self.assertAlmostEqual(m["per_kind_recall"]["burst"], 1.0)
        self.assertAlmostEqual(m["per_kind_recall"]["novel"], 0.0)


class TestBuildInjectedLog(unittest.TestCase):
    def test_synthetic_injection_adds_lines_and_windows(self):
        rows = []
        for minute in range(120):
            rows.append(
                "081109 %02d%02d00 %d INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_%d terminating"
                % (20 + minute // 60, minute % 60, 100 + minute, minute)
            )
        records = parse_lines(rows, fmt="hdfs").records
        lines, windows = build_injected_log(records, "hdfs", n_windows=3, seed=7)
        self.assertGreater(len(lines), len(records) * 0.9)
        self.assertGreaterEqual(len(windows), 1)
        self.assertTrue(all(w.n_lines > 0 for w in windows))
        kinds = {w.kind for w in windows}
        self.assertTrue(kinds.issubset({"burst", "novel", "escalation"}))

    @unittest.skipUnless((DATA / "HDFS_2k.log").exists(), "缺少 data/HDFS_2k.log")
    def test_injection_on_real_log_is_parseable(self):
        parsed = parse_file(DATA / "HDFS_2k.log", fmt="auto")
        lines, windows = build_injected_log(parsed.records, parsed.fmt, n_windows=4, seed=11)
        from tests.test_pipeline import _tempdir

        with _tempdir() as tmp:
            p = Path(tmp) / "inj.log"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            reparsed = parse_file(p, fmt="hdfs")
        self.assertGreaterEqual(len(windows), 2)
        self.assertGreater(reparsed.parse_rate, 0.95)


if __name__ == "__main__":
    unittest.main()
