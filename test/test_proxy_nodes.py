"""Unit tests for bulk proxy node parsing."""

from __future__ import annotations

import unittest

from services.proxy_nodes import parse_node_lines


class ParseNodesTest(unittest.TestCase):
    def test_comma_format(self):
        nodes = parse_node_lines("http://127.0.0.1:7932,us-a,2.5,us\nsocks5h://127.0.0.1:7901,jp")
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0], {"url": "http://127.0.0.1:7932", "label": "us-a", "weight": 2.5, "group": "us", "enabled": True})
        self.assertEqual(nodes[1]["label"], "jp")
        self.assertEqual(nodes[1]["weight"], 1.0)

    def test_space_format_and_comments(self):
        nodes = parse_node_lines("# header\n127.0.0.1:7901 jp\n\nhttp://127.0.0.1:7933")
        self.assertEqual([node["url"] for node in nodes], ["127.0.0.1:7901", "http://127.0.0.1:7933"])

    def test_dedupe_and_invalid(self):
        nodes = parse_node_lines("http://a:1\nhttp://a:1\nnot-a-proxy\nftp://x:2")
        self.assertEqual([node["url"] for node in nodes], ["http://a:1"])

    def test_weight_clamped(self):
        nodes = parse_node_lines("http://a:1,x,-3")
        self.assertEqual(nodes[0]["weight"], 0.0)

    def test_bad_weight_keeps_default(self):
        nodes = parse_node_lines("http://a:1,x,abc")
        self.assertEqual(nodes[0]["weight"], 1.0)


if __name__ == "__main__":
    unittest.main()
