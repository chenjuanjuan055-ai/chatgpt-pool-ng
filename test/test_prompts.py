"""Unit tests for prompt library normalization."""

from __future__ import annotations

import unittest

from services.prompts_service import normalize_item, normalize_items


class PromptTest(unittest.TestCase):
    def test_requires_title_and_content(self):
        self.assertIsNone(normalize_item({"title": "", "content": "x"}))
        self.assertIsNone(normalize_item({"title": "t", "content": " "}))

    def test_tags_deduped(self):
        item = normalize_item({"title": "t", "content": "c", "tags": ["a", "a", " b ", ""]})
        self.assertEqual(item["tags"], ["a", "b"])

    def test_items_dedupe_by_id(self):
        items = normalize_items([
            {"id": "p1", "title": "a", "content": "1"},
            {"id": "p1", "title": "b", "content": "2"},
            {"id": "p2", "title": "c", "content": "3"},
        ])
        self.assertEqual([item["id"] for item in items], ["p1", "p2"])


if __name__ == "__main__":
    unittest.main()
