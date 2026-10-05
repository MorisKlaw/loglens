"""检测器单元测试：统计检验、控制图、新模板、规则。"""

import unittest
from datetime import datetime, timedelta

import numpy as np

from loglens.detectors.base import DetectConfig, DetectContext
from loglens.detectors.ewma import EwmaDetector, ewma_flags
from loglens.detectors.novelty import NoveltyDetector
from loglens.detectors.poisson import PoissonDetector, benjamini_hochberg, poisson_sf
from loglens.detectors.robust_z import RobustZDetector
from loglens.detectors.rules import RuleDetector
from loglens.features import build_features
from loglens.parsers import parse_lines
from loglens.templates import TemplateMiner

BASE_TS = datetime(2008, 11, 9, 20, 0, 0)


def make_records(counts, template_name="PacketResponder 1 for block blk_1 terminating", level="INFO"):
    """按给定的每分钟条数造日志：counts[i] 表示第 i 分钟有多少条。"""
    rows = []
    lineno = 1
    for minute, n in enumerate(counts):
        ts = BASE_TS + timedelta(minutes=minute)
        for _ in range(int(n)):
            rows.append(
                "%s %s %d %s dfs.DataNode$PacketResponder: %s"
                % (
                    ts.strftime("%y%m%d"),
                    ts.strftime("%H%M%S"),
                    lineno,
                    level,
                    template_name,
                )
            )
            lineno += 1
    return parse_lines(rows, fmt="hdfs").records


class TestPoissonMath(unittest.TestCase):
    def test_sf_known_values(self):
        self.assertAlmostEqual(poisson_sf(0, 1.0), 1.0, places=6)
        self.assertAlmostEqual(poisson_sf(1, 1.0), 1 - np.exp(-1), places=6)
        self.assertAlmostEqual(poisson_sf(3, 1.0), 0.080301, places=5)
        self.assertAlmostEqual(poisson_sf(10, 1.0), 1.1142547828857374e-07, delta=1e-13)

    def test_sf_is_monotone(self):
        values = [poisson_sf(k, 2.0) for k in range(0, 12)]
        self.assertTrue(all(values[i] >= values[i + 1] for i in range(len(values) - 1)))

    def test_benjamini_hochberg(self):
        p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
        rejected = benjamini_hochberg(p, q=0.05)
        self.assertEqual(sum(rejected), 2)
        self.assertTrue(rejected[0] and rejected[1])


class TestEwma(unittest.TestCase):
    def test_flags_volume_spike(self):
        series = [10] * 20 + [80] + [10] * 5
        flags = ewma_flags(series, lam=0.25, L=4.0, min_value=5)
        self.assertTrue(any(idx == 20 and direction == "up" for idx, _, _, direction in flags))

    def test_no_flags_on_flat_series(self):
        self.assertEqual(ewma_flags([10] * 30, lam=0.25, L=4.0, min_value=5), [])


class TestStatisticalDetectors(unittest.TestCase):
    def _context(self, counts, config=None, template_name="PacketResponder 1 for block blk_1 terminating"):
        records = make_records(counts, template_name=template_name)
        miner = TemplateMiner()
        miner.mine(records)
        features = build_features(records, bucket_seconds=60)
        return DetectContext(features=features, templates=miner.templates, config=config or DetectConfig())

    def test_robust_z_finds_burst(self):
        counts = [2] * 30
        counts[15] = 40
        ctx = self._context(counts)
        anomalies = RobustZDetector().detect(ctx)
        self.assertTrue(anomalies)
        self.assertEqual(anomalies[0].bucket, ctx.features.buckets[15])
        self.assertGreaterEqual(anomalies[0].observed, 40)

    def test_robust_z_no_false_positive_on_flat_data(self):
        ctx = self._context([5] * 40)
        self.assertEqual(RobustZDetector().detect(ctx), [])

    def test_poisson_finds_burst_with_fdr(self):
        counts = [1] * 40
        counts[20] = 30
        ctx = self._context(counts)
        anomalies = PoissonDetector().detect(ctx)
        self.assertTrue(anomalies)
        self.assertLess(anomalies[0].p_value, 1e-6)

    def test_ewma_detector_on_volume(self):
        counts = [5] * 25 + [60] + [5] * 5
        ctx = self._context(counts)
        anomalies = EwmaDetector().detect(ctx)
        self.assertTrue(any(a.kind == "ewma-volume-up" for a in anomalies))

    def test_novelty_flags_late_new_template(self):
        records = make_records([3] * 40)
        late_ts = BASE_TS + timedelta(minutes=35)
        records.append(
            parse_lines(
                [
                    "%s %s 999 ERROR dfs.DataNode$DataXceiver: java.io.IOException: Premature EOF from inputStream"
                    % (late_ts.strftime("%y%m%d"), late_ts.strftime("%H%M%S"))
                ],
                fmt="hdfs",
            ).records[0]
        )
        miner = TemplateMiner()
        miner.mine(records)
        ctx = DetectContext(
            features=build_features(records, bucket_seconds=60), templates=miner.templates
        )
        anomalies = NoveltyDetector().detect(ctx)
        self.assertTrue(anomalies)
        self.assertEqual(anomalies[0].kind, "new-template")
        self.assertEqual(anomalies[0].severity, "high")


class TestRuleDetector(unittest.TestCase):
    def test_auth_failure_burst(self):
        lines = []
        ts = BASE_TS
        for i in range(5):
            lines.append(
                "Jun 14 15:16:0%d combo sshd(pam_unix)[19939]: authentication failure; logname= uid=0 rhost=218.188.2.4"
                % i
            )
        records = parse_lines(lines, fmt="linux").records
        miner = TemplateMiner()
        miner.mine(records)
        ctx = DetectContext(
            features=build_features(records, bucket_seconds=60), templates=miner.templates
        )
        anomalies = RuleDetector().detect(ctx)
        kinds = {a.kind for a in anomalies}
        self.assertIn("rule:auth-failure", kinds)

    def test_level_rule_uses_error_rows(self):
        records = make_records([1] * 10, level="ERROR")
        miner = TemplateMiner()
        miner.mine(records)
        ctx = DetectContext(
            features=build_features(records, bucket_seconds=60), templates=miner.templates
        )
        anomalies = RuleDetector().detect(ctx)
        self.assertTrue(any(a.kind == "rule:error-level" for a in anomalies))

    def test_no_rules_hit_on_benign_logs(self):
        records = make_records([2] * 20)
        miner = TemplateMiner()
        miner.mine(records)
        ctx = DetectContext(
            features=build_features(records, bucket_seconds=60), templates=miner.templates
        )
        self.assertEqual(RuleDetector().detect(ctx), [])


if __name__ == "__main__":
    unittest.main()
