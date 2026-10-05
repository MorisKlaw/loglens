"""产物契约测试：行号证据、BH-FDR 家族大小、报告复现命令。

这些用例保护的是独立复核提出并被修复的三处「证据链 / 可复现性」问题，
避免以后改代码时悄悄退化。
"""

import re
import shutil
import unittest
import uuid
from pathlib import Path

from loglens.detectors.base import Anomaly, DetectConfig, DetectContext
from loglens.detectors.poisson import PoissonDetector
from loglens.features import build_features
from loglens.parsers import parse_lines
from loglens.pipeline import RunConfig, _relpath, analyze_records, run
from loglens.templates import TemplateMiner

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TMP_ROOT = ROOT / "build_tmp"

EVIDENCE_RE = re.compile(r"^L\d+ \| ")


def _tempdir() -> str:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    p = TMP_ROOT / ("contract_" + uuid.uuid4().hex[:8])
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def _burst_records():
    """造一段「前 30 分钟正常、之后某分钟里同类事件暴增」的日志。"""
    rows = []
    for minute in range(30):
        rows.append(
            "081109 %02d%02d00 %d INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_%d terminating"
            % (20 + minute // 60, minute % 60, 100 + minute, minute)
        )
    for i in range(40):
        rows.append(
            "081109 202500 %d INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_%d terminating"
            % (500 + i, 1000 + i)
        )
    return parse_lines(rows, fmt="hdfs").records


class TestEvidenceTraceability(unittest.TestCase):
    def test_anomaly_evidence_carries_line_numbers(self):
        records = _burst_records()
        _, _, anomalies, _, _ = analyze_records(records, detect=DetectConfig(), bucket_seconds=60)
        self.assertTrue(anomalies)
        with_evidence = [a for a in anomalies if a.evidence]
        self.assertTrue(with_evidence, "异常判定应当带原始日志证据")
        for a in with_evidence:
            for cell in str(a.to_row()["evidence"]).split(" || "):
                self.assertRegex(cell, EVIDENCE_RE, msg="CSV 证据缺少 L 行号前缀: " + cell[:60])

    def test_incident_evidence_carries_line_numbers(self):
        records = _burst_records()
        _, _, _, _, incidents = analyze_records(records, detect=DetectConfig(), bucket_seconds=60)
        self.assertTrue(incidents)
        for inc in incidents:
            if not inc.evidence:
                continue
            for cell in str(inc.to_row()["evidence"]).split(" || "):
                self.assertRegex(cell, EVIDENCE_RE)


class TestPoissonFamilySize(unittest.TestCase):
    def test_detail_reports_family_size(self):
        records = _burst_records()
        miner = TemplateMiner()
        miner.mine(records)
        ctx = DetectContext(
            features=build_features(records, bucket_seconds=60),
            templates=miner.templates,
            config=DetectConfig(),
        )
        anomalies = PoissonDetector().detect(ctx)
        self.assertTrue(anomalies)
        for a in anomalies:
            self.assertIn("次检验中经 BH-FDR", a.detail)
        # 家族大小必须 >= 实际输出的判定数，且是正整数
        family = int(re.search(r"在 (\d+) 次检验中", anomalies[0].detail).group(1))
        self.assertGreaterEqual(family, len(anomalies))


class TestReproCommandContract(unittest.TestCase):
    def test_relpath_stays_relative_inside_repo(self):
        self.assertEqual(_relpath("data/HDFS_2k.log"), "data/HDFS_2k.log")
        outside = _relpath(str(Path("C:/somewhere/else/x.log")))
        self.assertNotIn("\\", outside)

    @unittest.skipUnless((DATA / "HDFS_2k.log").exists(), "缺少 data/HDFS_2k.log")
    def test_report_repro_command_matches_actual_paths(self):
        out = _tempdir()
        rel_out = _relpath(out)
        try:
            result = run(
                RunConfig(
                    input_path="data/HDFS_2k.log",
                    out_dir=out,
                    fmt="auto",
                    png=False,
                    write_parsed=False,
                )
            )
            report = Path(result.artifacts["report"]).read_text(encoding="utf-8")
            self.assertIn("python -m loglens run -i data/HDFS_2k.log -o " + rel_out, report)
            # 结论速览要同时给出 high / medium / low 三档
            summary_line = [ln for ln in report.splitlines() if ln.startswith("- **异常**")][0]
            for token in ("high", "medium", "low"):
                self.assertIn(token, summary_line)
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
