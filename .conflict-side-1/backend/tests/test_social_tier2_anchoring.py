#!/usr/bin/env python3
"""Tests for SOCIAL_TIER2_PATTERNS — strict anchoring for Tier 2 social routing."""

import pytest

from backend.services.agent_brain import SOCIAL_TIER2_PATTERNS


class TestSocialTier2Anchoring:
  @pytest.mark.parametrize("message", [
      "hello",
      "Hello!",
      "thanks",
      "thank you",
      "bye",
      "yes",
      "ok",
      "what's up",
      "how are you",
  ])
  def test_pure_social_matches(self, message):
      assert SOCIAL_TIER2_PATTERNS.fullmatch(message.strip())

  @pytest.mark.parametrize("message", [
      "hello can you search the web",
      "thanks for the help with my code",
      "yes please analyze the website",
      "hi there friend",
      "please volume up the analysis",
  ])
  def test_complex_messages_do_not_match(self, message):
      assert SOCIAL_TIER2_PATTERNS.fullmatch(message.strip()) is None
