"""Unit tests for the unified model catalog builder."""

from __future__ import annotations

import unittest

from services.model_catalog_service import build_catalog
from utils.helper import CODEX_IMAGE_MODEL, WEB_IMAGE_MODELS, image_upstream_model, split_image_model


def account(**kwargs):
    base = {"access_token": "t", "type": "Plus", "source_type": "web"}
    base.update(kwargs)
    return base


class BuildCatalogTest(unittest.TestCase):
    def test_fallback_without_upstream(self):
        catalog = build_catalog(accounts=[])
        self.assertIn("gpt-5.6", catalog["chat_models"])
        self.assertEqual(catalog["defaults"]["chat_model"], "auto")
        self.assertEqual(catalog["defaults"]["image_model"], "gpt-image-2")
        for model in WEB_IMAGE_MODELS:
            self.assertIn(model, catalog["image_models"])

    def test_upstream_models_used_for_chat(self):
        catalog = build_catalog(upstream_models=["gpt-5.6", "gpt-5.5", "auto"], accounts=[])
        self.assertEqual(catalog["chat_models"][:3], ["gpt-5.6", "gpt-5.5", "auto"])
        self.assertEqual(catalog["source"]["chat"], "upstream")

    def test_config_overrides_win(self):
        catalog = build_catalog(
            settings={"chat_models": ["my-chat"], "image_models": ["my-image"], "image_upscale_enabled": True},
            upstream_models=["x"],
            accounts=[],
        )
        self.assertEqual(catalog["chat_models"], ["my-chat"])
        self.assertEqual(catalog["image_models"], ["my-image"])
        self.assertEqual(catalog["source"], {"chat": "config", "image": "config"})
        self.assertTrue(catalog["capabilities"]["image_upscale"])

    def test_codex_accounts_add_prefixed_models(self):
        catalog = build_catalog(accounts=[account(source_type="codex", type="Plus"), account(source_type="web", type="Plus")])
        self.assertIn(CODEX_IMAGE_MODEL, catalog["image_models"])
        self.assertIn(f"plus-{CODEX_IMAGE_MODEL}", catalog["image_models"])
        self.assertIn(CODEX_IMAGE_MODEL, catalog["capabilities"]["high_resolution_image_models"])

    def test_revision_changes_with_content(self):
        first = build_catalog(upstream_models=["a"], accounts=[])
        second = build_catalog(upstream_models=["b"], accounts=[])
        self.assertNotEqual(first["revision"], second["revision"])

    def test_all_models_union(self):
        catalog = build_catalog(settings={"chat_models": ["c"], "image_models": ["i"]}, accounts=[])
        self.assertEqual(catalog["all_models"], ["c", "i"])


class ImageSlugTest(unittest.TestCase):
    def test_split_supports_2_5_family(self):
        for model in ("gpt-image-2", "gpt-image-2.5", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
            plan, base = split_image_model(model)
            self.assertIsNone(plan)
            self.assertEqual(base, model)

    def test_slug_mapping(self):
        self.assertEqual(image_upstream_model("gpt-image-2"), "gpt-5-3")
        self.assertEqual(image_upstream_model("gpt-image-2.5"), "auto")
        self.assertEqual(image_upstream_model("gpt-image-2.5-flare"), "auto")
        self.assertEqual(image_upstream_model(CODEX_IMAGE_MODEL), CODEX_IMAGE_MODEL)
        self.assertEqual(image_upstream_model("plus-codex-gpt-image-2"), CODEX_IMAGE_MODEL)

    def test_bad_model_falls_back_to_auto(self):
        self.assertEqual(image_upstream_model("nope"), "auto")


if __name__ == "__main__":
    unittest.main()
