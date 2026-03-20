"""scraper.py のユニットテスト"""

import os
import tempfile

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from src import database as db
from src.scraper import _get_user_agent, _check_robots_txt


@pytest.fixture(autouse=True)
def _setup_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()
    yield


class TestUserAgent:
    def test_returns_string(self):
        settings = {"scraper": {"user_agents": ["Agent/1.0", "Agent/2.0"]}}
        ua = _get_user_agent(settings)
        assert ua in ["Agent/1.0", "Agent/2.0"]

    def test_default(self):
        ua = _get_user_agent({})
        assert "AINAP" in ua


class TestDeduplication:
    def test_same_url_detected(self):
        url = "https://example.com/test-article"
        db.save_article(url=url, title="Test", source_name="Source")
        assert db.is_url_exists(url) is True

    def test_different_url_not_detected(self):
        db.save_article(url="https://a.com/1", title="A", source_name="S")
        assert db.is_url_exists("https://b.com/2") is False
