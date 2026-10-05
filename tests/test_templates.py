"""模板挖掘单元测试：掩码、聚类、通配符合并。"""

import unittest

from loglens.parsers import parse_lines
from loglens.templates import Drain, TemplateMiner, is_wildcard, mask_line

LINES = [
    "081109 203615 148 INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_38865049064139660 terminating",
    "081109 203807 222 INFO dfs.DataNode$PacketResponder: PacketResponder 0 for block blk_-6952295868487656571 terminating",
    "081109 204005 35 INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: blockMap updated: 10.251.73.220:50010 is added to blk_7128370237687728475 size 67108864",
]


class TestMasking(unittest.TestCase):
    def test_masks_block_ip_and_number(self):
        out = mask_line("Receiving block blk_-123 from /10.250.9.207:53270 size 67108864 in 12 ms")
        self.assertIn("<BLK>", out)
        self.assertIn("<IP>", out)
        self.assertIn("<NUM>", out)
        self.assertNotIn("10.250.9.207", out)

    def test_masks_path_and_uuid(self):
        out = mask_line("write /var/log/hadoop/hdfs/datanode.log id 550e8400-e29b-41d4-a716-446655440000 0x1f")
        self.assertIn("<PATH>", out)
        self.assertIn("<UUID>", out)
        self.assertIn("<HEX>", out)

    def test_wildcard_detection(self):
        self.assertTrue(is_wildcard("<*>"))
        self.assertTrue(is_wildcard("<NUM>"))
        self.assertFalse(is_wildcard("INFO"))


class TestDrain(unittest.TestCase):
    def setUp(self):
        self.records = parse_lines(LINES, fmt="hdfs").records
        self.miner = TemplateMiner(depth=4, sim_th=0.4)
        self.drain = self.miner.mine(self.records)

    def test_similar_lines_share_template(self):
        self.assertEqual(self.records[0].template_id, self.records[1].template_id)

    def test_different_lines_get_different_templates(self):
        self.assertNotEqual(self.records[0].template_id, self.records[2].template_id)

    def test_template_is_masked_and_has_wildcards(self):
        tmpl = self.drain.templates[self.records[0].template_id]
        self.assertGreater(tmpl.wildcard_count, 0)
        self.assertIn("<BLK>", tmpl.pattern)
        self.assertNotIn("38865049064139660", tmpl.pattern)

    def test_counts_and_examples(self):
        tmpl = self.drain.templates[self.records[0].template_id]
        self.assertEqual(tmpl.count, 2)
        self.assertEqual(len(tmpl.examples), 2)
        self.assertIsNotNone(tmpl.first_seen)

    def test_online_add_merges_into_existing_template(self):
        before = self.drain.n_templates()
        self.drain.add("PacketResponder 7 for block blk_999 terminating")
        self.assertEqual(self.drain.n_templates(), before)
        tmpl = self.drain.template_of("PacketResponder 7 for block blk_999 terminating")
        self.assertIsNotNone(tmpl)
        self.assertEqual(tmpl.count, 3)

    def test_new_shape_creates_new_template(self):
        before = self.drain.n_templates()
        self.drain.add("FATAL something completely different happened here")
        self.assertEqual(self.drain.n_templates(), before + 1)

    def test_similarity_threshold_rejects_distant_lines(self):
        sim = Drain.similarity(["a", "b", "c", "d"], ["a", "x", "y", "z"])
        self.assertAlmostEqual(sim, 0.25)
        self.assertLess(sim, 0.4)


if __name__ == "__main__":
    unittest.main()
