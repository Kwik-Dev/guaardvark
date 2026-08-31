#!/usr/bin/env python3
"""Krea Raw multi-character regression (2026-08-08, Joker batch).

A 343-word single-character costume prompt rendered split-panel spreads and
three-Joker group shots. Three compounding causes, each pinned here:

1. `_detect_subject_count` used substring matching — the plural "men" matched
   inside "element"/"embellishment", so a single-person prompt was classified
   as a multi-person scene and enhancement added group phrasing.
2. Enhancement suffixes pushed the prompt to 548 tokens against the Krea2
   encoder's silent 512-token truncation — the coherence/subject guard phrases
   appended LAST were exactly what fell off.
3. Krea 2 Raw (base checkpoint) reads catalog-style prompts as lookbook
   spreads; its negative prompt had no anti-collage terms.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.offline_image_generator import OfflineImageGenerator  # noqa: E402


class _FakeTokenizerOutput:
    def __init__(self, ids):
        self.input_ids = ids


class _FakeTokenizer:
    """1 word = 1 token — deterministic budget math for tests."""

    def __call__(self, text):
        return _FakeTokenizerOutput(text.split())


class _FakePipeline:
    tokenizer = _FakeTokenizer()


class TestSubjectCountWordBoundaries(unittest.TestCase):

    def setUp(self):
        self.gen = OfflineImageGenerator()

    def test_men_inside_element_is_not_plural(self):
        # The exact trigger from the Joker batch: "element", "embellishment"
        info = self.gen._detect_subject_count(
            "a costume where every element and embellishment serves the entrance"
        )
        self.assertEqual(info["subject_count"], "single")

    def test_real_plurals_still_detected(self):
        for prompt in (
            "two men fighting in an alley",
            "people walking in the street",
            "a group of children playing",
            "a crowd of fans cheering",
            "women at a cafe",
        ):
            info = self.gen._detect_subject_count(prompt)
            self.assertEqual(info["subject_count"], "multiple", prompt)

    def test_and_conjunction_needs_two_distinct_person_words(self):
        # "woman" contains "man" as a substring — must NOT count as two people
        self.assertEqual(
            self.gen._detect_subject_count("a woman singing and dancing")["subject_count"],
            "single",
        )
        self.assertEqual(
            self.gen._detect_subject_count("a man and a woman dancing")["subject_count"],
            "multiple",
        )

    def test_single_person_prompts(self):
        for prompt in (
            "a man in a joker costume walking alone down an alley",
            "portrait of a girl",
            "solo dancer on stage",
        ):
            info = self.gen._detect_subject_count(prompt)
            self.assertEqual(info["subject_count"], "single", prompt)


class TestSingleSubjectReinforcement(unittest.TestCase):

    def setUp(self):
        self.gen = OfflineImageGenerator()

    def test_solo_anchor_added_for_single_person(self):
        enhanced, _neg, det = self.gen.enhance_prompt_for_quality(
            "a man in an elaborate costume walking down an alley",
            style="realistic", auto_enhance=True,
        )
        self.assertTrue(det["subject_count_info"]["is_single_subject"])
        self.assertIn("solo, only one person", enhanced)

    def test_no_solo_anchor_for_groups(self):
        enhanced, _neg, det = self.gen.enhance_prompt_for_quality(
            "two men fighting in an alley", style="realistic", auto_enhance=True,
        )
        self.assertFalse(det["subject_count_info"]["is_single_subject"])
        self.assertNotIn("solo, only one person", enhanced)


class TestLongContextTokenBudget(unittest.TestCase):

    def setUp(self):
        self.gen = OfflineImageGenerator()
        self.gen._pipeline = _FakePipeline()

    def tearDown(self):
        self.gen._pipeline = None

    def test_long_prompt_fits_budget_and_keeps_user_text(self):
        # 480 words + suffixes would overflow 512; user prompt must survive whole
        user_prompt = "a man wearing " + " ".join(f"garment{i}" for i in range(478))
        enhanced, _neg, _det = self.gen.enhance_prompt_for_quality(
            user_prompt, style="realistic", auto_enhance=True, family="krea2",
        )
        self.assertTrue(enhanced.startswith(user_prompt))
        tokens = len(_FakeTokenizer()(enhanced).input_ids)
        self.assertLessEqual(tokens, self.gen._LONG_CONTEXT_TOKEN_LIMIT)

    def test_priority_phrases_survive_over_style_boilerplate(self):
        user_prompt = "a man wearing " + " ".join(f"garment{i}" for i in range(478))
        enhanced, _neg, _det = self.gen.enhance_prompt_for_quality(
            user_prompt, style="realistic", auto_enhance=True, family="krea2",
        )
        suffix = enhanced[len(user_prompt):]
        # priority-0 guards kept; priority-3 style boilerplate dropped first
        self.assertIn("solo", suffix)
        self.assertIn("coherent scene", suffix)
        self.assertNotIn("professional photography", suffix)

    def test_short_prompt_untrimmed(self):
        enhanced_short, _neg, _det = self.gen.enhance_prompt_for_quality(
            "a man in a park", style="realistic", auto_enhance=True, family="krea2",
        )
        # Well under budget: full boilerplate retained, original append order
        self.assertIn("photorealistic", enhanced_short)
        self.assertIn("coherent scene", enhanced_short)

    def test_oversized_user_prompt_passes_through_with_no_suffixes(self):
        user_prompt = " ".join(f"word{i}" for i in range(600))
        enhanced, _neg, _det = self.gen.enhance_prompt_for_quality(
            user_prompt, style="realistic", auto_enhance=True, family="krea2",
        )
        self.assertEqual(enhanced, user_prompt)

    def test_sdxl_family_not_budgeted_here(self):
        user_prompt = "a man wearing " + " ".join(f"garment{i}" for i in range(478))
        enhanced, _neg, _det = self.gen.enhance_prompt_for_quality(
            user_prompt, style="realistic", auto_enhance=True, family="sdxl",
        )
        # sdxl keeps the legacy path (dual-CLIP truncates on its own)
        self.assertIn("photorealistic", enhanced)


class TestKrea2RawAntiCollageNegatives(unittest.TestCase):

    def setUp(self):
        self.gen = OfflineImageGenerator()

    def test_anti_collage_always_added(self):
        detection = self.gen.detect_content_type("two men fighting in an alley")
        neg = self.gen._augment_krea2_raw_negatives("low quality", detection)
        self.assertIn("collage", neg)
        self.assertIn("split screen", neg)
        self.assertIn("magazine layout", neg)
        # group prompt: people-count negatives must NOT appear
        self.assertNotIn("multiple people", neg)

    def test_solo_negatives_only_for_single_person(self):
        detection = self.gen.detect_content_type(
            "a man in a joker costume walking alone down an alley"
        )
        neg = self.gen._augment_krea2_raw_negatives("", detection)
        self.assertIn("multiple people", neg)
        self.assertIn("clones", neg)

    def test_variant_gating(self):
        self.assertEqual(self.gen._krea2_variant("krea2-raw"), "raw")
        self.assertEqual(self.gen._krea2_variant("krea2-turbo"), "turbo")


if __name__ == "__main__":
    unittest.main()
