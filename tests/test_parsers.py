"""解析层单元测试：四种真实格式 + 自动识别 + 兜底行为。"""

import unittest
from datetime import datetime
from pathlib import Path

from loglens.parsers import detect_format, parse_file, parse_lines, read_text

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

HDFS_LINE = "081109 203615 148 INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_38865049064139660 terminating"
LINUX_LINE = "Jun 14 15:16:01 combo sshd(pam_unix)[19939]: authentication failure; logname= uid=0 euid=0 tty=NODEVssh rhost=218.188.2.4"
APACHE_LINE = "[Sun Dec 04 04:47:44 2005] [notice] workerEnv.init() ok /etc/httpd/conf/workers2.properties"
ZOOKEEPER_LINE = "2015-07-29 17:41:44,747 - INFO  [QuorumPeer[myid=1]/0:0:0:0:0:0:0:0:2181:FastLeaderElection@774] - Notification time out: 3200"


class TestParsers(unittest.TestCase):
    def test_hdfs(self):
        res = parse_lines([HDFS_LINE], fmt="hdfs")
        rec = res.records[0]
        self.assertEqual(res.fmt, "hdfs")
        self.assertEqual(rec.ts, datetime(2008, 11, 9, 20, 36, 15))
        self.assertEqual(rec.level, "INFO")
        self.assertEqual(rec.pid, 148)
        self.assertIn("PacketResponder", rec.source)
        self.assertEqual(rec.extra["component"], "dfs.DataNode")
        self.assertTrue(rec.message.startswith("PacketResponder 1 for block"))
        self.assertEqual(res.parse_rate, 1.0)

    def test_linux_syslog_uses_base_year(self):
        res = parse_lines([LINUX_LINE], fmt="linux", syslog_year=2005)
        rec = res.records[0]
        self.assertEqual(rec.ts.year, 2005)
        self.assertEqual(rec.ts.month, 6)
        self.assertEqual(rec.ts.day, 14)
        self.assertEqual(rec.host, "combo")
        self.assertEqual(rec.pid, 19939)
        self.assertEqual(rec.level, "ERROR")  # 由 authentication failure 推断
        self.assertIn("sshd", rec.source)

    def test_apache(self):
        res = parse_lines([APACHE_LINE], fmt="apache")
        rec = res.records[0]
        self.assertEqual(rec.ts, datetime(2005, 12, 4, 4, 47, 44))
        self.assertEqual(rec.level, "NOTICE")
        self.assertIn("workerEnv.init", rec.message)

    def test_zookeeper(self):
        res = parse_lines([ZOOKEEPER_LINE], fmt="zookeeper")
        rec = res.records[0]
        self.assertEqual(rec.ts, datetime(2015, 7, 29, 17, 41, 44, 747000))
        self.assertEqual(rec.level, "INFO")
        self.assertIn("FastLeaderElection", rec.source)
        self.assertIn("QuorumPeer", rec.extra["thread"])

    def test_auto_detect_each_format(self):
        cases = {
            "hdfs": [HDFS_LINE] * 5,
            "linux": [LINUX_LINE] * 5,
            "apache": [APACHE_LINE] * 5,
            "zookeeper": [ZOOKEEPER_LINE] * 5,
        }
        for expected, lines in cases.items():
            fmt, rates = detect_format(lines)
            self.assertEqual(fmt, expected, msg=str(rates))
            self.assertGreaterEqual(rates[expected], 0.9)

    def test_unknown_format_falls_back_to_generic(self):
        lines = ["some random text without any structure", "another unstructured line"]
        res = parse_lines(lines, fmt="auto")
        self.assertEqual(res.fmt, "generic")
        self.assertEqual(len(res.records), 2)

    def test_unmatched_line_keeps_previous_timestamp(self):
        res = parse_lines([HDFS_LINE, "    at java.lang.Thread.run(Thread.java:745)"], fmt="hdfs")
        self.assertEqual(len(res.records), 2)
        self.assertEqual(res.records[1].ts, res.records[0].ts)
        self.assertTrue(res.records[1].extra.get("ts_forward_filled"))
        self.assertEqual(res.records[1].parser, "fallback")
        self.assertLess(res.parse_rate, 1.0)

    @unittest.skipUnless((DATA / "HDFS_2k.log").exists(), "缺少 data/HDFS_2k.log")
    def test_real_files_parse_fully(self):
        for name, fmt in (
            ("HDFS_2k.log", "hdfs"),
            ("Linux_2k.log", "linux"),
            ("Apache_2k.log", "apache"),
            ("Zookeeper_2k.log", "zookeeper"),
        ):
            path = DATA / name
            if not path.exists():
                continue
            res = parse_file(path, fmt="auto")
            self.assertEqual(res.fmt, fmt, msg=name)
            self.assertEqual(len(res.records), 2000, msg=name)
            self.assertEqual(res.fallback_lines, 0, msg=name)
            self.assertEqual(res.with_ts, 2000, msg=name)

    @unittest.skipUnless((DATA / "HDFS_2k.log").exists(), "缺少 data/HDFS_2k.log")
    def test_read_text_returns_encoding(self):
        text, encoding = read_text(DATA / "HDFS_2k.log")
        self.assertIn("utf", encoding.lower())
        self.assertIn("PacketResponder", text)


if __name__ == "__main__":
    unittest.main()
