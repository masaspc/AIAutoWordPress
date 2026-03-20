"""generator.py のユニットテスト"""

import json
import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from src.generator import _build_prompt, _parse_json_response


class TestBuildPrompt:
    def test_variables_substituted(self):
        article = {
            "title": "Test Title",
            "source_name": "TestSource",
            "url": "https://example.com",
            "body": "Body text here",
        }
        prompt = _build_prompt(article)
        assert "Test Title" in prompt
        assert "TestSource" in prompt
        assert "https://example.com" in prompt
        assert "Body text here" in prompt

    def test_empty_body_handled(self):
        article = {
            "title": "T",
            "source_name": "S",
            "url": "https://x.com",
            "body": None,
        }
        prompt = _build_prompt(article)
        assert "https://x.com" in prompt


class TestParseJsonResponse:
    def test_json_block(self):
        text = '```json\n{"title": "テスト", "content": "<p>本文</p>"}\n```'
        result = _parse_json_response(text)
        assert result["title"] == "テスト"
        assert "<p>本文</p>" in result["content"]

    def test_bare_json(self):
        text = '{"title": "テスト", "content": "<p>本文</p>"}'
        result = _parse_json_response(text)
        assert result["title"] == "テスト"

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"title": "T", "content": "C"}\nDone!'
        result = _parse_json_response(text)
        assert result["title"] == "T"

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            _parse_json_response("No JSON here at all")
