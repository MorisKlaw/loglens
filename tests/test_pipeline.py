"""流水线端到端测试：产物完整性、报告结构、融合逻辑。"""

import json
import shutil
import unittest
import uuid
from pathlib import Path

from loglens.detectors import DetectConfig, DetectContext, default_detectors, fuse
from loglens.features import build_features
from loglens.parsers import parse_lines
from loglens.pipeline import RunConfig, analyze_records, run
from loglens.templates import TemplateMiner

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 说明：临时目录刻意放在仓库内（而不是系统临时目录），
# 这样在受限沙箱/CI 里也能稳定运行。
TMP_ROOT = ROOT / "build_tmp"


class _TempDir:
    """仓库内临时目录。

    刻意不用 tempfile.TemporaryDirectory / mkdtemp：前者清理时要 chmod，
    后者以 0700 创建目录，两者在受限沙箱（Windows + DSH 文件策略）下都会报
    PermissionError。这里用普通 mkdir 建目录，清理时忽略错误。
    """

    def __init__(self, prefix: str = "loglens_") -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.path = TMP_ROOT / (prefix + uuid.uuid4().hex[:8])
        self.path.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> str:
        return str(self.path)

    def __exit__(self, *exc) -> bool:
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def _tempdir() -> _TempDir:
    return _TempDir()


SYNTHETIC = [
    "081109 203615 148 INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_111 terminating",
    "081109 203700 149 INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: blockMap updated: 10.0.0.1:50010 is added to blk_222 size 67108864",
    "081109 203800 150 ERROR dfs.DataNode$DataXceiver: java.io.IOException: Premature EOF from inputStream",
]


class TestPipelineSynthetic(unittest.TestCase):
    def test_end_to_end_on_synthetic_log(self):
        with _tempdir() as tmp:
            src = Path(tmp) / "sample.log"
            src.write_text("\n".join(SYNTHETIC * 20) + "\n", encoding="utf-8")
            out = Path(tmp) / "out"
            result = run(RunConfig(input_path=str(src), out_dir=str(out), fmt="auto", target_buckets=10))
            self.assertEqual(result.parse.parse_rate, 1.0)
            self.assertGreaterEqual(len(result.templates), 3)
            for name in ("report.md", "parsed.csv", "templates.csv", "anomalies.csv", "summary.json"):
                self.assertTrue((out / name).exists(), msg=name)
            report = (out / "report.md").read_text(encoding="utf-8")
            for section in ("## 0 结论速览", "## 1 数据概览", "## 2 模板挖掘", "## 3 异常检测结果", "## 5 复现命令"):
                self.assertIn(section, report)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["parse"]["total_lines"], 60)
            self.assertIn("detection", summary)

    def test_fuse_creates_incident_for_injected_burst(self):
        rows = []
        for minute in range(30):
            for i in range(1):
                rows.append(
                    "081109 %02d%02d00 %d INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_1 terminating"
                    % (20 + minute // 60, minute % 60, 100 + minute)
                )
        for i in range(30):
            rows.append(
                "081109 202500 %d INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_1 terminating"
                % (200 + i)
            )
        records = parse_lines(rows, fmt="hdfs").records
        templates, features, anomalies, windows, incidents = analyze_records(
            records, detect=DetectConfig(), bucket_seconds=60
        )
        self.assertTrue(anomalies)
        self.assertTrue(incidents)
        self.assertGreaterEqual(incidents[0].score, 1.0)
        self.assertIn("检测器", incidents[0].summary)

    def test_all_detectors_registered_by_default(self):
        detectors = default_detectors(DetectConfig())
        names = sorted(d.name for d in detectors)
        self.assertEqual(names, ["ewma", "novelty", "poisson", "robust_z", "rules"])

    def test_detector_switches(self):
        cfg = DetectConfig(enable_rules=False, enable_ewma=False)
        names = sorted(d.name for d in default_detectors(cfg))
        self.assertEqual(names, ["novelty", "poisson", "robust_z"])


@unittest.skipUnless((DATA / "HDFS_2k.log").exists(), "缺少 data/HDFS_2k.log")
class TestPipelineRealLog(unittest.TestCase):
    def test_full_run_on_hdfs_sample(self):
        with _tempdir() as tmp:
            result = run(
                RunConfig(
                    input_path=str(DATA / "HDFS_2k.log"),
                    out_dir=tmp,
                    fmt="auto",
                    png=False,
                )
            )
            self.assertEqual(result.parse.fmt, "hdfs")
            self.assertEqual(len(result.parse.records), 2000)
            self.assertGreaterEqual(len(result.templates), 10)
            self.assertGreater(len(result.anomalies), 0)
            self.assertGreater(len(result.incidents), 0)
            charts = Path(tmp) / "charts"
            self.assertTrue((charts / "timeline.svg").exists())
            self.assertTrue((Path(tmp) / "report.md").read_text(encoding="utf-8").startswith("# "))


if __name__ == "__main__":
    unittest.main()
