"""publisher.py のユニットテスト"""

import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("WP_BASE_URL", "https://example.com")
os.environ.setdefault("WP_USERNAME", "test-user")
os.environ.setdefault("WP_APP_PASSWORD", "test-pass")

from src.publisher import _generate_slug


class TestGenerateSlug:
    def test_japanese_title(self):
        slug = _generate_slug("AIが変える未来のテクノロジー")
        assert slug  # 空でないこと
        assert " " not in slug  # スペースがないこと
        assert all(c.isalnum() or c == "-" for c in slug)

    def test_english_title(self):
        slug = _generate_slug("The Future of AI Technology")
        assert slug == "the-future-of-ai-technology"

    def test_max_length(self):
        long_title = "A" * 200
        slug = _generate_slug(long_title)
        assert len(slug) <= 80

    def test_special_chars_removed(self):
        slug = _generate_slug("AI: The Next Big Thing! #2024")
        assert ":" not in slug
        assert "!" not in slug
        assert "#" not in slug
